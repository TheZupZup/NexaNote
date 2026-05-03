#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXANOTE_DIR="$SCRIPT_DIR"
APP_DIR="$NEXANOTE_DIR/app"
VENV_ACTIVATE="$NEXANOTE_DIR/venv/bin/activate"
LOG_FILE="/tmp/nexanote_backend.log"
BACKEND_PID=""
CONFIG_FILE="$HOME/.nexanote-config"

# Lire le dossier de données depuis ~/.nexanote-config (si existant)
DATA_DIR="$HOME/.nexanote"
if [[ -f "$CONFIG_FILE" ]]; then
  SAVED_DIR=$(grep -E '^data_dir=' "$CONFIG_FILE" | cut -d'=' -f2-)
  if [[ -n "$SAVED_DIR" ]]; then
    DATA_DIR="$SAVED_DIR"
  fi
fi

cleanup() {
  if [[ -n "${BACKEND_PID}" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl -s http://127.0.0.1:8766/health >/dev/null 2>&1; then
  echo "🚀 Démarrage du backend..."
  echo "📂 Dossier de données : $DATA_DIR"
  if [[ -f "$VENV_ACTIVATE" ]]; then
    # shellcheck source=/dev/null
    source "$VENV_ACTIVATE"
  else
    echo "⚠️  venv introuvable ($VENV_ACTIVATE), utilisation du Python système"
  fi

  cd "$NEXANOTE_DIR"
  python main.py --data-dir "$DATA_DIR" >"$LOG_FILE" 2>&1 &
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
