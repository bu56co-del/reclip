#!/bin/bash
# Double-clickable launcher for ReClip on macOS.
#
# Why this exists alongside ReClip.app:
#   When the repo lives in a TCC-protected folder (Desktop, Documents,
#   Downloads, iCloud Drive), macOS blocks unsigned .app bundles from
#   executing shell scripts there — you'd see "Operation not permitted".
#   Terminal.app already has the necessary permissions, so double-clicking
#   this .command file routes the launch through Terminal and works.
#
# Trade-off: a Terminal window stays open while ReClip runs.
cd "$(dirname "$0")"
exec ./reclip-gui.sh
