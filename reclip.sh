#!/bin/bash
set -e
cd "$(dirname "$0")"

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

echo ""
echo "  ReClip is running at http://localhost:$PORT"
echo ""
python3 app.py
