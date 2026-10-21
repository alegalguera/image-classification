#!/bin/bash
# Removes Photo Sorter from this Mac: the app, the image model, the Desktop icon and its settings.
# Your photos, their folders and their category lists (CATEGORIES.md) are never touched. Open Terminal and paste:
#
#   curl -LsSf https://raw.githubusercontent.com/alegalguera/image-classification/main/uninstall.sh | bash
#
# PHOTO_SORTER_UNATTENDED=1 skips the question (for automation).

APP_DIR="$HOME/.local/share/uv/tools/photo-sorter"
MODEL_DIR="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--google--siglip2-base-patch16-224"
CONF_DIR="${PHOTO_SORTER_HOME:-$HOME/.config/photo-sorter}"
LAUNCHER="$HOME/Desktop/Photo Sorter.command"
LOG="$HOME/Library/Logs/photo-sorter-install.log"
LINE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; T=$'\033[38;5;37m'; G=$'\033[32m'; X=$'\033[0m'
else
  B=; D=; T=; G=; X=
fi
short() { echo "${1/#$HOME/~}"; }
size() { [ -e "$1" ] && du -shL "$1" 2>/dev/null | awk '{print $1}' || echo "not there"; }
ok() { printf "  ${G}✓${X} %-22s ${D}%s${X}\n" "$1" "$2"; }

main() {
  [ -n "$HOME" ] || exit 1
  export PATH="$HOME/.local/bin:$PATH"
  [ -t 1 ] && clear 2>/dev/null
  printf "\n  ${T}%s${X}\n" "$LINE"
  printf "   ${T}><(((('>${X}   ${B}PHOTO SORTER${X}  ·  remove\n"
  printf "  ${T}%s${X}\n\n" "$LINE"
  printf "  ${B}This removes${X}\n\n"
  printf "   the app                     %-10s ${D}%s${X}\n" "$(size "$APP_DIR")" "$(short "$APP_DIR")"
  printf "   the image model             %-10s ${D}%s${X}\n" "$(size "$MODEL_DIR/snapshots")" "$(short "$MODEL_DIR")"
  printf "   the Desktop icon            %-10s ${D}%s${X}\n" "" "$(short "$LAUNCHER")"
  printf "   settings, Google sign-in    %-10s ${D}%s${X}\n\n" "" "$(short "$CONF_DIR")"
  printf "  Your photos, their folders and their category lists stay exactly where they are.\n\n"
  if [ -z "$PHOTO_SORTER_UNATTENDED" ] && [ -r /dev/tty ]; then
    read -r -p "  Press Enter to remove Photo Sorter, or Control-C to cancel. " _ < /dev/tty
    echo
  fi

  pkill -f "photo-sorter serve" 2>/dev/null && ok "Closed Photo Sorter" ""
  command -v photo-sorter >/dev/null 2>&1 && (cd "$HOME" && photo-sorter setup --remove-model >/dev/null 2>&1 </dev/null)
  command -v uv >/dev/null 2>&1 && uv tool uninstall photo-sorter >/dev/null 2>&1 </dev/null
  rm -rf "$APP_DIR" "$HOME/.local/bin/photo-sorter"
  ok "App removed" "$(short "$APP_DIR")"
  rm -rf "$MODEL_DIR" && ok "Image model removed" "$(short "$MODEL_DIR")"
  rm -f "$LAUNCHER" && ok "Desktop icon removed" "$(short "$LAUNCHER")"
  rm -rf "$CONF_DIR" && ok "Settings removed" "$(short "$CONF_DIR")"
  rm -f "$LOG"

  printf "\n  ${G}${B}✓ Photo Sorter is removed.${X}\n"
  printf "  ${D}uv, the tool that installed it, stays in %s because other apps may use it.${X}\n" "$(short "$HOME/.local/bin/uv")"
  printf "  ${D}To remove it too: rm ~/.local/bin/uv ~/.local/bin/uvx${X}\n\n"
}

main "$@"
