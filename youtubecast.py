#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["yt-dlp[default]"]  # [default] adds yt-dlp-ejs for YouTube's JS challenges
# ///
"""Turn YouTube channels/playlists into audio podcast feeds.

Reads config.toml from $DATA_DIR (default: next to the script), checks each
source for videos not yet downloaded, downloads audio + thumbnail with yt-dlp,
and regenerates channel.xml (podcast RSS) in each podcast's folder. Designed to
run on a schedule (cron, systemd timer, Houston task).
"""

import email.utils
import fcntl
import json
import os
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp

SCRIPT_DIR = Path(__file__).resolve().parent
# config.toml, lock file and (by default) podcasts/ live here
DATA_DIR = Path(os.environ.get("DATA_DIR") or SCRIPT_DIR)

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"

MIME_TYPES = {
    "m4a": "audio/x-m4a",
    "mp4": "audio/mp4",
    "mp3": "audio/mpeg",
    "webm": "audio/webm",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
}


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def is_playlist(url):
    return "playlist?list=" in url


def normalize_url(url):
    # Channel root URLs list tabs, not videos; point yt-dlp at the Videos tab
    if re.search(r"youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/?$", url):
        return url.rstrip("/") + "/videos"
    return url


def list_entries(url):
    """Flat-extract video entries without downloading. Returns [] of dicts with at least 'id'."""
    opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
    }
    if not is_playlist(url):
        opts["playlistend"] = 100  # channel Videos tab is newest-first; 100 is plenty per run
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(normalize_url(url), download=False)
    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    return [e for e in entries if e.get("live_status") not in ("is_upcoming", "is_live")]


def newest_first(entries, backlog_order):
    # backlog_order describes how the source lists videos: "desc" = newest
    # first (channels, some playlists), "asc" = oldest first
    return entries if backlog_order == "desc" else list(reversed(entries))


def load_seen(folder):
    path = folder / "seen.txt"
    if not path.exists():
        return None
    return set(path.read_text().split())


def mark_seen(folder, ids):
    with open(folder / "seen.txt", "a") as f:
        for id in ids:
            f.write(id + "\n")


def load_episodes(folder):
    path = folder / "episodes.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_episodes(folder, episodes):
    (folder / "episodes.json").write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2)
    )


