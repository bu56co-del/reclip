#!/usr/bin/env python3
"""Fetch a webpage, find every video on it, download with yt-dlp, then
transcribe each one via faster-whisper. Outputs:

    <outdir>/page.html              — raw page (for debugging)
    <outdir>/video_urls.txt         — discovered video URLs
    <outdir>/video-N.<ext>          — downloaded videos
    <outdir>/transcripts/N.txt      — per-video transcript
    <outdir>/transcripts/N.srt      — per-video SRT (timestamps)
    <outdir>/full_transcript.txt    — all transcripts concatenated

Designed for Mac, no admin needed. Installs faster-whisper into the current
venv on first run (~50MB). yt-dlp is invoked as a subprocess so you can
point YTDLP_PATH at the standalone macOS binary you already have.

Usage:
    python3 transcribe_page.py "<url>" [--whisper-model base.en] \\
                                       [--outdir ~/Downloads/v-jepa]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Pull anything that looks like a media file or HLS manifest. Quote chars
# and angle brackets are excluded so the URL doesn't run into HTML.
URL_RE = re.compile(
    r"""https?://[^\s"'<>\\]+?\.(?:mp4|webm|m3u8|m4v|mov)(?:\?[^\s"'<>\\]*)?""",
    re.IGNORECASE,
)
# Also extract og:video / twitter:player:stream meta tags.
META_VIDEO_RE = re.compile(
    r'<meta\s+[^>]*?(?:property|name)=["\'](?:og:video(?::url|:secure_url)?|twitter:player:stream)["\'][^>]*?content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def find_yt_dlp() -> str:
    for env in ("YTDLP_PATH",):
        p = os.environ.get(env)
        if p and os.path.isfile(os.path.expanduser(p)):
            return os.path.expanduser(p)
    for cand in ("~/bin/yt-dlp_macos", "~/bin/yt-dlp"):
        cand = os.path.expanduser(cand)
        if os.path.isfile(cand):
            return cand
    found = shutil.which("yt-dlp")
    if found:
        return found
    sys.exit("yt-dlp not found. Set YTDLP_PATH or `brew install yt-dlp`.")


def find_deno() -> str | None:
    for cand in ("~/.deno/bin/deno",):
        cand = os.path.expanduser(cand)
        if os.path.isfile(cand):
            return cand
    return shutil.which("deno")


def discover_videos(html: bytes, page_url: str) -> list[str]:
    text = html.decode("utf-8", errors="ignore")
    urls = set()
    for m in URL_RE.finditer(text):
        urls.add(m.group(0))
    for m in META_VIDEO_RE.finditer(text):
        urls.add(m.group(1))
    # Filter out obvious noise (tracking pixels reusing .mp4 as path)
    return sorted(u for u in urls if not u.endswith(".gif.mp4"))


def yt_dlp_download(ytdlp: str, url: str, out_template: str, deno: str | None) -> int:
    cmd = [ytdlp, "--no-playlist", "--no-warnings",
           "-S", "vcodec:h264,acodec:m4a",
           "--merge-output-format", "mp4",
           "-o", out_template, url]
    if deno:
        cmd[1:1] = ["--js-runtimes", f"deno:{deno}"]
    print("  $", " ".join(cmd))
    return subprocess.run(cmd).returncode


def ensure_faster_whisper():
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    print("Installing faster-whisper into the current venv (one-time, ~50MB)...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"])


def transcribe(audio_path: Path, txt_path: Path, srt_path: Path, model_size: str, language: str | None) -> None:
    from faster_whisper import WhisperModel
    # int8 quantized = fastest on CPU; runs on every Mac. M-series users
    # can swap to compute_type="int8_float16" for a small speedup.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio_path), language=language, vad_filter=True)

    lines_txt = []
    lines_srt = []
    for i, seg in enumerate(segments, 1):
        lines_txt.append(seg.text.strip())
        lines_srt.append(
            f"{i}\n{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}\n{seg.text.strip()}\n"
        )
    txt_path.write_text("\n".join(lines_txt) + "\n", encoding="utf-8")
    srt_path.write_text("\n".join(lines_srt), encoding="utf-8")
    print(f"    detected language: {info.language} (prob {info.language_probability:.2f})")
    print(f"    duration: {info.duration:.1f}s, transcribed segments: {len(lines_txt)}")


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="Webpage URL containing one or more videos")
    ap.add_argument("--outdir", default=None, help="Output dir (default: ~/Downloads/transcribe-<host>)")
    ap.add_argument("--whisper-model", default="base.en",
                    help="faster-whisper model size: tiny.en, base.en, small.en, medium.en, large-v3")
    ap.add_argument("--language", default="en", help="Language hint for whisper (set empty to auto-detect)")
    ap.add_argument("--skip-download", action="store_true", help="Skip download step (re-transcribe existing video-*.mp4)")
    args = ap.parse_args()

    if not args.outdir:
        import urllib.parse
        host = urllib.parse.urlparse(args.url).netloc.replace(".", "-")
        args.outdir = os.path.expanduser(f"~/Downloads/transcribe-{host}")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {outdir}")

    page_path = outdir / "page.html"
    urls_path = outdir / "video_urls.txt"

    if not args.skip_download:
        print("\n=== Step 1: Fetch page ===")
        try:
            page_path.write_bytes(fetch(args.url))
            print(f"  saved {page_path} ({page_path.stat().st_size} bytes)")
        except Exception as e:
            sys.exit(f"Fetch failed: {e}")

        videos = discover_videos(page_path.read_bytes(), args.url)
        if not videos:
            print("  No <video>/og:video URLs found by regex.")
            print("  Falling back to letting yt-dlp scrape the page directly.")
            videos = [args.url]
        urls_path.write_text("\n".join(videos) + "\n", encoding="utf-8")
        print(f"  found {len(videos)} candidate(s); see {urls_path}")
        for v in videos:
            print(f"    - {v}")

        print("\n=== Step 2: Download with yt-dlp ===")
        ytdlp = find_yt_dlp()
        deno = find_deno()
        print(f"  yt-dlp: {ytdlp}")
        if deno:
            print(f"  deno  : {deno}")
        for i, vurl in enumerate(videos, 1):
            tmpl = str(outdir / f"video-{i}.%(ext)s")
            rc = yt_dlp_download(ytdlp, vurl, tmpl, deno)
            if rc != 0:
                print(f"  ⚠️  yt-dlp returned {rc} for {vurl}")

    print("\n=== Step 3: Ensure faster-whisper ===")
    ensure_faster_whisper()

    print("\n=== Step 4: Transcribe ===")
    transcripts_dir = outdir / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)
    media_files = sorted(p for p in outdir.iterdir()
                         if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".m4v", ".mov", ".mp3", ".m4a", ".wav"})
    if not media_files:
        sys.exit("No media files found to transcribe in " + str(outdir))
    for f in media_files:
        stem = f.stem
        print(f"  → {f.name}")
        transcribe(f, transcripts_dir / f"{stem}.txt", transcripts_dir / f"{stem}.srt",
                   args.whisper_model, args.language or None)

    print("\n=== Step 5: Concatenate ===")
    full = outdir / "full_transcript.txt"
    with full.open("w", encoding="utf-8") as out:
        for f in sorted(transcripts_dir.glob("*.txt")):
            out.write(f"\n\n========== {f.stem} ==========\n\n")
            out.write(f.read_text(encoding="utf-8"))
    print(f"  saved {full} ({full.stat().st_size} bytes)")

    print(f"\n✅ Done. Paste {full} contents back to Claude for a summary.")


if __name__ == "__main__":
    main()
