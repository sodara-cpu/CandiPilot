#!/bin/sh
# Met CandiPilot en ligne depuis ce Mac : adresse https publique via un tunnel
# Cloudflare gratuit (sans compte). Le lien marche tant que ce script tourne.
#
#   ./deploy.sh            production (branche main) : vrai robot, port 8000, données dans data/
#   ./deploy.sh recette    recette (branche develop) : robot simulé, port 8001, données dans data-recette/
#
# E-mails (mot de passe oublié) : définir CANDIPILOT_SMTP_HOST, _PORT, _USER, _PASSWORD, _FROM ;
# sans SMTP, les liens sont écrits dans <dossier de données>/server.log.
cd "$(dirname "$0")"
ENV=${1:-production}
case "$ENV" in
  production) PORT=8000; DATA=data;         FAKE=0; BRANCH=main ;;
  recette)    PORT=8001; DATA=data-recette; FAKE=1; BRANCH=develop ;;
  *) echo "Usage : ./deploy.sh [production|recette]"; exit 1 ;;
esac
CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$CURRENT" != "$BRANCH" ] && echo "Attention : la $ENV se déploie depuis la branche $BRANCH (branche actuelle : $CURRENT)."

mkdir -p "$DATA"
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port $PORT" 2>/dev/null && sleep 1

cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > "$DATA/tunnel.log" 2>&1 &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null' EXIT INT TERM

URL=""
for _ in $(seq 1 30); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$DATA/tunnel.log" | head -1)
  [ -n "$URL" ] && break
  sleep 1
done
[ -z "$URL" ] && { echo "Le tunnel n'a pas démarré, voir $DATA/tunnel.log"; exit 1; }

CANDIPILOT_DATA="$PWD/$DATA" CANDIPILOT_FAKE_ROBOT=$FAKE CANDIPILOT_SECURE_COOKIE=1 CANDIPILOT_PUBLIC_URL="$URL" \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > "$DATA/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER $TUNNEL 2>/dev/null' EXIT INT TERM

echo ""
echo "CandiPilot ($ENV) est en ligne : $URL"
[ "$FAKE" = 1 ] && echo "Robot simulé : aucune candidature réelle n'est envoyée."
echo "(Ctrl+C pour arrêter ; le lien change à chaque lancement)"
wait $TUNNEL
