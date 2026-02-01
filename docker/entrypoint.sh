#!/usr/bin/env bash
set -euo pipefail

# Timezone
tz_name="${TZ:-${TIME_ZONE:-}}"
if [ -n "$tz_name" ] && [ -f "/usr/share/zoneinfo/$tz_name" ]; then
    ln -snf "/usr/share/zoneinfo/$tz_name" /etc/localtime
    echo "$tz_name" > /etc/timezone
    export TZ="$tz_name"
fi

# Select Apache configuration based on role
apache_templates_dir="${APACHE_TEMPLATES_DIR:-/opt/varuna/apache}"
if [ "${SNMP_API_ONLY:-0}" = "1" ]; then
    echo "SNMP API mode: Using HTTP-only configuration"
    cp "${apache_templates_dir}/varuna.conf" /etc/apache2/sites-available/000-default.conf
elif [ "${DEBUG:-False}" = "True" ]; then
    echo "Development mode: Using HTTP-only configuration"
    cp "${apache_templates_dir}/varuna.conf" /etc/apache2/sites-available/000-default.conf
else
    echo "Production mode: Using SSL configuration"
    cp "${apache_templates_dir}/varuna-ssl.conf" /etc/apache2/sites-available/000-default.conf
fi

# Substitute environment variables in Apache config
apache_conf=/etc/apache2/sites-available/000-default.conf
if grep -q '\${' "$apache_conf"; then
    envsubst '$SERVER_NAME $SERVER_ALIASES $WSGI_PROCESSES $WSGI_THREADS' < "$apache_conf" > /tmp/apache.conf
    mv /tmp/apache.conf "$apache_conf"
fi

# SNMP log path setup (if applicable)
if [ "${SNMP_API_ONLY:-0}" = "1" ] && [ -n "${SNMP_LOG_PATH:-}" ]; then
    log_dir="$(dirname "$SNMP_LOG_PATH")"
    mkdir -p "$log_dir"
    touch "$SNMP_LOG_PATH"
    chown www-data:www-data "$SNMP_LOG_PATH" || true
fi

# Wait for PostgreSQL
if [ "${DB_ENGINE:-sqlite}" = "postgres" ]; then
    echo "Waiting for PostgreSQL..."
    python <<'PY'
import time
import django
from django.db import connections
from django.db.utils import OperationalError

django.setup()

while True:
    try:
        connections['default'].cursor()
        break
    except OperationalError:
        time.sleep(1)
PY
fi

if [ "${DJANGO_MIGRATE:-1}" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
fi

if [ "${DJANGO_COLLECTSTATIC:-1}" = "1" ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput
    if python manage.py compress --help >/dev/null 2>&1; then
        echo "Compressing CSS/JS files..."
        python manage.py compress --force
    else
        echo "Skipping compress (django-compressor not installed)"
    fi
fi

if [ "${SNMP_API_ONLY:-0}" = "1" ] && [ -n "${SNMP_LOG_PATH:-}" ]; then
    tail -n 0 -F "$SNMP_LOG_PATH" &
fi

echo "Starting Apache..."
exec "$@"
