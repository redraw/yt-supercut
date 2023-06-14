import os
import math
import json
import tempfile
import webvtt
from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func
from . import db


def get_video_ids(url, verbose=True):
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
        }
    }

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if "entries" in info:
        for entry in info["entries"]:
            if "entries" in entry:
                for subentry in entry["entries"]:
                    yield subentry["id"]
            else:
                yield entry["id"]
    else:
        yield info["id"]


def download_and_process_subtitles(video_id, lang, lock=None, verbose=False):
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

        subtitles.append({
            "video_id": video_id,
            "lang": lang,
            "start_seconds": math.floor(caption.start_in_seconds),
            "end_seconds": math.ceil(caption.end_in_seconds),
            "start_time": caption.start,
            "end_time": caption.end,
            "text": caption.text.split("\n")[0],
        })

    db.clear_subtitles(video_id, lang)
    db.add_subtitles(video_id, lang, subtitles) 
    db.add_video_info(info)
    db.add_channel_info(info)


def download_part(info, folder="output", spacing_secs=5):
    print(f"Dowloading {info}")

    if not os.path.exists(folder):
        os.mkdir(folder)

    opts = {
        "ignoreerrors": True,
        "force_keyframes_at_cuts": True,
        "download_ranges": download_range_func(None, [
            (info["start_seconds"] - spacing_secs, info["end_seconds"] + spacing_secs)
        ]),
        "download_archive": os.path.join(folder, "archive.txt"),
        "outtmpl": os.path.join(folder, "%(title)s.%(id)s.%(start_time)s-%(end_time)s.%(ext)s"),
    }

    with YoutubeDL(opts) as yt:
        yt.download([info["link"]])
