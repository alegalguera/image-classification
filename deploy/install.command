#!/bin/bash
# Photo Sorter installer for macOS. Double-click it: it says what it will install and where, then asks before
# starting. Running it again updates the app. To remove everything later, double-click uninstall.command.
# Google Drive links (not needed for Google Drive for Desktop folders) need a sign-in file: put client_secret.json
# next to this installer before sharing it, and it is set up too.

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
APP_DIR="$HOME/.local/share/uv/tools/photo-sorter"
HF_DIR="${HF_HOME:-$HOME/.cache/huggingface}"
MODEL_DIR="$HF_DIR/hub/models--google--siglip2-base-patch16-224"
CONF_DIR="${PHOTO_SORTER_HOME:-$HOME/.config/photo-sorter}"
LAUNCHER="$HOME/Desktop/Photo Sorter.command"
LOG="$HOME/Library/Logs/photo-sorter-install.log"

if [ -t 1 ]; then    # colours and animation only in a real Terminal window
  B=$'\033[1m'; D=$'\033[2m'; T=$'\033[38;5;37m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; X=$'\033[0m'
else
  B=; D=; T=; G=; R=; Y=; X=
fi
LINE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
short() { echo "${1/#$HOME/~}"; }                       # /Users/you/... -> ~/...
ok() { printf "  ${G}✓${X} %-22s ${D}%s${X}\n" "$1" "$2"; }
fail() {
  printf "\n  ${R}${B}✗ %s${X}\n  The details are in ${B}%s${X}\n" "$1" "$(short "$LOG")"
  printf "  Send that file, or a screenshot of this window, to whoever helps you with Photo Sorter.\n\n"
  exit 1
}
hub_kb() { du -sk "$HF_DIR" 2>/dev/null | awk '{print $1 + 0}'; }
model_mb() {   # what has arrived so far
  local mb=$(( ($(hub_kb) - HUB_START) / 1024 ))
  if [ "$mb" -gt 0 ]; then echo "$mb MB of about 1,500 MB"; else echo "connecting to huggingface.co..."; fi
}
spin() {   # spin "message" command...: runs the command quietly (output goes to the log) with a spinner and a timer
  local msg="$1" i=0 start=$SECONDS extra="" frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  shift
  "$@" >>"$LOG" 2>&1 &
  local pid=$!
  if [ -t 1 ]; then
    while kill -0 "$pid" 2>/dev/null; do
      local t=$((SECONDS - start))
      if [ -n "$SPIN_EXTRA" ] && [ $((i % 10)) -eq 0 ]; then extra="$($SPIN_EXTRA)"; fi
      printf "\r\033[K  ${T}%s${X} %s ${D}%d:%02d  %s${X}" "${frames[i % 10]}" "$msg" $((t / 60)) $((t % 60)) "$extra"
      i=$((i + 1)); sleep 0.1
    done
    printf "\r\033[K"
  fi
  wait "$pid"
}

mkdir -p "$(dirname "$LOG")" && : > "$LOG"
FREE_GB=$(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}')
[ -t 1 ] && clear 2>/dev/null
printf "\n  ${T}%s${X}\n" "$LINE"
printf "   ${T}><(((('>${X}   ${B}PHOTO SORTER${X}  ·  installer\n"
printf "   Sorts research photos into folders, right here on this Mac.\n"
printf "  ${T}%s${X}\n\n" "$LINE"

printf "  ${B}What it will do${X}\n\n"
if [ -d "$APP_DIR" ]; then
  printf "   ${T}1${X}  Update the app                                   about 1 GB\n"
else
  printf "   ${T}1${X}  Install the app                                  about 1 GB\n"
fi
printf "      ${D}from pypi.org  →  %s${X}\n" "$(short "$APP_DIR")"
if ! command -v uv >/dev/null 2>&1; then
  printf "      ${D}first it installs uv, the free tool that installs apps like this one (from astral.sh)${X}\n"
fi
if [ -d "$MODEL_DIR" ]; then
  printf "   ${T}2${X}  Image model (SigLIP 2, by Google)                already here ✓\n"
else
  printf "   ${T}2${X}  Download the image model (SigLIP 2, by Google)   1.4 GB\n"
fi
printf "      ${D}from huggingface.co  →  %s${X}\n" "$(short "$MODEL_DIR")"
printf "   ${T}3${X}  Put a \"Photo Sorter\" icon on your Desktop\n"
printf "      ${D}→  %s${X}\n\n" "$(short "$LAUNCHER")"
printf "  Your photos are never uploaded: Photo Sorter runs on this Mac.\n"
if [ "${FREE_GB:-99}" -lt 4 ]; then
  printf "  ${Y}Free space: %s GB. About 3 GB are needed; free up some space first if you can.${X}\n" "$FREE_GB"
else
  printf "  ${D}Free space: %s GB (about 3 GB needed) ✓${X}\n" "$FREE_GB"
fi
printf "  ${D}A log of every step is kept in %s${X}\n\n" "$(short "$LOG")"
read -r -p "  Press Enter to start, or close this window to cancel. " _
echo

# 1. the app ---------------------------------------------------------------------------------------------
if pgrep -f "photo-sorter serve" >/dev/null 2>&1; then
  pkill -f "photo-sorter serve" && ok "Closed Photo Sorter" "it restarts with the new version at the end"
fi
if ! command -v uv >/dev/null 2>&1; then
  spin "Installing uv, the tool that installs the app" sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh" \
    || fail "Could not install uv. Check the internet connection and run the installer again."
  ok "uv installed" "$(short "$HOME/.local/bin/uv")"
fi
spin "Installing the app (a few minutes the first time)" \
  uv tool install --force --reinstall-package photo-sorter --python 3.12 ".[researcher]" \
  || fail "Installing the app failed."
ok "App installed" "$(short "$APP_DIR")"
if [ -f "$HERE/client_secret.json" ]; then
  mkdir -p "$CONF_DIR" && cp "$HERE/client_secret.json" "$CONF_DIR/" && ok "Google sign-in file" "$(short "$CONF_DIR")"
fi

# 2. the image model ----------------------------------------------------------------------------------------
if [ -d "$MODEL_DIR" ]; then
  spin "Checking the image model" sh -c 'cd "$HOME" && photo-sorter setup'
else
  HUB_START=$(hub_kb)
  SPIN_EXTRA=model_mb spin "Downloading the image model" sh -c 'cd "$HOME" && photo-sorter setup'
fi || fail "Getting the image model failed. Check the internet connection and run the installer again."
SAVED="$(grep -o 'saved in .*' "$LOG" | tail -1)"
ok "Image model ready" "${SAVED#saved in }"

# 3. the Desktop icon -------------------------------------------------------------------------------------
cat > "$LAUNCHER" <<'EOS'
#!/bin/bash
# Starts Photo Sorter and opens it in your browser. Keep this window open while you work: closing it stops Photo Sorter.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME" && photo-sorter serve --open
EOS
chmod +x "$LAUNCHER" || fail "Could not create the Desktop icon."
ok "Desktop icon" "$(short "$LAUNCHER")"

printf "\n  ${T}%s${X}\n" "$LINE"
printf "   ${G}${B}✓ Photo Sorter is installed${X}\n\n"
printf "   ${B}Start it${X}    double-click \"Photo Sorter\" on your Desktop\n"
printf "   ${B}Update it${X}   run this installer again\n"
printf "   ${B}Remove it${X}   double-click uninstall.command, next to this installer\n"
printf "  ${T}%s${X}\n\n" "$LINE"
printf "  Opening Photo Sorter now: it shows up in your browser. Keep its small window open while you work.\n\n"
open "$LAUNCHER"
