from pathlib import Path

with open("index.html", "w") as f:
    for folder, arr in sorted(
        [
            folder,
            [
                f"  <li><a href='{folder}/{f.name}' target='_blank'>{f.name.split('.')[0]}</a></li>"
                for f in files
            ],
        ]
        for folder, files in {
            p.name: [i for i in p.iterdir() if i.name.endswith(".html")]
            for p in Path().iterdir()
            if p.is_dir()
        }.items()
    ):
        f.write(f"<h3>{folder}</h3>\n")
        f.write("<ul>\n" + "\n".join(arr) + "\n</ul>")
