#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BIN="$SCRIPT_DIR/app/build/linux/x64/release/bundle/app"

if [[ ! -x "$APP_BIN" ]]; then
  echo "Binaire Flutter introuvable: $APP_BIN"
  echo "Compilez d'abord l'app avec:"
  echo "  cd $SCRIPT_DIR/app && flutter build linux --release"
  exit 1
fi

GDK_BACKEND=x11 "$APP_BIN"
