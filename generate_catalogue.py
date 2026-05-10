"""
generate_catalogue.py
─────────────────────
Reads data.csv, extracts thumbnails + duration from each video URL using
ffmpeg ASYNCHRONOUSLY (all videos processed in parallel), then writes a
fully self-contained video_catalogue.html with thumbnails embedded as
base64 — zero runtime CORS issues.

Requirements:
  ffmpeg must be on PATH  (brew install ffmpeg  /  apt install ffmpeg)

Usage:
  python3 generate_catalogue.py
  python3 generate_catalogue.py --csv my_videos.csv --out catalogue.html
  python3 generate_catalogue.py --workers 8   # default: 6
"""

import asyncio
import base64
import csv
import json
import os
import sys
import argparse
import time

# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(
        description="Generate a video catalogue HTML from a CSV."
    )
    p.add_argument("--csv", default="data.csv", help="Input CSV file")
    p.add_argument("--out", default="index.html", help="Output HTML file")
    p.add_argument(
        "--workers", type=int, default=20, help="Max parallel ffmpeg jobs (default: 20)"
    )
    p.add_argument(
        "--thumb-time",
        default="5",
        help="Seek offset in seconds for thumbnail frame (default: 5)",
    )
    return p.parse_args()


# ── CSV ───────────────────────────────────────────────────────────────────────


def load_csv(filepath):
    videos = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            videos.append(
                {
                    "title": row["Title"],
                    "url": row["Video"],
                    "added_by": row["Added by"],
                    "category": row["Category"],
                    "comment_count": int(row["Comment count"]),
                    "date_added": row["Date added"],
                    "view_count": int(row["View count"]),
                    "thumb_b64": None,
                    "duration": None,
                }
            )
    return videos


# ── ASYNC FFMPEG ──────────────────────────────────────────────────────────────


async def probe_video(
    url: str, thumb_time: str, sem: asyncio.Semaphore, idx: int, total: int
):
    """
    Run ffprobe (duration) and ffmpeg (thumbnail) concurrently for one video.
    Returns (thumb_b64 | None, duration_str | None)
    """
    async with sem:
        label = url.split("/")[-1] or url
        print(f"  [{idx + 1}/{total}] {label}", flush=True)
        thumb_b64, duration = await asyncio.gather(
            _extract_thumb(url, thumb_time),
            _extract_duration(url),
        )
        parts = []
        parts.append("✓ thumb" if thumb_b64 else "✗ thumb")
        parts.append(f"✓ {duration}" if duration else "✗ duration")
        print(f"         └─ {', '.join(parts)}", flush=True)
        return thumb_b64, duration


async def _run(cmd, timeout=30):
    """Helper: run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout, stderr
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, b"", b"timeout"


async def _extract_thumb(url: str, thumb_time: str) -> str | None:
    """Extract a single JPEG frame via ffmpeg, return as base64 string."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        thumb_time,
        "-i",
        url,
        "-frames:v",
        "1",
        "-vf",
        "scale=480:270:force_original_aspect_ratio=decrease,"
        "pad=480:270:(ow-iw)/2:(oh-ih)/2:black",
        "-q:v",
        "5",
        "-f",
        "image2",
        "pipe:1",
    ]
    rc, stdout, _ = await _run(cmd, timeout=30)
    if rc == 0 and stdout:
        return base64.b64encode(stdout).decode()
    return None


