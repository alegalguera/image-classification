#!/bin/bash
# Double-click installer for macOS. Installs photo-sorter with the local image model and puts a
# "Photo Sorter" launcher on the Desktop. If the researchers will paste Google Drive links, the admin puts the
# Google sign-in file client_secret.json next to this installer before sharing it (Drive for Desktop folders
# work without it). Running it again updates the app.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
LOG="$HOME/Library/Logs/photo-sorter-install.log"          # the full output, for troubleshooting
mkdir -p "$(dirname "$LOG")" && : > "$LOG"
step() { printf "\n\033[1m%s\033[0m\n" "$1"; }
ok() { printf "   \033[32m✓\033[0m %s\n" "$1"; }
fail() {
  printf "\n\033[31m✗ %s\033[0m\n   Details are in %s\n   Send that file, or a screenshot of this window, to whoever set up photo-sorter.\n" "$1" "$LOG"
  exit 1
}

echo "== photo-sorter installer =="
step "1/3  Installing the app (a few minutes the first time; it stays quiet while it works) ..."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh 2>>"$LOG" | sh >>"$LOG" 2>&1 || fail "Could not install uv, the helper that installs the app. Check the internet connection."
fi
uv tool install --force --reinstall-package photo-sorter --python 3.12 ".[researcher]" >>"$LOG" 2>&1 \
  || fail "Installing the app failed."
ok "installed"
CONF="${PHOTO_SORTER_HOME:-$HOME/.config/photo-sorter}"
if [ -f "$HERE/client_secret.json" ]; then
  mkdir -p "$CONF" && cp "$HERE/client_secret.json" "$CONF/" && ok "Google sign-in file installed"
fi

step "2/3  Getting the image model (about 1.5 GB, only the first time) ..."
(cd "$HOME" && photo-sorter setup) || fail "Getting the model failed. Check the internet connection and run the installer again."

step "3/3  Putting the Photo Sorter icon on your Desktop ..."
LAUNCHER="$HOME/Desktop/Photo Sorter.command"
cat > "$LAUNCHER" <<'EOS'
#!/bin/bash
# Starts photo-sorter and opens it in your browser. Closing this window stops it.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME" && photo-sorter serve --open
EOS
chmod +x "$LAUNCHER" || fail "Could not create $LAUNCHER"
ok "$LAUNCHER"

printf "\n\033[32m✓ photo-sorter is installed.\033[0m\n"
echo "  From now on, double-click \"Photo Sorter\" on your Desktop: it opens in your browser,"
echo "  and closing its Terminal window stops it. Starting it now ..."
open "$LAUNCHER"
