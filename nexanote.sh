#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXANOTE_DIR="$SCRIPT_DIR"
APP_DIR="$NEXANOTE_DIR/app"
VENV_ACTIVATE="$NEXANOTE_DIR/venv/bin/activate"
LOG_FILE="/tmp/nexanote_backend.log"
BACKEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl -s http://127.0.0.1:8766/health >/dev/null 2>&1; then
  echo "🚀 Démarrage du backend..."
  if [[ -f "$VENV_ACTIVATE" ]]; then
    # shellcheck source=/dev/null
    source "$VENV_ACTIVATE"
  else
    echo "⚠️  venv introuvable ($VENV_ACTIVATE), utilisation du Python système"
  fi

  cd "$NEXANOTE_DIR"
  python main.py >"$LOG_FILE" 2>&1 &
  BACKEND_PID=$!

  echo -n "⏳ Attente"
  for _ in $(seq 1 20); do
    sleep 0.5
    echo -n "."
    if curl -s http://127.0.0.1:8766/health >/dev/null 2>&1; then
      echo " ✅"
      break
    fi
  done
else
  echo "✅ Backend déjà en marche"
fi

cd "$APP_DIR"
echo "📦 Mise à jour des dépendances Flutter..."
flutter pub get
GDK_BACKEND=x11 flutter run -d linux
