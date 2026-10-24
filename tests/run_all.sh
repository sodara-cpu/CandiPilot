#!/bin/sh
# Lance les trois séries de tests sur un serveur temporaire en robot simulé (aucun envoi réel).
# Utilisé en local et par l'intégration continue GitHub (.github/workflows/ci.yml).
#     ./tests/run_all.sh            (PY=python ./tests/run_all.sh pour un autre interpréteur)
set -e
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
PORT=${PORT:-8765}
TMP=$(mktemp -d)
export CANDIPILOT_FAKE_ROBOT=1 CANDIPILOT_DATA="$TMP/data"

$PY -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > "$TMP/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$TMP"' EXIT
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/api/openapi.json" > /dev/null && break
  sleep 0.5
done

echo "== API";       $PY tests/test_api.py "http://127.0.0.1:$PORT"
echo "== Parcours";  $PY tests/test_workflow.py "http://127.0.0.1:$PORT" "$TMP/server.log"
echo "== Interface"; $PY tests/test_ui.py "http://127.0.0.1:$PORT" "$TMP/ui"
echo "== JobTeaser"; $PY tests/test_jobteaser.py "http://127.0.0.1:$PORT"
echo "Tous les tests sont passés."
