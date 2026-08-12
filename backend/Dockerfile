FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# DATABASE_URL points at the persistent volume in production (see fly.toml) —
# there is no default seed data baked into the image, the real finanzas.db
# is uploaded once to the volume (see DEPLOY.md).
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
