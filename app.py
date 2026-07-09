import os
import shutil
import sys
import uuid
import glob
import json
import re
import shlex
import subprocess
import threading
import time
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

FFMPEG_PATH = os.environ.get("FFMPEG_PATH")
# Pass browser cookies to yt-dlp to get past "Sign in to confirm" /
# "Access denied" bot checks. Value is yt-dlp's --cookies-from-browser
# spec, e.g. "chrome", "firefox", or "chrome:Default".
COOKIES_BROWSER = os.environ.get("COOKIES_BROWSER")


def _find_executable(env_var, fallbacks, path_lookup=None):
    candidate = os.environ.get(env_var)
    if candidate:
        candidate = os.path.expanduser(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for c in fallbacks:
        c = os.path.expanduser(c)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    if path_lookup:
        found = shutil.which(path_lookup)
        if found:
            return found
    return None


# yt-dlp binary: prefer an external standalone build (which ships its own
# Python and tracks upstream weekly — much newer than what pip installs
# into a Py3.9 venv where the latest supported release caps at 2025.10.14).
# Fall back to whatever yt-dlp is in PATH (typically the venv's pip-installed
# version set up by reclip.sh).
YTDLP = _find_executable(
    "YTDLP_PATH",
    ["~/bin/yt-dlp_macos", "~/bin/yt-dlp"],
    path_lookup="yt-dlp",
) or "yt-dlp"

# Deno JavaScript runtime: when present, recent yt-dlp uses it to solve
# YouTube's player JS challenges — without that, many videos serve only
# SABR/throttled streams.
DENO = _find_executable("DENO_PATH", ["~/.deno/bin/deno"], path_lookup="deno")


def _supports_flag(binary, flag):
    try:
        r = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=10)
        return flag in r.stdout
    except (OSError, subprocess.SubprocessError):
        return False


# Modern yt-dlp (2025.11+) understands --js-runtimes. When we have both a
# capable binary and Deno, yt-dlp's own default clients (android_vr,
# web_safari) handle PO Tokens and SABR correctly — so we drop our legacy
# hardcoded player_client override that's now actively counterproductive.
MODERN_YTDLP = _supports_flag(YTDLP, "--js-runtimes")
USE_JS_RUNTIME = MODERN_YTDLP and bool(DENO)


def js_runtime_args():
    return ["--js-runtimes", f"deno:{DENO}"] if USE_JS_RUNTIME else []


# Default YouTube extractor args.
#   - Modern stack (yt-dlp 2025.11+): leave defaults alone — android_vr +
#     web_safari + JS challenge solving cover PO-Token-gated videos.
#   - Legacy stack: yt-dlp silently skips cookie-incompatible clients
#     (ios, android, tv*) when cookies-from-browser is set, leaving only
#     web_safari which YouTube force-SABRs. Pick a working list manually.
# Either way, YTDLP_EXTRA_ARGS in ~/.reclip-env wins.
if MODERN_YTDLP:
    DEFAULT_EXTRA_ARGS = []
elif COOKIES_BROWSER:
    DEFAULT_EXTRA_ARGS = ["--extractor-args", "youtube:player_client=mweb,web_safari,web"]
else:
    DEFAULT_EXTRA_ARGS = ["--extractor-args", "youtube:player_client=web_safari,ios,mweb,tv_simply"]
_user_args = shlex.split(os.environ.get("YTDLP_EXTRA_ARGS", ""))
EXTRA_ARGS = _user_args if _user_args else DEFAULT_EXTRA_ARGS

# Make HLS fallback fail fast — when YouTube serves a fragmented stream
# that 403s, yt-dlp's defaults grind through (fragments * 10 retries)
# attempts and a job can hang for hours. Quit at the first failure so
# the user sees a real error in seconds.
FAIL_FAST_ARGS = ["--abort-on-unavailable-fragments", "--fragment-retries", "3"]


def cookie_args():
    return ["--cookies-from-browser", COOKIES_BROWSER] if COOKIES_BROWSER else []


# Print which stack we ended up on so users running ReClip from a terminal
# can immediately see whether the modern path engaged.
print(f"  yt-dlp: {YTDLP}{' (modern)' if MODERN_YTDLP else ''}", file=sys.stderr)
if DENO:
    print(f"  deno  : {DENO}{' (JS challenges enabled)' if USE_JS_RUNTIME else ''}", file=sys.stderr)
