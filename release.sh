#!/bin/sh
# Mise en production, toujours dans le même ordre :
#   1. tests  2. version locale (http://127.0.0.1:8000)  3. GitHub (develop puis main)  4. Render, puis vérification.
# Render ne se déclenche pas tout seul depuis GitHub : le déploiement passe par son API (RENDER_API_KEY dans .env).
set -e
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }
SERVICE=srv-dbel5cnb9u1s73a3md30
SITE=https://candipilot.onrender.com

echo "== 1. Tests"
./tests/run_all.sh > /tmp/candipilot-tests.log 2>&1 || { tail -30 /tmp/candipilot-tests.log; exit 1; }
grep -E "réussies|passés" /tmp/candipilot-tests.log

echo "== 2. Version locale"
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8000" 2>/dev/null && sleep 2 || true
nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > data/server.log 2>&1 &
sleep 4
curl -sf http://127.0.0.1:8000/healthz > /dev/null && echo "http://127.0.0.1:8000 à jour"

echo "== 3. GitHub"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git push -q origin "$BRANCH"
git checkout -q main && git merge --ff-only -q "$BRANCH" && git push -q origin main && git checkout -q "$BRANCH"
echo "main = $(git rev-parse --short main)"

echo "== 4. Render"
[ -n "$RENDER_API_KEY" ] || { echo "RENDER_API_KEY manquant dans .env"; exit 1; }
DEP=$(curl -s -X POST -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
  -d '{"clearCache":"do_not_clear"}' "https://api.render.com/v1/services/$SERVICE/deploys" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
while :; do
  ST=$(curl -s -H "Authorization: Bearer $RENDER_API_KEY" "https://api.render.com/v1/services/$SERVICE/deploys/$DEP" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  case $ST in live) break ;; *failed*|canceled|deactivated) echo "Déploiement $ST"; exit 1 ;; esac
  sleep 20
done
curl -sf "$SITE/healthz?db_check=1" > /dev/null && echo "$SITE en ligne ($(git rev-parse --short main))"
