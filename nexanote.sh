#!/bin/bash
NEXANOTE_DIR="$HOME/NexaNote"
APP_DIR="$NEXANOTE_DIR/app"
VENV="$NEXANOTE_DIR/venv/bin/activate"
LOG_FILE="/tmp/nexanote_backend.log"

if ! curl -s http://127.0.0.1:8766/health > /dev/null 2>&1; then
  echo "🚀 Démarrage du backend..."
  source "$VENV"
  cd "$NEXANOTE_DIR"
  python main.py > "$LOG_FILE" 2>&1 &
  BACKEND_PID=$!
  echo -n "⏳ Attente"
  for i in $(seq 1 20); do
    sleep 0.5
    echo -n "."
    if curl -s http://127.0.0.1:8766/health > /dev/null 2>&1; then
      echo " ✅"
      break
    fi
  done
else
  echo "✅ Backend déjà en marche"
fi

cd "$APP_DIR"
flutter run -d linux
kill $BACKEND_PID 2>/dev/null