elif MODERN_YTDLP:
    print("  deno  : not found — install for PO-Token / SABR-gated videos", file=sys.stderr)
if MODERN_YTDLP and COOKIES_BROWSER:
    print("  cookies: held for retry only (modern stack downloads cookie-free "
          "first to avoid PO-Token 403s)", file=sys.stderr)


jobs = {}

# Matches yt-dlp's --newline progress lines, e.g.:
#   [download]   1.4% of   12.34MiB at  2.34MiB/s ETA 00:08
#   [download]  50.0% of ~ 150.00MiB at   45.32KiB/s ETA 25:00
#   [download] 100% of   12.34MiB in 00:05
PROGRESS_RE = re.compile(r"\[download\]\s+([0-9.]+)%")
SPEED_RE = re.compile(r"\bat\s+(\S+)")

# Kill yt-dlp if it produces no output for this long (probably stuck).
# Resets on every line, so long videos with steady progress are fine.
STALL_TIMEOUT = 300
# Absolute backstop so a truly pathological job doesn't run forever.
HARD_TIMEOUT = 3600


# Container/codec compatibility — QuickTime decodes H.264 reliably but
# struggles with VP9/AV1 inside mp4 (Threads/IG often serve those). When
# we detect the result is on the unfriendly list, we re-encode to H.264.
QUICKTIME_FRIENDLY_CODECS = {"h264", "avc", "avc1"}

TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
DURATION_RE = re.compile(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)")
FFMPEG_VIDEO_CODEC_RE = re.compile(r"Video:\s+(\w+)")
FFMPEG_SPEED_RE = re.compile(r"speed=\s*(\S+)")


def _ffmpeg_binary():
    if FFMPEG_PATH:
        return FFMPEG_PATH
    return shutil.which("ffmpeg")


