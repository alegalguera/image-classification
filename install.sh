#!/bin/bash
# ReeFile installer for macOS. Open Terminal and paste:
#
#   curl -LsSf https://raw.githubusercontent.com/alegalguera/image-classification/main/install.sh | bash
#
# It says what it will install and where, and asks before starting. Running it again updates ReeFile.
# Everything goes into your user folder: no administrator password, and nothing to install first, because
# uv (the free tool it installs ReeFile with) brings its own copy of Python. To remove it: uninstall.sh.
#
# For developers and automation:
#   PHOTO_SORTER_SOURCE=<folder or URL>   install from a local copy (or another archive) instead of GitHub
#   PHOTO_SORTER_UNATTENDED=1             don't ask and don't open the app at the end
# A Google sign-in file (client_secret.json) at the top of a local source folder is installed too.

REPO="https://github.com/alegalguera/image-classification"
RAW="https://raw.githubusercontent.com/alegalguera/image-classification/main"
APP_DIR="$HOME/.local/share/uv/tools/photo-sorter"
HF_DIR="${HF_HOME:-$HOME/.cache/huggingface}"
MODEL_DIR="$HF_DIR/hub/models--google--siglip2-base-patch16-224"
CONF_DIR="${PHOTO_SORTER_HOME:-$HOME/.config/photo-sorter}"
LAUNCHER="$HOME/Desktop/ReeFile.command"
OLD_LAUNCHER="$HOME/Desktop/Photo Sorter.command"     # from versions before the ReeFile name
LOG="$HOME/Library/Logs/photo-sorter-install.log"
LINE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -t 1 ]; then    # colours and animation only in a real Terminal window
  B=$'\033[1m'; D=$'\033[2m'; T=$'\033[38;5;37m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; X=$'\033[0m'
else
  B=; D=; T=; G=; R=; Y=; X=
fi

short() { echo "${1/#$HOME/~}"; }                       # /Users/you/... -> ~/...
ok() { printf "  ${G}✓${X} %-22s ${D}%s${X}\n" "$1" "$2"; }
fail() {
  printf "\n  ${R}${B}✗ %s${X}\n" "$1"
  if [ -s "$LOG" ]; then
    printf "  ${D}Last lines of the log:${X}\n"
    tail -n 6 "$LOG" | cut -c1-160 | sed "s/^/     ${D}/; s/$/${X}/"
    printf "  The full details are in ${B}%s${X}\n" "$(short "$LOG")"
  fi
  printf "  Send that file, or a screenshot of this window, to whoever helps you with ReeFile.\n\n"
  exit 1
}
hf_kb() { du -sk "$HF_DIR" 2>/dev/null | awk '{print $1 + 0}'; }
model_mb() {   # how much of the model has arrived so far
  local mb=$(( ($(hf_kb) - HF_START) / 1024 ))
  if [ "$mb" -gt 0 ]; then echo "$mb MB of about 1,500 MB"; else echo "connecting to huggingface.co..."; fi
}
spin() {   # spin "message" command...: runs the command quietly (output goes to the log) with a spinner and a timer
  local msg="$1" i=0 start=$SECONDS extra="" frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  shift
  "$@" >>"$LOG" 2>&1 </dev/null &
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

main() {
  [ "$(uname -s)" = "Darwin" ] || fail "This installer is for Macs. For Windows, see the README."
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" != "1" ]; then
    fail "This Mac has an Intel processor. ReeFile needs a Mac with an Apple chip (M1 or newer): PyTorch, the image-recognition library it uses, no longer supports Intel Macs."
  fi
  export PATH="$HOME/.local/bin:$PATH"
  export UV_PYTHON_PREFERENCE=only-managed     # always its own Python, never Homebrew's, conda's or the system's
  local src="${PHOTO_SORTER_SOURCE:-$REPO/archive/refs/heads/main.zip}" from="github.com"
  if [ -d "$src" ]; then
    src="$(cd "$src" && pwd)"
    from="$(short "$src")"
  fi
  mkdir -p "$(dirname "$LOG")" && : > "$LOG"
  local free_gb
  free_gb=$(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}')

  [ -t 1 ] && clear 2>/dev/null
  printf "\n  ${T}%s${X}\n" "$LINE"
  printf "   ${T}><(((('>${X}   ${B}ReeFile${X}  ·  installer\n"
  printf "   Sorts research photos into folders, right here on this Mac.\n"
  printf "  ${T}%s${X}\n\n" "$LINE"
  printf "  ${B}What it will do${X}\n\n"
  if [ -d "$APP_DIR" ]; then
    printf "   ${T}1${X}  Update the app                                   about 1 GB\n"
  else
    printf "   ${T}1${X}  Install the app                                  about 1 GB\n"
  fi
  printf "      ${D}from %s and pypi.org  →  %s${X}\n" "$from" "$(short "$APP_DIR")"
  if ! command -v uv >/dev/null 2>&1; then
    printf "      ${D}with uv, the free tool that installs it and brings its own Python (from astral.sh  →  ~/.local/bin)${X}\n"
  fi
  if [ -d "$MODEL_DIR" ]; then
    printf "   ${T}2${X}  Image model (SigLIP 2, by Google)                already here ✓\n"
  else
    printf "   ${T}2${X}  Download the image model (SigLIP 2, by Google)   1.4 GB\n"
  fi
  printf "      ${D}from huggingface.co  →  %s${X}\n" "$(short "$MODEL_DIR")"
  printf "   ${T}3${X}  Put a \"ReeFile\" icon on your Desktop\n"
  printf "      ${D}→  %s${X}\n\n" "$(short "$LAUNCHER")"
  printf "  Everything stays in your user folder: no administrator password, nothing else to install.\n"
  printf "  Your photos are never uploaded: ReeFile runs on this Mac.\n"
  if [ "${free_gb:-99}" -lt 4 ]; then
    printf "  ${Y}Free space: %s GB. About 3 GB are needed; free up some space first if you can.${X}\n" "$free_gb"
  else
    printf "  ${D}Free space: %s GB (about 3 GB needed) ✓${X}\n" "$free_gb"
  fi
  printf "  ${D}A log of every step is kept in %s${X}\n\n" "$(short "$LOG")"
  if [ -z "$PHOTO_SORTER_UNATTENDED" ] && [ -r /dev/tty ]; then
    read -r -p "  Press Enter to start, or Control-C to cancel. " _ < /dev/tty
    echo
  fi

  # 1. the app
  if pgrep -f "photo-sorter serve" >/dev/null 2>&1; then
    pkill -f "photo-sorter serve" && ok "Closed ReeFile" "it restarts with the new version at the end"
  fi
  if ! command -v uv >/dev/null 2>&1; then
    spin "Installing uv" sh -c "curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh" \
      || fail "Could not install uv. Check the internet connection and run the installer again."
    ok "uv installed" "$(short "$HOME/.local/bin/uv")"
  fi
  local spec="photo-sorter[researcher] @ $src"
  [ -d "$src" ] && spec="photo-sorter[researcher] @ file://$src"
  spin "Installing the app (a few minutes the first time)" \
    uv tool install --force --reinstall-package photo-sorter --python 3.12 "$spec" \
    || fail "Installing the app failed."
  ok "App installed" "$(short "$APP_DIR")"
  if [ -d "$src" ] && [ -f "$src/client_secret.json" ]; then
    mkdir -p "$CONF_DIR" && cp "$src/client_secret.json" "$CONF_DIR/" && ok "Google sign-in file" "$(short "$CONF_DIR")"
  fi

  # 2. the image model
  if [ -d "$MODEL_DIR" ]; then
    spin "Checking the image model" sh -c 'cd "$HOME" && photo-sorter setup'
  else
    HF_START=$(hf_kb)
    SPIN_EXTRA=model_mb spin "Downloading the image model" sh -c 'cd "$HOME" && photo-sorter setup'
  fi || fail "Getting the image model failed."
  local saved
  saved="$(grep -o 'saved in .*' "$LOG" | tail -1)"
  ok "Image model ready" "${saved#saved in }"

  # 3. the Desktop icon
  cat > "$LAUNCHER" <<'EOS'
#!/bin/bash
# Starts ReeFile and opens it in your browser. Keep this window open while you work: closing it stops ReeFile.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME" && photo-sorter serve --open
EOS
  chmod +x "$LAUNCHER" || fail "Could not create the Desktop icon."
  rm -f "$OLD_LAUNCHER"
  ok "Desktop icon" "$(short "$LAUNCHER")"

  printf "\n  ${T}%s${X}\n" "$LINE"
  printf "   ${G}${B}✓ ReeFile is installed${X}\n\n"
  printf "   ${B}Start it${X}    double-click \"ReeFile\" on your Desktop\n"
  printf "   ${B}Update it${X}   paste the same line again\n"
  printf "   ${B}Remove it${X}   paste:  ${D}curl -LsSf %s/uninstall.sh | bash${X}\n" "$RAW"
  printf "  ${T}%s${X}\n\n" "$LINE"
  if [ -z "$PHOTO_SORTER_UNATTENDED" ]; then
    printf "  Opening ReeFile now: it shows up in your browser. Keep its small window open while you work.\n\n"
    open "$LAUNCHER"
  fi
}

main "$@"
