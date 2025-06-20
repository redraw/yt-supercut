import os
import re
import math
import json
import tempfile
from pathlib import Path

import webvtt
import appdirs
import frontmatter
from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func, ExistingVideoReached
from . import db


CONFIG_DIR = appdirs.user_config_dir("yt_supercut")
Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)


def get_video_ids(url, cookies_from=None, lang=None, verbose=True, proxy=None):
    stripped_url = re.sub(r"[^\w]", "_", url)
    archive_path = Path(CONFIG_DIR) / f"{stripped_url}.{lang}.txt"

    opts = {
        "skipdownload": True,
        "no_warnings": True,
        "noprogress": True,
        "dumpjson": True,
        "quiet": not verbose,
        "extract_flat": "in_playlist",
        "extractor_args": {
            "youtube": {"skip": ["dash", "hls"]},
            "youtubetab": {"approximate_date": ["timestamp"]},
        },
        "break_on_existing": True,
        "break_per_url": True,
        "force_write_download_archive": True,
        "download_archive": str(archive_path),
    }

    if cookies_from:
        opts.update(
            {
                "cookiesfrombrowser": cookies_from.split(","),
            }
        )

    if proxy:
        opts.update(
            {
                "proxy": proxy,
            }
        )

    with YoutubeDL(opts) as ydl:
        try:
            ydl.extract_info(url, download=False)
        except ExistingVideoReached:
            pass

    with archive_path.open() as f:
        for line in f.readlines():
            _, video_id = line.strip().split(" ")
            yield video_id


def download_and_process_subtitles(
    video_id, lang, cookies_from=None, lock=None, verbose=False, proxy=None
):
    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "skip_download": True,
            "no_warnings": True,
            "writeautomaticsub": True,
            "writeinfojson": True,
            "convertsubtitles": True,
            "noprogress": True,
            "subtitlesformat": "vtt",
            "subtitleslangs": [lang, "-live_chat"],
            "quiet": not verbose,
            "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
        }

        if cookies_from:
            opts.update(
                {
                    "cookiesfrombrowser": cookies_from.split(","),
                }
            )

        if proxy:
            opts.update(
                {
                    "proxy": proxy,
                }
            )

        with YoutubeDL(opts) as yt:
            # download subtitles
            yt.download([f"https://www.youtube.com/watch?v={video_id}"])

        with lock:
            # process subtitles
            # inside a lock, since sqlite3 writes are not thread-safe
            process_subtitles(video_id, lang, tmpdir)


def process_subtitles(video_id, lang, folder):
    filename_sub = os.path.join(folder, f"{video_id}.{lang}.vtt")
    filename_infojson = os.path.join(folder, f"{video_id}.info.json")

    if not os.path.exists(filename_sub):
        db.add_video_language(video_id, lang, available=False)
        return

    with open(filename_infojson) as f:
        info = json.load(f)

    subtitles = []
    filepath = os.path.join(folder, f"{video_id}.{lang}.vtt")

    for caption in webvtt.read(filepath):
        if caption.end_in_seconds - caption.start_in_seconds < 0.5:
            continue

        subtitles.append(
            {
                "video_id": video_id,
                "lang": lang,
                "start_seconds": math.floor(caption.start_in_seconds),
                "end_seconds": math.ceil(caption.end_in_seconds),
                "start_time": caption.start,
                "end_time": caption.end,
                "text": caption.text.split("\n")[0],
            }
        )

    db.clear_subtitles(video_id, lang)
    db.add_subtitles(video_id, lang, subtitles)
    db.add_video_info(info)
    db.add_channel_info(info)


def download_part(info, folder="output", spacing_secs=5, proxy=None):
    print(f"Dowloading {info}")

    if not os.path.exists(folder):
        os.mkdir(folder)

    opts = {
        "ignoreerrors": True,
        "force_keyframes_at_cuts": True,
        "download_ranges": download_range_func(
            None,
            [
                (
                    info["start_seconds"] - spacing_secs,
                    info["end_seconds"] + spacing_secs,
                )
            ],
        ),
        "download_archive": os.path.join(folder, "archive.txt"),
        "outtmpl": os.path.join(
            folder, "%(title)s.%(id)s.%(start_time)s-%(end_time)s.%(ext)s"
        ),
    }

    if proxy:
        opts.update(
            {
                "proxy": proxy,
            }
        )

    with YoutubeDL(opts) as yt:
        yt.download([info["link"]])


def build_transcript(video_id, lang=None, path: str = "."):
    video = db.get_video(video_id)
    fn = os.path.join(path, f"{video_id}.{lang}.md")

    if os.path.exists(fn):
        print(f"Transcript for {video_id} in {lang} already exists.")
        return

    if not video:
        print(f"Video {video_id} not found in the database.")
        return

    subtitles = db.get_subtitles(video_id, lang)
    if not subtitles:
        print(f"No subtitles found for {video_id} in {lang}")
        return

    transcript = ""
    for idx, subtitle in enumerate(subtitles):
        if idx % 25 == 0:
            transcript += (
                f"\n[{subtitle['start_time']} / ?t={subtitle['start_seconds']}]\n"
            )
        transcript += f"{subtitle['text']} "

    with open(fn, "wb") as f:
        p = frontmatter.Post(
            transcript,
            **{
                "title": video["video_title"],
                "lang": lang,
                "url": video["video_url"],
            },
        )
        frontmatter.dump(p, f)