def _probe_video_codec(path):
    ff = _ffmpeg_binary()
    if not ff:
        return None
    try:
        r = subprocess.run(
            [ff, "-hide_banner", "-i", path],
            capture_output=True, text=True, timeout=15,
        )
        # ffmpeg exits non-zero because no output was specified; codec info
        # is in stderr regardless.
        m = FFMPEG_VIDEO_CODEC_RE.search(r.stderr)
        return m.group(1).lower() if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def _transcode_to_h264(src_path, job, job_id, source_codec):
    ff = _ffmpeg_binary()
    if not ff:
        return None
    root, ext = os.path.splitext(src_path)
    dst_path = f"{root}.h264{ext}"
    notice = (
        f"--- ReClip: source codec is {source_codec.upper()}, transcoding to "
        f"H.264 for QuickTime compatibility ---"
    )
    print(f"  [yt-dlp:{job_id}] {notice}", file=sys.stderr, flush=True)
    job["log"].append(notice)
    job["phase"] = "Transcoding to H.264"
    job["progress"] = 0.0
    job.pop("speed", None)

    cmd = [
        ff, "-y", "-i", src_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        dst_path,
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except OSError as e:
        job["log"].append(f"ReClip: ffmpeg spawn failed ({e}); keeping original file.")
        return None

    duration = None
    for line in proc.stderr:
        line = line.rstrip()
        if not line:
            continue
        if duration is None:
            m_d = DURATION_RE.search(line)
            if m_d:
                h, m, s = m_d.groups()
                duration = int(h) * 3600 + int(m) * 60 + float(s)
        m_t = TIME_RE.search(line)
        if m_t and duration:
            h, m, s = m_t.groups()
            cur = int(h) * 3600 + int(m) * 60 + float(s)
            job["progress"] = min(99.5, (cur / duration) * 100)
        m_sp = FFMPEG_SPEED_RE.search(line)
        if m_sp:
            job["speed"] = m_sp.group(1)

    rc = proc.wait()
    if rc != 0:
        job["log"].append(
            f"ReClip: transcode failed (ffmpeg exit {rc}); keeping the original "
            f"{source_codec.upper()} file. Open it with VLC."
        )
        try:
            os.remove(dst_path)
        except OSError:
            pass
        return None

    # Swap in the new file under the original filename so downstream
    # filename / glob logic doesn't change.
    try:
        os.remove(src_path)
        os.rename(dst_path, src_path)
    except OSError as e:
        job["log"].append(f"ReClip: post-transcode rename failed ({e}).")
        return None
    return src_path


def _build_cmd(url, format_choice, format_id, out_template, extra_args, with_cookies):
    cmd = [YTDLP, "--no-playlist", "--newline", "-o", out_template]
    cmd += js_runtime_args()
    if with_cookies and COOKIES_BROWSER:
        cmd += ["--cookies-from-browser", COOKIES_BROWSER]
    cmd += extra_args + FAIL_FAST_ARGS
    if FFMPEG_PATH:
        cmd += ["--ffmpeg-location", FFMPEG_PATH]
    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        # Prefer H.264 video + AAC audio so the merged mp4 plays in macOS
        # QuickTime / Safari without VLC. -S is non-strict: if H.264 isn't
        # available (e.g. Threads / IG VP9-only), yt-dlp falls back to the
        # best alternative codec.
        cmd += ["-S", "vcodec:h264,acodec:m4a", "--merge-output-format", "mp4"]
        if format_id:
            cmd += ["-f", f"{format_id}+bestaudio/best"]
        else:
            cmd += ["-f", "bestvideo+bestaudio/best"]
    cmd.append(url)
    return cmd


def _run_yt_dlp(cmd, job, job_id):
    """Spawn yt-dlp, stream its output to job["log"] + stderr, parse
    progress/phase/speed into job. Returns (rc, kill_reason)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    started = time.monotonic()
    state = {"last_activity": started, "kill_reason": None}

    def _watchdog():
        while proc.poll() is None:
            now = time.monotonic()
            if now - state["last_activity"] > STALL_TIMEOUT:
                state["kill_reason"] = f"no progress for {STALL_TIMEOUT // 60} min"
                proc.kill()
                return
            if now - started > HARD_TIMEOUT:
                state["kill_reason"] = f"exceeded {HARD_TIMEOUT // 60} min total"
                proc.kill()
                return
            time.sleep(5)

    threading.Thread(target=_watchdog, daemon=True).start()

    for line in proc.stdout:
        state["last_activity"] = time.monotonic()
        line = line.rstrip()
        if not line:
            continue
        print(f"  [yt-dlp:{job_id}] {line}", file=sys.stderr, flush=True)
        job["log"].append(line)
        if len(job["log"]) > 500:
            job["log"].pop(0)

        m = PROGRESS_RE.search(line)
        if m:
            job["progress"] = float(m.group(1))
            job["phase"] = "Downloading"
            speed_m = SPEED_RE.search(line)
            if speed_m:
                job["speed"] = speed_m.group(1)
        elif "Destination:" in line:
            job["phase"] = "Downloading"
        elif "Merging formats" in line:
            job["phase"] = "Merging"
            job["progress"] = 100.0
        elif "[ExtractAudio]" in line:
            job["phase"] = "Extracting audio"
            job["progress"] = 100.0
        elif "Deleting original" in line:
            job["phase"] = "Cleaning up"

    return proc.wait(), state["kill_reason"]


def _should_retry_without_cookies(log_lines):
    """True if the failure signature suggests YouTube served only SABR
    or 403-HLS to the cookie-bearing clients. Non-cookie clients
    (tv_simply, ios) often work for these videos."""
    text = "\n".join(log_lines[-40:]).lower()
    has_hls_403 = ("fragment" in text and "not found" in text and "403" in text)
    sabr_empty = "downloaded file is empty" in text
    return has_hls_403 or sabr_empty


# When the cookie-based clients fail with SABR/HLS-403, retry with a
# broader set of non-cookie clients. tv_simply / ios sometimes fail
# (page-reload, missing po_token); tv and web_embedded occasionally
# succeed where they don't.
RETRY_NO_COOKIES_ARGS = ["--extractor-args", "youtube:player_client=tv,tv_simply,ios,web_embedded"]


def _po_token_required(log_lines):
    text = "\n".join(log_lines[-40:]).lower()
    return "po token" in text or "po_token" in text or "potoken" in text


def _needs_auth_signature(log_lines):
    """True if the failure looks like it needs a logged-in session — a
    bot-check, or a private / members-only / age-gated video. Used on the
    modern stack to decide whether adding cookies would help (vs. cookies
    being the thing that broke the download in the first place)."""
    text = "\n".join(log_lines[-40:]).lower()
    markers = (
        "sign in to confirm",
        "not a bot",
        "this video is private",
        "private video",
        "members-only",
        "members only",
        "join this channel",
        "age-restricted",
        "age restricted",
        "inappropriate for some users",
        "sign in to view",
        "login required",
        "requires authentication",
        "confirm your age",
    )
    return any(m in text for m in markers)


def _clean_partial_files(job_id):
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*")):
        try:
            os.remove(f)
        except OSError:
            pass


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    job["progress"] = 0.0
    job["phase"] = "Starting"
    job["log"] = []
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    try:
        if MODERN_YTDLP:
            # Modern stack (yt-dlp 2025.11+ with Deno): Deno solves YouTube's
            # bot challenge, so cookies are unnecessary AND often harmful —
            # they switch yt-dlp to authenticated clients (tv_downgraded)
            # whose adaptive-format URLs need a session-bound PO Token and
            # then 403 on the actual video-data download. So try WITHOUT
            # cookies first; only add them back if the video genuinely needs
            # a login (private / members-only / age-gated / bot check).
            cmd = _build_cmd(url, format_choice, format_id, out_template, EXTRA_ARGS, with_cookies=False)
            rc, kill_reason = _run_yt_dlp(cmd, job, job_id)

            if (
                rc != 0
                and not kill_reason
                and COOKIES_BROWSER
                and _needs_auth_signature(job["log"])
            ):
                notice = "--- ReClip: video needs a logged-in session, retrying with cookies ---"
                print(f"  [yt-dlp:{job_id}] {notice}", file=sys.stderr, flush=True)
                job["log"].append(notice)
                job["phase"] = "Retrying with cookies"
                job["progress"] = 0.0
                job.pop("speed", None)
                _clean_partial_files(job_id)
                cmd = _build_cmd(url, format_choice, format_id, out_template, EXTRA_ARGS, with_cookies=True)
                rc, kill_reason = _run_yt_dlp(cmd, job, job_id)
        else:
            # Legacy stack (old pip yt-dlp, no Deno): cookies are needed to
            # pass the bot check. Send them first, then retry WITHOUT cookies
            # using a broad client list if we hit the SABR/HLS-403 signature.
            cmd = _build_cmd(url, format_choice, format_id, out_template, EXTRA_ARGS, with_cookies=True)
            rc, kill_reason = _run_yt_dlp(cmd, job, job_id)

            if (
                rc != 0
                and not kill_reason
                and COOKIES_BROWSER
                and _should_retry_without_cookies(job["log"])
            ):
                notice = "--- ReClip: cookie-bearing clients hit SABR/HLS-403, retrying without cookies via tv,tv_simply,ios,web_embedded ---"
                print(f"  [yt-dlp:{job_id}] {notice}", file=sys.stderr, flush=True)
                job["log"].append(notice)
                job["phase"] = "Retrying without cookies"
                job["progress"] = 0.0
                job.pop("speed", None)
                _clean_partial_files(job_id)
                cmd = _build_cmd(url, format_choice, format_id, out_template, RETRY_NO_COOKIES_ARGS, with_cookies=False)
                rc, kill_reason = _run_yt_dlp(cmd, job, job_id)

        if kill_reason:
            job["status"] = "error"
            job["error"] = f"Download timed out ({kill_reason})"
            return
        if rc != 0:
            job["status"] = "error"
            if _po_token_required(job["log"]):
                job["error"] = (
                    "YouTube requires a PO Token for this video — yt-dlp can't extract it "
                    "automatically. See the yt-dlp PO Token Guide for a workaround."
                )
            else:
                err_line = next(
                    (l for l in reversed(job["log"]) if l.startswith("ERROR")),
                    job["log"][-1] if job["log"] else "yt-dlp failed",
                )
                job["error"] = err_line.strip()
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        # For video downloads, re-encode to H.264 if the source codec is
        # something QuickTime can't decode (VP9/AV1). Threads/IG content
        # routinely lands here; YouTube usually doesn't because the -S
        # preference already picked H.264.
        if format_choice != "audio":
            codec = _probe_video_codec(chosen)
            if codec and codec not in QUICKTIME_FRIENDLY_CODECS:
                _transcode_to_h264(chosen, job, job_id, codec)

        job["status"] = "done"
        job["progress"] = 100.0
        job["phase"] = "Done"
        job["file"] = chosen
        ext = os.path.splitext(chosen)[1]
        title = job.get("title", "").strip()
        if title:
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:20].strip()
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = [YTDLP, "--no-playlist", "-j", url] + js_runtime_args() + cookie_args() + EXTRA_ARGS
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": f["format_id"],
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "progress": job.get("progress"),
        "phase": job.get("phase"),
        "speed": job.get("speed"),
    })


@app.route("/api/log/<job_id>")
def get_log(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"lines": job.get("log", [])})


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