def download_episode(folder, video_id, lang=None, pubdate="upload"):
    """Download audio + thumbnail for one video. Returns an episode dict."""
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if lang:
        opts["extractor_args"] = {"youtube": {"lang": [lang]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)

    filepath = Path(info["requested_downloads"][0]["filepath"])
    thumbnail = folder / f"{video_id}.jpg"
    # pubdate = "upload" keeps the video's original date; "download" makes
    # old videos added to a playlist show up as new episodes
    timestamp = None
    if pubdate == "upload":
        timestamp = info.get("timestamp")
        if not timestamp and info.get("upload_date"):
            timestamp = int(
                datetime.strptime(info["upload_date"], "%Y%m%d")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
    return {
        "id": video_id,
        "title": info.get("title") or video_id,
        "description": info.get("description") or "",
        "timestamp": timestamp or int(datetime.now(timezone.utc).timestamp()),
        "duration": info.get("duration"),
        "link": info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
        "file": filepath.name,
        "mime": MIME_TYPES.get(filepath.suffix.lstrip("."), "application/octet-stream"),
        "filesize": filepath.stat().st_size,
        "thumbnail": thumbnail.name if thumbnail.exists() else None,
    }


def write_feed(folder, podcast, base_url, episodes):
    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("atom", ATOM_NS)
    podcast_url = base_url + podcast["folder"] + "/"

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    def el(parent, tag, text=None, **attrs):
        e = ET.SubElement(parent, tag, attrs)
        if text is not None:
            e.text = text
        return e

    el(channel, "title", podcast["title"])
    el(channel, "link", podcast["url"])
    el(channel, "description", podcast["title"])
    logo_url = base_url + podcast["folder"] + "_logo.jpg"
    el(channel, f"{{{ATOM_NS}}}link", href=podcast_url + "channel.xml", rel="self", type="application/rss+xml")
    el(channel, f"{{{ITUNES_NS}}}image", href=logo_url)
    image = el(channel, "image")
    el(image, "url", logo_url)
    el(image, "title", podcast["title"])
    el(image, "link", podcast["url"])

    for ep in sorted(episodes, key=lambda e: e["timestamp"], reverse=True):
        item = el(channel, "item")
        el(item, "title", ep["title"])
        el(item, "description", ep["description"])
        el(item, "link", ep["link"])
        el(item, "guid", ep["id"], isPermaLink="false")
        el(item, "pubDate", email.utils.format_datetime(
            datetime.fromtimestamp(ep["timestamp"], tz=timezone.utc)))
        el(item, "enclosure", url=podcast_url + ep["file"],
           type=ep["mime"], length=str(ep["filesize"]))
        if ep.get("duration"):
            el(item, f"{{{ITUNES_NS}}}duration", str(int(ep["duration"])))
        if ep.get("thumbnail"):
            el(item, f"{{{ITUNES_NS}}}image", href=podcast_url + ep["thumbnail"])

    ET.indent(rss)
    ET.ElementTree(rss).write(folder / "channel.xml", encoding="utf-8", xml_declaration=True)


def process_podcast(podcast, config):
    folder = DATA_DIR / config.get("root", "podcasts") / podcast["folder"]  # absolute root wins
    folder.mkdir(parents=True, exist_ok=True)
    backlog = podcast.get("backlog", config.get("backlog", 1))

    backlog_order = podcast.get("backlog_order", config.get("backlog_order", "desc"))
    entries = newest_first(list_entries(podcast["url"]), backlog_order)
    seen = load_seen(folder)

    if seen is None:
        # First run: newest `backlog` become episodes, everything older is marked seen
        candidates = entries[:backlog]
        skipped = [e["id"] for e in entries[backlog:]]
        mark_seen(folder, skipped)
        log(f"[{podcast['folder']}] first run: {len(entries)} videos, "
            f"downloading {len(candidates)}, skipping {len(skipped)}")
    else:
        candidates = [e for e in entries if e["id"] not in seen]
        if candidates:
            log(f"[{podcast['folder']}] {len(candidates)} new video(s)")

    episodes = load_episodes(folder)
    known = {ep["id"] for ep in episodes}
    added = 0
    for entry in reversed(candidates):  # oldest first
        if entry["id"] in known:
            mark_seen(folder, [entry["id"]])
            continue
        try:
            # entry titles from flat listings can be auto-translated, so log
            # the real title only after the full metadata arrives
            log(f"[{podcast['folder']}] downloading {entry['id']}")
            ep = download_episode(folder, entry["id"],
                                  podcast.get("lang", config.get("lang")),
                                  podcast.get("pubdate", config.get("pubdate", "upload")))
            episodes.append(ep)
            save_episodes(folder, episodes)
            mark_seen(folder, [entry["id"]])
            added += 1
            log(f"[{podcast['folder']}] added {ep['id']} \"{ep['title']}\"")
        except yt_dlp.utils.DownloadError as e:
            log(f"[{podcast['folder']}] FAILED {entry['id']}: {e}")

    if added or not (folder / "channel.xml").exists():
        write_feed(folder, podcast, config["base_url"], episodes)
        log(f"[{podcast['folder']}] feed updated, {len(episodes)} episode(s)")


def main():
    lock = open(DATA_DIR / "youtubecast.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another instance is running, exiting")
        return 0

    with open(DATA_DIR / "config.toml", "rb") as f:
        config = tomllib.load(f)

    failures = 0
    for podcast in config["podcasts"]:
        try:
            process_podcast(podcast, config)
        except Exception as e:
            failures += 1
            log(f"[{podcast['folder']}] ERROR: {type(e).__name__}: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
