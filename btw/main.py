import asyncio
import json
import time
from datetime import datetime

import aiofiles
import httpx
from bs4 import BeautifulSoup
from tqdm import tqdm

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/113.0"
}


def get_video_urls(html_text, search=False):
    soup = BeautifulSoup(html_text, "html.parser")
    if search:
        data = [
            [
                f"https://www.blacktowhite.net{a['href']}",
                f"https://www.blacktowhite.net{img['src']}",
            ]
            for d in soup.find_all(class_="contentRow")
            if (a := d.find("a"))
            and a.has_attr("href")
            and (img := d.find("img"))
            and img.has_attr("src")
        ]
        return data
    data = []
    for a in soup.find_all("a"):
        try:
            video_url = f"https://www.blacktowhite.net{a['data-src']}".replace(
                "/lightbox", ""
            )
            img = f"https://www.blacktowhite.net{a.find('img')['src']}".split(
                "?"
            )[0]
            if video_url and img:
                data.append([video_url, img])
        except:
            pass
    return data


def get_video_source(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    try:
        video_source_url = [
            s["src"]
            for s in soup.find_all("source")
            if s.has_attr("src") and s["src"] and s["src"]
        ][0]
        dt = (
            datetime.utcfromtimestamp(
                int([t for t in soup.find_all("time")][0]["data-time"])
            )
            .date()
            .isoformat()
        )
        return f"https://www.blacktowhite.net{video_source_url}", dt
    except:
        return "", ""


async def main(video_type, duration, pages_range):
    async with aiofiles.open(f"{video_type}-{duration}.html", "a") as f:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for page in pages_range:
                response = await client.get(
                    f"https://www.blacktowhite.net/{video_type}/page-{page}?order=view_count&direction=desc&type=video&newer_than={duration}",
                    headers=headers,
                )
                for video_url, img in tqdm(get_video_urls(response.text)):
                    response = await client.get(video_url, headers=headers)
                    video_source_url, dt = get_video_source(response.text)
                    await f.write(
                        f"""
        <a href="{video_source_url}" target="_blank">
            <img src="{img}">
        </a>
                    """.replace(
                            "                ", ""
                        )
                    )
                    await f.flush()


async def main_search(url: str, keyword, pages):
    async with aiofiles.open(f"{keyword}.html", "a") as f:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for page in tqdm(range(pages)):
                response = await client.get(
                    url.replace("{page}", str(page + 1)),
                    headers=headers,
                )
                for video_url, img in get_video_urls(
                    response.text, search=True
                ):
                    response = await client.get(video_url, headers=headers)
                    video_source_url, dt = get_video_source(response.text)
                    await f.write(
                        f"""
        <a href="{video_source_url}" target="_blank">
            <img src="{img}">
        </a>
                    """
                    )
                    await f.flush()


def get_user_input(arr):
    user_input = input(
        "\n".join([f"{i+1} - {val}" for i, val in enumerate(arr)])
        + f"\n{len(arr) + 1} - all of the above\n\nYour choice: "
    ).split()

    if str(len(arr) + 1) in user_input:
        return arr
    return [val for i, val in enumerate(arr) if str(i + 1) in user_input]


search_query = input("Is this a search query? (y/N)")

if search_query.casefold() == "y":
    url = input("URL: ")
    keyword = input("Keyword: ")
    pages = 10 if (p := int(input("No. of pages: "))) > 10 else p
    filename = f"{keyword}.html"
    print(f"{filename = }")
    with open(filename, "w") as f:
        f.write(
            """
<!DOCTYPE html>
<html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <style>
        img {
            max-width: fit-content;
            height: auto;
        }
        </style>
    </head>
    <body>

    """
        )
    asyncio.run(main_search(url, keyword, pages))
    with open(filename, "a+") as f:
        f.write(
            """

    </body>
</html>

"""
        )

else:
    video_types = [
        "blowjob-videos",
        "fucking-videos",
        "cuckold-vodeos",
        "gangbang-videos",
        "videos",
    ]

    time_durations = [
        "last_month",
        "all",
    ]

    video_types_input = get_user_input(video_types)
    time_durations_input = {
        t: range(*[int(i) for i in input(f"No. of pages for {t}: ").split()])
        for t in get_user_input(time_durations)
    }

    for video_type in video_types_input:
        for duration, pages_range in time_durations_input.items():
            filename = f"{video_type}-{duration}.html"
            print(f"{filename = }")
            with open(filename, "w") as f:
                f.write(
                    """
<!DOCTYPE html>
<html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <style>
        img {
            max-width: fit-content;
            height: auto;
        }
        </style>
    </head>
    <body>

    """
                )
            asyncio.run(main(video_type, duration, pages_range))
            with open(filename, "a+") as f:
                f.write(
                    """

    </body>
</html>

    """
                )
