FROM python:3.11-slim

WORKDIR /app

# Build context is the repo root (fly.toml lives there, and flyctl builds
# from its directory) — these paths are relative to that root, not to
# backend/, even though this Dockerfile itself lives in backend/.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

# DATABASE_URL points at the persistent volume in production (see fly.toml) —
# there is no default seed data baked into the image, the real finanzas.db
# is uploaded once to the volume (see DEPLOY.md).
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
