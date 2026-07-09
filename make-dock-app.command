#!/bin/bash
# Build a Dock-able ReClip.app in ~/Applications that launches the native
# GUI with no Terminal window. Double-click this file once, then drag
# ~/Applications/ReClip.app into your Dock.
#
# Why the app lives in ~/Applications and not next to this repo:
# macOS TCC blocks an unsigned .app located in a protected folder
# (Desktop / Documents / Downloads / iCloud) from even executing its own
# launcher — that's the silent "Operation not permitted" you hit before.
# Keeping the .app outside those folders sidesteps that; it then just
# READS + runs the repo scripts, which triggers a normal one-time
# "allow access to your Desktop folder" prompt (no admin needed).
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications"
APP="$APP_DIR/ReClip.app"
mkdir -p "$APP_DIR"

echo "Repo: $REPO_DIR"
echo "App : $APP"
echo ""

if ! command -v osacompile >/dev/null 2>&1; then
    echo "osacompile not found — this script only runs on macOS."
    echo "Press any key to close..."; read -n 1; exit 1
fi

# AppleScript launcher. Notes:
#  - `/bin/bash reclip.sh` READS the script (vs. exec-ing a Desktop file
#    by its shebang), which is the TCC-friendly path.
#  - RECLIP_GUI=1 selects the pywebview window instead of the browser.
#  - PATH is widened so a Homebrew python3 is found outside a login shell.
#  - Output is redirected to reclip.log and the job is backgrounded with
#    `&` so `do shell script` returns immediately (the applet quits; the
#    ReClip window stays open and closing it stops the server).
TMP="$(mktemp -t reclip-app).applescript"
cat > "$TMP" <<APPLESCRIPT
do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin; cd " & quoted form of "$REPO_DIR" & " && RECLIP_GUI=1 /bin/bash reclip.sh > reclip.log 2>&1 &"
APPLESCRIPT

rm -rf "$APP"
osacompile -o "$APP" "$TMP"
rm -f "$TMP"

echo ""
echo "Built $APP"
echo ""
echo "Next:"
echo "  1. Double-click ReClip.app in ~/Applications (Finder > Go > Applications,"
echo "     or press Cmd-Shift-A). Approve the Desktop-access prompt if it appears."
echo "  2. Drag ReClip.app from ~/Applications into your Dock."
echo ""
echo "To give it a custom icon: select an image, Cmd-C, then Get Info (Cmd-I) on"
echo "ReClip.app, click the small icon top-left, Cmd-V."
echo ""
echo "Press any key to close..."
read -n 1
