#!/bin/bash
# Double-click this file in Finder to run the V-JEPA transcribe pipeline.
# Locates the repo automatically, activates the venv, then invokes the
# Python entry point with the Meta blog URL.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

URL="https://ai.meta.com/blog/v-jepa-yann-lecun-ai-model-video-joint-embedding-predictive-architecture/"

echo "ReClip repo: $REPO_DIR"
if [ ! -d venv ]; then
    echo "Setting up Python venv (one-time)..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
else
    source venv/bin/activate
fi

echo
echo "Running transcribe_page.py for V-JEPA..."
python3 scripts/transcribe_page.py "$URL"

OUTDIR="$HOME/Downloads/transcribe-ai-meta-com"
echo
echo "Opening output folder in Finder..."
open "$OUTDIR" 2>/dev/null || true

TRANSCRIPT="$OUTDIR/full_transcript.txt"
if [ -f "$TRANSCRIPT" ]; then
    echo
    echo "✅ Transcript ready: $TRANSCRIPT"
    echo "   Copying to clipboard..."
    pbcopy < "$TRANSCRIPT" 2>/dev/null && echo "   → clipboard now holds the full transcript. Paste it back to Claude." \
                                      || echo "   (pbcopy unavailable; open the file manually.)"
fi
echo
echo "Press any key to close this window..."
read -n 1
