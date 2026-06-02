#!/bin/bash
# Launch ReClip in a native pywebview window instead of the browser.
# Reuses reclip.sh for venv + ffmpeg setup.
RECLIP_GUI=1 exec "$(dirname "$0")/reclip.sh" "$@"
