FROM python:3.12-slim

WORKDIR /srv

# Версия релиза, прокидывается сборкой из тега (см. .github/workflows/release.yml).
# Локальная сборка (docker compose up --build) оставляет значение по умолчанию — "dev".
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Pango и шрифты — для WeasyPrint (PDF-отчёт, app/shared/pdf_report.py): Inter и DejaVu для
# текста, Noto Color Emoji — для иконок категорий
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 \
       fonts-inter fonts-dejavu-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY webapp ./webapp
COPY migrations ./migrations
COPY alembic.ini .

# Метка времени сборки — вычисляется прямо в момент сборки образа. Слой закэширован, пока не
# поменяется содержимое app/webapp (COPY выше) — значит метка реально обновляется только
# тогда, когда в образ попал новый код. Показывается в мини-аппе (правый верхний угол шапки) и
# в /healthz, чтобы на стенде было видно, подтянулась ли свежая сборка после `... up -d --build`.
RUN date -u +"%Y-%m-%d %H:%M UTC" > /srv/build_info.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
