import asyncio

import aiofiles
import httpx
from bs4 import BeautifulSoup
from tqdm import tqdm

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/113.0"
}


def get_video_urls(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    return [
        [
            str(a["href"]).strip(),
            f"https:{a.find(class_='lazyload')['data-src']}",
        ]
        for a in soup.find_all("a", href=True)
        if a.find("img") and a["href"].strip() and a.find(class_="lazyload")
    ]


def get_video_source(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    arr = [
        str(s["src"]).strip()
        for s in soup.find_all("source")
        if s.has_attr("src") and str(s["src"]).strip()
    ]
    return arr[0] if arr else ""


video_source_urls = []


async def main(url, keyword, pages):
    async with aiofiles.open(f"{keyword}.html", "a") as f:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for page in tqdm(range(1, pages + 1)):
                response = await client.get(
                    url.replace("{keyword}", keyword).replace(
                        "{page}", str(page)
                    ),
                    headers=headers,
                )
                for video_url, video_img in get_video_urls(response.text):
                    response = await client.get(video_url, headers=headers)
                    video_source_url = get_video_source(response.text)
                    if (
                        video_source_url
                        and video_source_url not in video_source_urls
                    ):
                        if not video_source_url:
                            continue
                        video_source_urls.append(video_source_url)
                        await f.write(
                            f"""\n<a href="{video_source_url}" target="_blank"><img src="{video_img}"></a>\n"""
                        )
                    await f.flush()


url = "https://www.homemoviestube.com/search/{keyword}/page{page}.html?sortby=views"
keyword, pages = input().split()

with open(f"{keyword}.html", "w") as f:
    f.write(
        """
<!DOCTYPE html>
<html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <style>
        img {
            width: 300px;
            height: auto;
        }
        </style>
    </head>
    <body>

"""
    )

asyncio.run(main(url, keyword, int(pages)))

with open(f"{keyword}.html", "a+") as f:
    f.write(
        """

    </body>
</html>

"""
    )
