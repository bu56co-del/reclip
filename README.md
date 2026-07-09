# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download as MP4 or MP3.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

https://github.com/user-attachments/assets/419d3e50-c933-444b-8cab-a9724986ba05

![ReClip MP3 Mode](assets/preview-mp3.png)

## Features

- Download videos from 1000+ supported sites (via [yt-dlp](https://github.com/yt-dlp/yt-dlp))
- MP4 video or MP3 audio extraction
- Quality/resolution picker
- Bulk downloads — paste multiple URLs at once
- Automatic URL deduplication
- Clean, responsive UI — no frameworks, no build step
- Single Python file backend (~150 lines)

## Quick Start

Only `python3` is required up front. `yt-dlp` and `ffmpeg` are installed
into a local venv on first run — no admin/`sudo` needed.

```bash
git clone https://github.com/averygan/reclip.git
cd reclip
./reclip.sh
```

Open **http://localhost:8899**.

If you'd rather use system packages, install them with your package manager
(`brew install yt-dlp ffmpeg` or `sudo apt install ffmpeg`) before running.

### Native window (optional)

Prefer a standalone window over the browser tab? Run the GUI launcher
instead — it spins up Flask in the background and opens a native window
via [pywebview](https://pywebview.flowrl.com/) (no admin needed):

```bash
./reclip-gui.sh
```

First run installs `pywebview` into the venv. Closing the window stops
the server.

### Double-click launcher (macOS)

Two double-clickable launchers ship with the repo:

- **`ReClip.command`** — opens via Terminal. Works **everywhere**, including
  when the repo lives in TCC-protected folders (Desktop, Documents,
  Downloads, iCloud Drive). A Terminal window stays open while ReClip runs.
- **`ReClip.app`** — a clean macOS app bundle, no Terminal popup. Only works
  when the repo is **outside** TCC-protected folders (e.g. `~/reclip/`),
  because a `.app` located in those folders can't even execute its own
  launcher.

If you see `Operation not permitted` in `reclip.log` when double-clicking
`ReClip.app`, use the Dock app below instead (or move the repo to `~/reclip/`).

### Dock app (macOS, works even on Desktop) — recommended

Run **`make-dock-app.command`** once. It builds `~/Applications/ReClip.app`
(outside the TCC-protected zone) that launches the native GUI with no
Terminal window. Because the app itself lives in `~/Applications`, it can
run even when this repo is on the Desktop — on first launch macOS shows a
normal *"allow access to your Desktop folder"* prompt; click **Allow** (no
admin needed).

```bash
./make-dock-app.command   # or double-click it in Finder
```

Then open **Applications** (Finder → Go → Applications, or ⌘⇧A) and drag
**ReClip.app** into your Dock. Closing the ReClip window stops the server.

To give it a custom icon: copy any image, select `ReClip.app`, press ⌘I,
click the small icon at the top-left of the Info window, and press ⌘V.

Or with Docker:

```bash
docker build -t reclip . && docker run -p 8899:8899 reclip
```

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

### Bypassing "Sign in to confirm" / "Access denied"

Some sites (YouTube especially) block requests that aren't signed in. Run
ReClip with `COOKIES_BROWSER` set to a browser you're logged into, and yt-dlp
will reuse its cookies:

```bash
COOKIES_BROWSER=chrome ./reclip.sh
```

Supported values follow yt-dlp's `--cookies-from-browser` spec — `chrome`,
`firefox`, `edge`, `safari`, `brave`, etc., optionally with a profile
(`chrome:Default`). Close the browser first if it locks its cookie database.

To persist this for the double-click launchers, drop the setting in
`~/.reclip-env`:

```bash
echo 'COOKIES_BROWSER=chrome' >> ~/.reclip-env
```

Every launch (`reclip.sh`, `reclip-gui.sh`, `ReClip.command`, `ReClip.app`)
sources `~/.reclip-env` automatically. You can put `PORT=9000` etc. there
too.

### Modern yt-dlp + Deno (recommended on macOS)

The yt-dlp pip-installed into a Python 3.9 venv caps at 2025.10.14 (later
releases require Python ≥ 3.10), and recent YouTube anti-bot updates need
features only in newer yt-dlp. Two env vars let ReClip use a standalone
yt-dlp build and a JavaScript runtime instead:

```
YTDLP_PATH=/Users/yourname/bin/yt-dlp_macos
DENO_PATH=/Users/yourname/.deno/bin/deno
```

ReClip auto-detects both at `~/bin/yt-dlp_macos` and `~/.deno/bin/deno`
if the env vars aren't set, so on a Mac that already has them the
modern stack engages without any `~/.reclip-env` change.

When the modern stack is active, ReClip:

- forwards `--js-runtimes deno:<path>` so yt-dlp can solve YouTube's
  player JS challenges (avoids the throttled / SABR streams),
- **drops the legacy `player_client` override** — yt-dlp 2025.11+'s own
  defaults (`android_vr`, `web_safari`) skip PO-Token-gated formats
  better than any hand-picked list,
- **downloads cookie-free first, even when `COOKIES_BROWSER` is set.**
  Deno already clears the bot check, so cookies aren't needed — and
  sending them switches yt-dlp to authenticated clients whose adaptive
  formats need a session-bound PO Token and then `403` on the actual
  video data (`unable to download video data: HTTP Error 403: Forbidden`).
  Cookies are added back automatically only when a download fails with a
  login-required signature (private / members-only / age-gated video).

You can verify on startup — the Terminal running ReClip prints e.g.

```
  yt-dlp: /Users/you/bin/yt-dlp_macos (modern)
  deno  : /Users/you/.deno/bin/deno (JS challenges enabled)
```

Install hints (no admin):

```bash
# yt-dlp standalone macOS binary
mkdir -p ~/bin
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos -o ~/bin/yt-dlp_macos
chmod +x ~/bin/yt-dlp_macos

# Deno
curl -fsSL https://deno.land/install.sh | sh
```

### `ERROR: The downloaded file is empty` (YouTube SABR)

For YouTube, ReClip already passes
`--extractor-args youtube:player_client=web_safari,ios,mweb` by default.
These clients skip YouTube's SABR-only streams (which complete with zero
bytes) and avoid the `tv_simply` "page needs to be reloaded" trap. Other
extractors silently ignore the `youtube:` key, so the default is safe
across all sites.

If you need to override (e.g. a specific video that only works with
another client), put your own value in `~/.reclip-env`:

```
YTDLP_EXTRA_ARGS="--extractor-args youtube:player_client=ios"
```

The `~/.reclip-env` file is sourced by bash, so **values containing
spaces must be quoted** — without the surrounding `"..."` bash treats
the second word as a command and you'll see `command not found`.

`YTDLP_EXTRA_ARGS` is forwarded verbatim to every yt-dlp invocation, so
any other flag (e.g. `--no-mtime`) can go there too.

### `pyobjc-core` build failure on macOS

If `./reclip-gui.sh` first-run dies trying to compile `pyobjc-core` (clang
errors like `default-const-init-var-unsafe`), the venv is using a too-old
`pip` that picked a yanked release. The script now upgrades `pip` on fresh
venvs, so the easiest recovery is:

```bash
rm -rf venv && ./reclip-gui.sh
```

If a download still fails, your yt-dlp may be out of date — these platforms
change often. Update it inside the venv:

```bash
source venv/bin/activate && pip install -U yt-dlp
```

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python + Flask (~150 lines)
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Dependencies:** 2 (Flask, yt-dlp)

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
