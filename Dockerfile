# =============================================================================
# Drop Monitor — image de production (API + dashboard + moteur + captures)
#
# Un seul conteneur héberge tout : FastAPI sert l'API REST, le WebSocket et
# le frontend compilé, et le moteur de surveillance tourne dans la même
# boucle asyncio. Aucun service annexe (ni Redis, ni worker séparé).
#
# Build : docker build -t drop-monitor .
# Run   : docker run -p 8000:8000 --env-file .env -v dropmon:/data drop-monitor
# =============================================================================

# --- Étape 1 : compilation du dashboard React -------------------------------
FROM node:20-alpine AS frontend

WORKDIR /build
# Les dépendances d'abord : cette couche est mise en cache tant que les
# fichiers de lock ne changent pas.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# --- Étape 2 : exécution -----------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app

# curl : nécessaire au HEALTHCHECK ci-dessous.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + ses bibliothèques système, installés à un emplacement partagé
# et lisible par l'utilisateur non privilégié.
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /var/lib/apt/lists/*

# Code applicatif (couches les plus volatiles en dernier).
COPY main.py server.py ./
COPY src/ ./src/
COPY plugins/ ./plugins/
COPY config/ ./config/
COPY --from=frontend /build/dist ./frontend/dist

# Exécution sans privilèges ; /data est le point de montage du volume.
RUN useradd --create-home --uid 10001 dropmon \
    && mkdir -p /data/screenshots /data/logs \
    && chown -R dropmon:dropmon /app /data
USER dropmon

ENV PORT=8000 \
    DATA_DIR=/data \
    LOG_DIR=/data/logs \
    SCREENSHOTS_DIR=/data/screenshots

EXPOSE 8000

# Sonde interne : complète le healthcheck Railway (voir railway.json).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/v1/health" || exit 1

CMD ["python", "server.py"]
