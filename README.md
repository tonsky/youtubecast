# youtubecast

Turns YouTube channels/playlists into audio podcast feeds. Runs on a schedule
(cron, [Houston](https://github.com/tonsky/houston) task), downloads audio +
thumbnails with yt-dlp, generates `channel.xml` (podcast RSS) per source. Point
a static webserver at the `root` folder and subscribe to
`<base_url>/<folder>/channel.xml`.

## Requirements

- [uv](https://docs.astral.sh/uv/): dependencies are declared inline in
  `youtubecast.py` (PEP 723), `uv run --script` creates and caches the
  environment. Uses Python 3.11+ if installed, downloads one otherwise
- ffmpeg
- [Deno](https://deno.com): yt-dlp needs a JavaScript runtime for full YouTube
  support and only uses Deno by default, see
  [EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS)

## Configuration

Copy `config.example.toml` to `config.toml` in the data dir and edit. The data
dir is `$DATA_DIR` if set (Houston sets it), otherwise the script's folder. It
also holds the lock file and, by default, the podcasts.

```toml
base_url = "https://podcasts.example.com/"
backlog  = 1

[[podcasts]]
url    = "https://www.youtube.com/@SomeChannel"
title  = "Some Channel"
folder = "some-channel"

[[podcasts]]
url           = "https://www.youtube.com/playlist?list=PL0123456789abcdef0123456789abcdef"
title         = "Some Playlist"
folder        = "some-playlist"
backlog       = 3
backlog_order = "asc"
pubdate       = "download"
```

- `base_url` — public URL the webserver serves `root` at
- `root` — folder where podcast folders are created (webserver docroot).
  Relative to the data dir, default `"podcasts"`; absolute paths work too
- `backlog` — how many latest videos to download when a source is added (default 1)
- `[[podcasts]]` — one per source: `url` (channel or playlist), `title` (podcast
  name), `folder` (folder name under `root`)

Optional, global or per-podcast (per-podcast wins):

- `backlog` — per-source override
- `backlog_order` — how the source lists its videos, used to pick the newest
  `backlog` videos on first run: `"desc"` (default) means newest first, so they
  are taken from the beginning; `"asc"` means oldest first, taken from the end.
  Channels always list newest first; playlists depend on their sort order
- `lang` — episode metadata is fetched in the video's original language by
  default; set e.g. `lang = "ru"` to pin a language if YouTube ever starts
  serving auto-translated titles/descriptions
- `pubdate` — what becomes the episode's pubDate: `"upload"` (default) uses the
  video's original upload date; `"download"` uses the time the episode was
  downloaded, so old videos added to a playlist appear as new episodes

## File structure

```
youtubecast/
├── youtubecast.py
│                       # data dir, the same folder unless $DATA_DIR is set:
├── config.toml
├── youtubecast.lock    # prevents overlapping runs
└── podcasts/           # `root` from config, serve this with a webserver
    ├── some-channel_logo.jpg   # put it here yourself, the feed references it
    ├── some-channel/
    │   ├── channel.xml     # podcast RSS feed
    │   ├── episodes.json   # episode metadata, channel.xml is rebuilt from it
    │   ├── seen.txt        # video IDs already handled
    │   ├── <video_id>.m4a  # episode audio
    │   └── <video_id>.jpg  # episode thumbnail
    ├── some-playlist_logo.jpg
    └── some-playlist/
        └── ...
```

New videos are detected as "not in `seen.txt`", i.e. new since last *download*,
not last check — a failed download retries on the next run. Delete an ID from
`seen.txt` (and its files) to re-download it.

## Running

```sh
uv run --script --upgrade-package yt-dlp youtubecast.py
```

`--upgrade-package yt-dlp` updates yt-dlp in the cached environment on every
run (YouTube breaks old versions often); without it uv keeps whatever version
it installed first. Costs under a second when there's nothing new. Exits
non-zero if any source failed.

### Houston

One service: an hourly task plus a Caddy site serving the podcasts from the
data dir. Put `config.toml` into `~/youtubecast/data/`.

```toml
[services.youtubecast]
repo     = "https://github.com/tonsky/youtubecast"
task     = "uv run --script --upgrade-package yt-dlp youtubecast.py"
schedule = "hourly"
timeout  = "55m"
caddy    = """
podcasts.example.com {
    root * {{data}}/podcasts
    file_server
}
"""
```

### Cron

`crontab -e`:

```cron
# check sources hourly
0 * * * *  cd /path/to/youtubecast && uv run --script --upgrade-package yt-dlp youtubecast.py >> cron.log 2>&1
```
