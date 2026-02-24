FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    apache2 \
    libapache2-mod-wsgi-py3 \
    python3-psycopg2 \
    gettext-base \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app && useradd -m -u 1000 appuser && chown -R appuser:appuser /app

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app/
RUN chown -R appuser:appuser /app && mkdir -p /var/www/static && chown -R appuser:appuser /var/www/static

COPY docker/entrypoint.sh /usr/local/bin/varuna-entrypoint.sh
RUN chmod +x /usr/local/bin/varuna-entrypoint.sh

COPY docker/apache/varuna.conf /opt/varuna/apache/varuna.conf
COPY docker/apache/varuna-ssl.conf /opt/varuna/apache/varuna-ssl.conf

EXPOSE 80 8000

RUN a2enmod wsgi rewrite ssl headers expires proxy_http

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/healthz/ || curl -fsS http://localhost:80/api/healthz/ || exit 1

ENTRYPOINT ["/usr/local/bin/varuna-entrypoint.sh"]
CMD ["apache2ctl", "-D", "FOREGROUND"]
