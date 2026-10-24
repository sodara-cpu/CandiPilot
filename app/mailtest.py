"""Vérifie l'envoi des e-mails (réglages SMTP du fichier .env).

    set -a; . ./.env; set +a; .venv/bin/python -m app.mailtest destinataire@exemple.com
"""
import sys

from .auth import send_mail

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage : python -m app.mailtest destinataire@exemple.com")
    try:
        sent = send_mail(sys.argv[1], "CandiPilot : test d'envoi", "Si tu lis ce message, les e-mails de CandiPilot fonctionnent.")
    except Exception as e:
        sys.exit(f"Échec de l'envoi : {e}\nVérifie CANDIPILOT_SMTP_USER et CANDIPILOT_SMTP_PASSWORD dans .env.")
    print("E-mail envoyé." if sent else "SMTP non configuré : remplis .env (voir .env.example).")
