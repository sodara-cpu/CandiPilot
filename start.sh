#!/bin/sh
# Lance CandiPilot sur http://127.0.0.1:8000
# Ajoute CANDIPILOT_FAKE_ROBOT=1 devant pour une démonstration sans envoi réel.
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }   # réglages privés (e-mails…)
echo "CandiPilot : ouvre http://127.0.0.1:8000 (Ctrl+C pour arrêter)"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
