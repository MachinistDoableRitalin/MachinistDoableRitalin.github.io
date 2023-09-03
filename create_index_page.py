from pathlib import Path

with open("index.html", "w") as f:
    f.write(
        "<ul>\n"
        + "\n".join(
            sorted(
                [
                    f"  <li><a href='{f.name}' target='_blank'>{f.name.split('.')[0]}</a></li>"
                    for f in Path.cwd().iterdir()
                    if f.name.endswith(".html") and f.name != "index.html"
                ]
            )
        )
        + "\n</ul>"
    )
