#!/bin/bash
set -e
cd "$(dirname "$0")"

# Load persistent settings (COOKIES_BROWSER, PORT, etc.) from ~/.reclip-env
# if present, so double-clicked launchers pick them up without needing
# environment variables set in the shell.
if [ -f "$HOME/.reclip-env" ]; then
    set -a
    source "$HOME/.reclip-env"
    set +a
fi

# Only python3 is required up front — yt-dlp and ffmpeg are installed
# into the venv below (no admin needed) if not already on PATH.
if ! command -v python3 &> /dev/null; then
    echo "Missing required tool: python3"
    if command -v brew &> /dev/null; then
        echo "Install with:  brew install python3"
    elif command -v apt &> /dev/null; then
        echo "Install with:  sudo apt install python3 python3-venv"
    else
        echo "Please install python3 (>= 3.8)"
    fi
    exit 1
fi

# Set up venv and install Python deps (flask, yt-dlp)
if [ ! -d "venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    # Upgrade pip first — venvs ship with whatever pip the base Python had,
    # which on older systems (e.g. macOS Python 3.9) is too old to honour
    # yanked release markers and ends up building broken sdists.
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
else
    source venv/bin/activate
fi

# Resolve ffmpeg: prefer a system install, otherwise pip-install a
# static binary via imageio-ffmpeg (no admin required).
if command -v ffmpeg &> /dev/null; then
    FFMPEG_PATH="$(command -v ffmpeg)"
else
    if ! python3 -c "import imageio_ffmpeg" &> /dev/null; then
        echo "System ffmpeg not found, installing static fallback (imageio-ffmpeg)..."
        pip install -q imageio-ffmpeg
    fi
    FFMPEG_PATH="$(python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
fi
export FFMPEG_PATH

PORT="${PORT:-8899}"
export PORT

if [ -n "$RECLIP_GUI" ]; then
    if ! python3 -c "import webview" &> /dev/null; then
        echo "Installing pywebview..."
        pip install -q pywebview
    fi
    echo ""
    echo "  ReClip GUI starting (closing the window stops the server)"
    if [ -n "$COOKIES_BROWSER" ]; then
        echo "  Using cookies from browser: $COOKIES_BROWSER"
    fi
    echo ""
    python3 reclip_gui.py
else
    echo ""
    echo "  ReClip is running at http://localhost:$PORT"
    if [ -n "$COOKIES_BROWSER" ]; then
        echo "  Using cookies from browser: $COOKIES_BROWSER"
    fi
    echo ""
    python3 app.py
fi