async def _extract_duration(url: str) -> str | None:
    """Use ffprobe to get video duration."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        url,
    ]
    rc, stdout, _ = await _run(cmd, timeout=15)
    if rc == 0 and stdout.strip():
        try:
            secs = float(stdout.decode().strip())
            return _fmt_duration(secs)
        except ValueError:
            pass
    return None


def _fmt_duration(secs: float) -> str:
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── ORCHESTRATOR ──────────────────────────────────────────────────────────────


async def enrich_videos(videos: list, thumb_time: str, max_workers: int):
    sem = asyncio.Semaphore(max_workers)
    total = len(videos)
    t0 = time.perf_counter()

    tasks = [
        probe_video(v["url"], thumb_time, sem, i, total) for i, v in enumerate(videos)
    ]
    results = await asyncio.gather(*tasks)

    for video, (thumb_b64, duration) in zip(videos, results):
        video["thumb_b64"] = thumb_b64
        video["duration"] = duration

    elapsed = time.perf_counter() - t0
    ok_thumb = sum(1 for v in videos if v["thumb_b64"])
    ok_dur = sum(1 for v in videos if v["duration"])
    print(
        f"\n  Finished in {elapsed:.1f}s  |  "
        f"thumbnails {ok_thumb}/{total}  |  durations {ok_dur}/{total}"
    )


# ── HTML GENERATION ───────────────────────────────────────────────────────────


def get_categories(videos):
    return sorted(set(v["category"] for v in videos))


def generate_html(videos: list) -> str:
    # JS payload — strip build-time-only keys
    js_videos = [
        {
            k: v[k]
            for k in (
                "title",
                "url",
                "added_by",
                "category",
                "comment_count",
                "date_added",
                "view_count",
                "duration",
            )
        }
        for v in videos
    ]
    videos_json = json.dumps(js_videos)

    categories = get_categories(videos)
    category_buttons = "\n".join(
        f'<button class="filter-btn" data-cat="{cat}">{cat}</button>'
        for cat in categories
    )

    # Thumbnail map: url → data URI
    thumb_map = {
        v["url"]: (f"data:image/jpeg;base64,{v['thumb_b64']}" if v["thumb_b64"] else "")
        for v in videos
    }
    thumb_map_json = json.dumps(thumb_map)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VideoVault — Catalogue</title>
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:      #0a0a0f; --surface: #111118; --card: #16161f;
      --border:  #2a2a3a; --accent:  #e8ff47; --accent2: #ff5c5c;
      --text:    #e8e8f0; --muted:   #6b6b80; --pill: #1f1f2e;
    }}
    html {{ scroll-behavior: smooth; }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: 'DM Sans', sans-serif; min-height: 100vh; overflow-x: hidden;
    }}
    body::before {{
      content:''; position:fixed; inset:0; pointer-events:none; z-index:0;
      background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    }}

    /* HEADER */
    header {{
      position:sticky; top:0; z-index:100;
      background:rgba(10,10,15,.85); backdrop-filter:blur(18px);
      -webkit-backdrop-filter:blur(18px); border-bottom:1px solid var(--border);
      padding:0 clamp(1rem,5vw,4rem);
      display:flex; align-items:center; justify-content:space-between;
      gap:1rem; height:64px;
    }}
    .logo {{
      font-family:'Bebas Neue',sans-serif; font-size:clamp(1.6rem,4vw,2.2rem);
      letter-spacing:.06em; color:var(--accent);
      text-shadow:0 0 30px rgba(232,255,71,.35); flex-shrink:0;
    }}
    .logo span {{ color:var(--text); }}
    .search-wrap {{ position:relative; flex:1; max-width:420px; }}
    .search-wrap svg {{
      position:absolute; left:14px; top:50%; transform:translateY(-50%);
      color:var(--muted); pointer-events:none;
    }}
    #search {{
      width:100%; background:var(--pill); border:1px solid var(--border);
      border-radius:999px; padding:.55rem 1rem .55rem 2.8rem;
      color:var(--text); font-family:'DM Sans',sans-serif;
      font-size:.88rem; outline:none; transition:border-color .2s;
    }}
    #search:focus {{ border-color:var(--accent); }}
    #search::placeholder {{ color:var(--muted); }}
    .stats-badge {{ font-size:.78rem; color:var(--muted); white-space:nowrap; }}

    /* HERO */
    .hero {{ padding:clamp(3rem,8vw,6rem) clamp(1rem,5vw,4rem) clamp(2rem,5vw,3rem); }}
    .hero-eyebrow {{
      font-size:.75rem; letter-spacing:.2em; text-transform:uppercase;
      color:var(--accent); margin-bottom:.8rem; font-weight:500;
    }}
    .hero h1 {{
      font-family:'Bebas Neue',sans-serif; font-size:clamp(3.5rem,10vw,8rem);
      line-height:.9;
      background:linear-gradient(135deg,#fff 40%,#6b6b80 100%);
      -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
    }}
    .hero-sub {{
      margin-top:1.2rem; color:var(--muted);
      font-size:clamp(.9rem,2vw,1.05rem); max-width:520px;
      line-height:1.6; font-weight:300;
    }}

    /* FILTERS */
    .filters {{
      padding:0 clamp(1rem,5vw,4rem) 2rem;
      display:flex; gap:.5rem; flex-wrap:wrap;
    }}
    .filter-btn {{
      background:var(--pill); border:1px solid var(--border);
      border-radius:999px; color:var(--muted);
      padding:.4rem 1.1rem; font-family:'DM Sans',sans-serif;
      font-size:.82rem; font-weight:500; cursor:pointer;
      transition:all .2s; letter-spacing:.02em;
    }}
    .filter-btn:hover {{ border-color:var(--accent); color:var(--accent); }}
    .filter-btn.active {{
      background:var(--accent); color:#0a0a0f;
      border-color:var(--accent); font-weight:700;
    }}

    /* SORT */
    .sort-bar {{
      padding:0 clamp(1rem,5vw,4rem) 1.5rem;
      display:flex; align-items:center; gap:1rem;
      font-size:.82rem; color:var(--muted);
    }}
    #sort-select {{
      background:var(--pill); border:1px solid var(--border); border-radius:8px;
      color:var(--text); padding:.35rem .8rem;
      font-family:'DM Sans',sans-serif; font-size:.82rem; outline:none; cursor:pointer;
    }}

    /* GRID */
    .grid {{
      padding:0 clamp(1rem,5vw,4rem) 4rem;
      display:grid;
      grid-template-columns:repeat(auto-fill,minmax(min(280px,100%),1fr));
      gap:1.5rem;
    }}

    /* CARD */
    .card {{
      background:var(--card); border:1px solid var(--border);
      border-radius:16px; overflow:hidden; cursor:pointer;
      transition:transform .28s cubic-bezier(.22,.68,0,1.2),
                 box-shadow .28s ease, border-color .2s;
      animation:fadeUp .5s ease both;
    }}
    @keyframes fadeUp {{
      from {{ opacity:0; transform:translateY(20px); }}
      to   {{ opacity:1; transform:translateY(0); }}
    }}
    .card:hover {{
      transform:translateY(-6px) scale(1.01);
      box-shadow:0 20px 60px rgba(0,0,0,.5),0 0 0 1px var(--accent);
      border-color:var(--accent);
    }}
    .card:hover .play-overlay {{ opacity:1; }}
    .card:hover .thumb-img    {{ transform:scale(1.06); }}

    .thumb {{
      position:relative; width:100%; aspect-ratio:16/9;
      background:var(--surface); overflow:hidden;
    }}
    .thumb-img {{
      width:100%; height:100%; object-fit:cover; display:block;
      transition:transform .4s ease;
    }}
    .thumb-placeholder {{
      width:100%; height:100%;
      display:flex; align-items:center; justify-content:center;
      flex-direction:column; gap:.5rem; color:var(--muted); font-size:.75rem;
      background:linear-gradient(135deg,#16161f,#1e1e2e);
    }}
    .thumb-placeholder .icon {{ font-size:2.5rem; opacity:.4; }}
    .duration-badge {{
      position:absolute; bottom:8px; right:8px;
      background:rgba(0,0,0,.82); color:#fff; font-size:.7rem;
      font-weight:600; padding:2px 7px; border-radius:5px;
      letter-spacing:.03em; backdrop-filter:blur(4px);
    }}
    .play-overlay {{
      position:absolute; inset:0; background:rgba(232,255,71,.12);
      display:flex; align-items:center; justify-content:center;
      opacity:0; transition:opacity .2s;
    }}
    .play-icon {{
      width:52px; height:52px; background:var(--accent); border-radius:50%;
      display:flex; align-items:center; justify-content:center;
      box-shadow:0 0 40px rgba(232,255,71,.5);
    }}
    .play-icon svg {{ transform:translateX(2px); }}

    .card-body  {{ padding:1rem 1.1rem 1.2rem; }}
    .card-cat   {{
      display:inline-block; font-size:.67rem; font-weight:700;
      letter-spacing:.12em; text-transform:uppercase;
      color:var(--accent); margin-bottom:.45rem;
    }}
    .card-title {{
      font-size:clamp(.92rem,2vw,1rem); font-weight:500; line-height:1.35;
      margin-bottom:.7rem; color:var(--text);
      display:-webkit-box; -webkit-line-clamp:2;
      -webkit-box-orient:vertical; overflow:hidden;
    }}
    .card-meta {{
      display:flex; align-items:center; justify-content:space-between;
      gap:.5rem; flex-wrap:wrap;
    }}
    .card-author {{
      display:flex; align-items:center; gap:.45rem;
      font-size:.78rem; color:var(--muted);
    }}
    .avatar {{
      width:22px; height:22px; border-radius:50%;
      display:flex; align-items:center; justify-content:center;
      font-size:.65rem; font-weight:700; color:#fff;
      flex-shrink:0; text-transform:uppercase;
    }}
    .card-stats {{ display:flex; gap:.8rem; font-size:.73rem; color:var(--muted); }}
    .card-stats span {{ display:flex; align-items:center; gap:.3rem; }}

    /* MODAL */
    .modal-bg {{
      position:fixed; inset:0; background:rgba(0,0,0,.88); z-index:500;
      display:flex; align-items:center; justify-content:center; padding:1rem;
      opacity:0; pointer-events:none; transition:opacity .25s;
      backdrop-filter:blur(10px);
    }}
    .modal-bg.open {{ opacity:1; pointer-events:all; }}
    .modal {{
      background:var(--card); border:1px solid var(--border);
      border-radius:20px; width:100%; max-width:760px; overflow:hidden;
      position:relative;
      transform:scale(.94) translateY(20px);
      transition:transform .3s cubic-bezier(.22,.68,0,1.2);
    }}
    .modal-bg.open .modal {{ transform:scale(1) translateY(0); }}
    .modal-video {{ width:100%; aspect-ratio:16/9; background:#000; }}
    .modal-video video {{ width:100%; height:100%; display:block; }}
    .modal-info {{ padding:1.4rem 1.6rem; }}
    .modal-title {{
      font-family:'Bebas Neue',sans-serif;
      font-size:clamp(1.4rem,4vw,2rem); letter-spacing:.04em; margin-bottom:.6rem;
    }}
    .modal-row {{
      display:flex; gap:1.5rem; flex-wrap:wrap;
      font-size:.82rem; color:var(--muted); margin-bottom:.8rem;
    }}
    .modal-cat {{
      font-size:.72rem; font-weight:700; letter-spacing:.12em;
      text-transform:uppercase; color:var(--accent);
      border:1px solid var(--accent); border-radius:999px;
      padding:2px 10px; display:inline-block; margin-bottom:.5rem;
    }}
    .modal-close {{
      position:absolute; top:1rem; right:1rem;
      background:rgba(10,10,15,.8); border:1px solid var(--border);
      border-radius:50%; width:36px; height:36px; color:var(--text);
      font-size:1.1rem; display:flex; align-items:center; justify-content:center;
      cursor:pointer; z-index:10; transition:background .2s;
    }}
    .modal-close:hover {{ background:var(--accent); color:#0a0a0f; }}

    .empty-state {{
      grid-column:1/-1; text-align:center; padding:5rem 1rem; color:var(--muted);
    }}
    .empty-state .big-emoji {{ font-size:3rem; margin-bottom:1rem; }}

    footer {{
      border-top:1px solid var(--border); text-align:center;
      padding:2rem 1rem; color:var(--muted); font-size:.78rem; letter-spacing:.05em;
    }}
    footer span {{ color:var(--accent); }}

    ::-webkit-scrollbar {{ width:6px; }}
    ::-webkit-scrollbar-track {{ background:var(--bg); }}
    ::-webkit-scrollbar-thumb {{ background:var(--border); border-radius:3px; }}

    @media (max-width:600px) {{
      .stats-badge {{ display:none; }}
      .modal-info  {{ padding:1rem; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="logo">Video<span>Vault</span></div>
  <div class="search-wrap">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
    <input type="text" id="search" placeholder="Search titles, creators, categories…" autocomplete="off" />
  </div>
  <div class="stats-badge" id="count-badge"></div>
</header>

<section class="hero">
  <div class="hero-eyebrow">&#9679; Curated Collection</div>
  <h1>The Vault</h1>
  <p class="hero-sub">Every video, every creator — one place. Explore the catalogue.</p>
</section>

<div class="filters">
  <button class="filter-btn active" data-cat="All">All</button>
  {category_buttons}
</div>

<div class="sort-bar">
  <span>Sort by</span>
  <select id="sort-select">
    <option value="date_desc">Newest first</option>
    <option value="date_asc">Oldest first</option>
    <option value="views_desc">Most viewed</option>
    <option value="views_asc">Least viewed</option>
    <option value="comments_desc">Most commented</option>
    <option value="title_asc">Title A–Z</option>
  </select>
</div>

<div class="grid" id="grid"></div>

<div class="modal-bg" id="modal-bg">
  <div class="modal" id="modal">
    <button class="modal-close" id="modal-close">&#10005;</button>
    <div class="modal-video" id="modal-video-wrap"></div>
    <div class="modal-info">
      <div class="modal-cat"   id="modal-cat"></div>
      <div class="modal-title" id="modal-title"></div>
      <div class="modal-row"   id="modal-row"></div>
    </div>
  </div>
</div>

<footer>Built with <span>&#9673;</span> &mdash; VideoVault &copy; 2025</footer>

<script>
const VIDEOS    = {videos_json};
const THUMB_MAP = {thumb_map_json};

let activeCategory = 'All';
let searchQuery    = '';
let sortMode       = 'date_desc';

function fmtViews(n) {{
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n;
}}
function fmtDate(d) {{
  return new Date(d).toLocaleDateString('en-US',{{month:'short',day:'numeric',year:'numeric'}});
}}
function getInitial(name) {{ return name.charAt(0).toUpperCase(); }}
function getAvatarColor(name) {{
  const pal = ['#ff5c5c','#ff9f47','#47c7ff','#b47fff','#47ff9f','#ff47c7'];
  let h = 0;
  for (const c of name) h = (h*31+c.charCodeAt(0)) & 0xFFFFFF;
  return pal[h % pal.length];
}}

function getFiltered() {{
  let list = VIDEOS.filter(v => {{
    const matchCat = activeCategory === 'All' || v.category === activeCategory;
    const q = searchQuery.toLowerCase();
    const matchQ = !q || v.title.toLowerCase().includes(q)
      || v.added_by.toLowerCase().includes(q)
      || v.category.toLowerCase().includes(q);
    return matchCat && matchQ;
  }});
  list.sort((a,b) => {{
    switch(sortMode) {{
      case 'date_asc':      return new Date(a.date_added)-new Date(b.date_added);
      case 'date_desc':     return new Date(b.date_added)-new Date(a.date_added);
      case 'views_desc':    return b.view_count-a.view_count;
      case 'views_asc':     return a.view_count-b.view_count;
      case 'comments_desc': return b.comment_count-a.comment_count;
      case 'title_asc':     return a.title.localeCompare(b.title);
    }}
  }});
  return list;
}}

function renderGrid() {{
  const grid  = document.getElementById('grid');
  const badge = document.getElementById('count-badge');
  const list  = getFiltered();
  badge.textContent = list.length + ' video' + (list.length !== 1 ? 's' : '');

  if (!list.length) {{
    grid.innerHTML = '<div class="empty-state"><div class="big-emoji">🔍</div><p>No videos match your search.</p></div>';
    return;
  }}

  grid.innerHTML = list.map((v, i) => {{
    const thumb = THUMB_MAP[v.url];
    const thumbHTML = thumb
      ? '<img class="thumb-img" src="' + thumb + '" alt="' + v.title + '" loading="lazy" />'
      : '<div class="thumb-placeholder"><div class="icon">&#127909;</div><span>No preview</span></div>';
    const durHTML = v.duration ? '<div class="duration-badge">' + v.duration + '</div>' : '';
    const avatarColor = getAvatarColor(v.added_by);
    const globalIdx   = VIDEOS.indexOf(v);
    return '<div class="card" data-idx="' + globalIdx + '" style="animation-delay:' + (i*0.04) + 's">'
      + '<div class="thumb">' + thumbHTML + durHTML
      + '<div class="play-overlay"><div class="play-icon">'
      + '<svg width="20" height="20" viewBox="0 0 24 24" fill="#0a0a0f"><polygon points="5,3 19,12 5,21"/></svg>'
      + '</div></div></div>'
      + '<div class="card-body">'
      + '<div class="card-cat">' + v.category + '</div>'
      + '<div class="card-title">' + v.title + '</div>'
      + '<div class="card-meta">'
      + '<div class="card-author"><div class="avatar" style="background:' + avatarColor + '">' + getInitial(v.added_by) + '</div>' + v.added_by + '</div>'
      + '<div class="card-stats">'
      + '<span><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>' + fmtViews(v.view_count) + '</span>'
      + '<span><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>' + v.comment_count + '</span>'
      + '</div></div></div></div>';
  }}).join('');

  grid.querySelectorAll('.card').forEach(card => {{
    card.addEventListener('click', () => openModal(parseInt(card.dataset.idx)));
  }});
}}

function openModal(idx) {{
  const v     = VIDEOS[idx];
  const thumb = THUMB_MAP[v.url] || '';
  document.getElementById('modal-video-wrap').innerHTML =
    '<video controls autoplay muted src="' + v.url + '" poster="' + thumb + '"></video>';
  document.getElementById('modal-cat').textContent   = v.category;
  document.getElementById('modal-title').textContent = v.title;
  document.getElementById('modal-row').innerHTML =
    '<span>&#128100; ' + v.added_by + '</span>'
    + '<span>&#128065; ' + fmtViews(v.view_count) + ' views</span>'
    + '<span>&#128172; ' + v.comment_count + ' comments</span>'
    + '<span>&#128197; ' + fmtDate(v.date_added) + '</span>'
    + (v.duration ? '<span>&#9201; ' + v.duration + '</span>' : '');
  document.getElementById('modal-bg').classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal() {{
  document.getElementById('modal-bg').classList.remove('open');
  document.getElementById('modal-video-wrap').innerHTML = '';
  document.body.style.overflow = '';
}}

document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-bg').addEventListener('click', e => {{
  if (e.target === e.currentTarget) closeModal();
}});
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});

document.querySelectorAll('.filter-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    activeCategory = btn.dataset.cat;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderGrid();
  }});
}});

document.getElementById('search').addEventListener('input', e => {{
  searchQuery = e.target.value; renderGrid();
}});
document.getElementById('sort-select').addEventListener('change', e => {{
  sortMode = e.target.value; renderGrid();
}});

renderGrid();
</script>
</body>
</html>"""


# ── ENTRY POINT ───────────────────────────────────────────────────────────────


async def main_async():
    args = parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, args.csv)
    out_path = os.path.join(script_dir, args.out)

    if not os.path.exists(csv_path):
        print(f"Error: CSV not found at {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📂 Loading: {csv_path}")
    videos = load_csv(csv_path)
    print(f"   → {len(videos)} videos\n")

    print(
        f"🎬 Extracting thumbnails + durations  "
        f"(max {args.workers} parallel ffmpeg jobs)…\n"
    )
    await enrich_videos(videos, args.thumb_time, args.workers)

    print(f"\n🖊️  Writing HTML…")
    html = generate_html(videos)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) // 1024
    print(f"✅ Done → {out_path}  ({size_kb} KB)")
    print(f"   Open: file://{out_path}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
