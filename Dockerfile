FROM python:3.12-slim

WORKDIR /srv

# Версия релиза, прокидывается сборкой из тега (см. .github/workflows/release.yml).
# Локальная сборка (docker compose up --build) оставляет значение по умолчанию — "dev".
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY webapp ./webapp
COPY migrations ./migrations
COPY alembic.ini .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
