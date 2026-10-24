# CandiPilot — serveur web (Render). Le robot tourne dans l'extension de chaque utilisateur ;
# Chromium ne sert ici qu'à fabriquer les lettres de motivation en PDF.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright CANDIPILOT_ROBOT=extension
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt && python -m playwright install --with-deps chromium

COPY app app
COPY src src
COPY config/profile.example.yaml config/profile.example.yaml
COPY extension extension

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
