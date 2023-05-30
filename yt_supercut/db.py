import os
from datetime import datetime
from sqlite_utils import Database

DB_PATH = os.getenv("DB_PATH", "youtube.db")


def get_db() -> Database:
    return Database(DB_PATH)


def init():
    db = get_db()

    db["channels"].create({
            "uploader_id": str,
            "channel_name": str,
            "channel_url": str,
        }, 
        pk="uploader_id", 
        not_null={"channel_url"}, 
        if_not_exists=True,
    )

    db["videos"].create({
            "video_id": str,
            "video_title": str,
            "video_url": str,
            "uploader_id": str,
            "upload_date": str,
        }, 
        pk="video_id", 
        not_null={"video_title", "video_url"}, 
        if_not_exists=True, 
        foreign_keys=[
            ("uploader_id", "channels"),
        ]
    )
    db["videos"].create_index(["uploader_id"], if_not_exists=True)

    db["subtitles"].create(
        {
            "subtitle_id": int,
            "video_id": str,
            "start_time": str,
            "end_time": str,
            "start_seconds": int,
            "end_seconds": int,
            "lang": str,
            "text": str,
        }, 
        pk="subtitle_id", 
        not_null={"start_seconds", "end_seconds", "start_time", "end_time", "lang", "text"}, 
        if_not_exists=True, 
        foreign_keys=[
            ("video_id", "videos")
        ]
    ).enable_fts(
        ["text"], 
        create_triggers=True, 
        replace=True
    )

    db["video_languages"].create({
            "video_id": str,
            "lang": str,
            "available": bool,
        }, 
        pk=("video_id", "lang"),
        foreign_keys=[
            ("video_id", "videos"),
        ], 
        defaults={"available": True},
        not_null={"lang"},
        if_not_exists=True,
    )

    db.create_view("subtitles_with_videos", """
        select
            s.subtitle_id,
            v.video_id,
            v.uploader_id,
            v.video_title,
            v.upload_date,
            c.channel_name,
            s.start_seconds,
            s.end_seconds,
            s.lang,
            s.text,
            v.video_url || "&start=" || (s.start_seconds-4) || "&end=" || (s.end_seconds+2) as link
        from subtitles s
        join videos v on s.video_id = v.video_id
        join channels c ON v.uploader_id = c.uploader_id
    """, replace=True)
    

def add_channel_info(info):
    db = get_db()
    db["channels"].insert({
        "uploader_id": info["uploader_id"],
        "channel_name": info["uploader"],
        "channel_url": info["channel_url"],
    }, replace=True)


def add_video_info(info):
    db = get_db()
    db["videos"].insert({
        "video_id": info["id"],
        "video_title": info["title"],
        "video_url": info["webpage_url"],
        "uploader_id": info["uploader_id"],
        "upload_date": datetime.strptime(info["upload_date"], "%Y%m%d"),
    }, replace=True)


def add_subtitles(video_id, lang, items):
    db = get_db()
    db["subtitles"].insert_all(items)
    db["video_languages"].insert({
        "video_id": video_id,
        "lang": lang,
        "available": True,
    }, replace=True)


def clear_subtitles(video_id, lang):
    db = get_db()
    db["subtitles"].delete_where("video_id = :video_id AND lang = :lang", {
        "video_id": video_id,
        "lang": lang,
    })
    db["video_languages"].delete_where("video_id = :video_id AND lang = :lang", {
        "video_id": video_id,
        "lang": lang,
    })
    db.conn.commit()


def add_video_language(video_id, lang, available=True):
    db = get_db()
    db["video_languages"].insert({
        "video_id": video_id,
        "lang": lang,
        "available": available,
    }, replace=True)


def search(text, uploader_id=None, lang=None):
    db = get_db()
    where = []
    where_args = {}

    where.append(f"subtitle_id in (select rowid from subtitles_fts where subtitles_fts match :text)")
    where_args["text"] = text

    if lang:
        where.append(f"lang = :lang")
        where_args["lang"] = lang

    if uploader_id:
        where.append(f"video_id in (select video_id from videos where uploader_id = :uploader_id)")
        where_args["uploader_id"] = uploader_id

    for row in db["subtitles_with_videos"].rows_where(
        " AND ".join(where),
        where_args,
        order_by="video_id, start_seconds ASC",
    ):
        yield row


def get_channels():
    db = get_db()
    for channel in db["channels"].rows:
        yield channel


def get_channel(uploader_id):
    db = get_db()
    for row in db["channels"].rows_where("uploader_id = ?", [uploader_id]):
        return row


def get_videos():
    db = get_db()
    for video in db["videos"].rows:
        yield video


def filter_existing_video_ids(video_ids, lang):
    db = get_db()
    db.execute("CREATE TEMPORARY TABLE IF NOT EXISTS tmp (video_id TEXT NOT NULL PRIMARY KEY)")
    db["tmp"].insert_all([{"video_id": video_id} for video_id in video_ids])
    for row in db["tmp"].rows_where(
        "video_id not in (select video_id from video_languages where lang = :lang or available = 0)",
        {"lang": lang},
    ):
        yield row["video_id"]
    db["tmp"].drop()


def get_video(video_id):
    db = get_db()
    for row in db["videos"].rows_where("video_id = ?", [video_id]):
        return row


def delete_video(video_id):
    db = get_db()
    db["subtitles"].delete_where("video_id = ?", [video_id])
    db["video_languages"].delete_where("video_id = ?", [video_id])
    db["videos"].delete(video_id)


def delete_channel(uploader_id):
    db = get_db()
    db["videos"].delete_where("uploader_id = ?", [uploader_id])
    db["subtitles"].delete_where("video_id in (select video_id from videos where uploader_id = ?)", [uploader_id])
    db["video_languages"].delete_where("video_id in (select video_id from videos where uploader_id = ?)", [uploader_id])
    db["channels"].delete(uploader_id)
