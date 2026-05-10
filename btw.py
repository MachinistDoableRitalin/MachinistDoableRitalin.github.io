import asyncio
import csv
import json
import logging
import re
from datetime import datetime
from io import StringIO

import aiohttp
import tomllib
from bs4 import BeautifulSoup
from tqdm.asyncio import tqdm

with open("config.toml", "rb") as f:
    env = tomllib.load(f)
# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = env["BASE_URL"]
TARGET = re.sub("page=[0-9]+", "page={page}", env["TARGET"])
PAGES = range(1, 11)

MAX_CONNECTIONS = 20  # total simultaneous TCP connections
MAX_PER_HOST = 10  # per-host limit (be polite)
REQUEST_TIMEOUT = 30  # seconds per request
RETRY_ATTEMPTS = 3  # retries on transient errors
RETRY_BACKOFF = 1.5  # seconds between retries (multiplied each attempt)
OUTPUT_CSV = "data.csv"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)


async def fetch(session: aiohttp.ClientSession, url: str) -> str:
    """GET a URL with retry/back-off on transient errors."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            async with session.get(url, raise_for_status=True) as resp:
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == RETRY_ATTEMPTS:
                log.error("Failed %s after %d attempts: %s", url, attempt, exc)
                raise
            wait = RETRY_BACKOFF * attempt
            log.warning(
                "Attempt %d/%d failed for %s (%s) — retrying in %.1fs",
                attempt,
                RETRY_ATTEMPTS,
                url,
                exc,
                wait,
            )
            await asyncio.sleep(wait)


def parse_info(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info = {
        item.find("dt").get_text(strip=True): item.find("dd").get_text(strip=True)
        for item in soup.find_all(class_="pairs pairs--justified")
        if item.find("dt") and item.find("dd")
    }
    try:
        info["Date added"] = str(
            datetime.strptime(info.get("Date added"), "%b %d, %Y").date()
        )
    except:
        pass
    try:
        info["View count"] = int(info.get("View count").replace(",", "_").strip())
    except:
        info["View count"] = 0
    try:
        info["Comment count"] = int(info.get("Comment count").replace(",", "_").strip())
    except:
        info["Comment count"] = 0
    h1 = soup.find("h1")
    info["Title"] = h1.get_text(strip=True) if h1 else ""
    info["Video"] = f"{BASE_URL}{soup.find('source').get('src')}"
    return info


def parse_video_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [
        f"{BASE_URL}{a['href']}"
        for h3 in soup.find_all("h3")
        if (a := h3.find("a")) and a.get("href")
    ]


async def get_info(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        html = await fetch(session, url)
        return parse_info(html, url)
    except Exception:
        return None  # already logged; skip bad pages


async def get_videos(session: aiohttp.ClientSession, page: int) -> list[dict]:
    url = TARGET.replace("{page}", str(page))
    try:
        html = await fetch(session, url)
    except Exception:
        log.error("Skipping page %d — could not fetch listing.", page)
        return []

    links = parse_video_links(html)
    tasks = [get_info(session, link) for link in links]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=MAX_CONNECTIONS,
        limit_per_host=MAX_PER_HOST,
        ssl=False,  # set True if you need SSL verification
    )

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        page_tasks = [get_videos(session, p) for p in PAGES]

        data: list[dict] = []
        for coro in tqdm(
            asyncio.as_completed(page_tasks),
            total=len(PAGES),
            desc="Scraping pages",
        ):
            page_data = await coro
            data.extend(page_data)

    if not data:
        log.warning("No data collected — check BASE_URL / TARGET.")
        return

    # Collect all unique field names, keeping Title + Video first
    priority = ["Title", "Video"]
    extra = sorted({k for row in data for k in row} - set(priority))
    fieldnames = priority + extra

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)

    log.info("Saved %d records to %s", len(data), OUTPUT_CSV)


if __name__ == "__main__":
    asyncio.run(main())
