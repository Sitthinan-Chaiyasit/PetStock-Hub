FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PETSTOCK_DB=/data/petstock.db \
    REQUIRE_SECRET_KEY=1 \
    GUNICORN_WORKERS=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./

# SQLite uses one writer at a time; a single process is intentional here.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-1} --timeout 60 --access-logfile - --error-logfile - wsgi:app"]
