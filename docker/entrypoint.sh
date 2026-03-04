#!/usr/bin/env bash
set -euo pipefail

# Timezone
tz_name="${TZ:-${TIME_ZONE:-}}"
if [ -n "$tz_name" ] && [ -f "/usr/share/zoneinfo/$tz_name" ]; then
    ln -snf "/usr/share/zoneinfo/$tz_name" /etc/localtime
    echo "$tz_name" > /etc/timezone
    export TZ="$tz_name"
fi

# Select Apache configuration based on runtime mode
apache_templates_dir="${APACHE_TEMPLATES_DIR:-/opt/varuna/apache}"
if [ "${BACKEND_BEHIND_FRONTEND_PROXY:-0}" = "1" ]; then
    echo "Frontend proxy mode: Using HTTP-only configuration"
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

# Wait for PostgreSQL
db_engine="${DB_ENGINE:-django.db.backends.sqlite3}"
if [[ "$db_engine" == *postgres* ]]; then
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

if [ "${VARUNA_AUTH_BOOTSTRAP:-0}" = "1" ]; then
    bootstrap_username="${VARUNA_AUTH_USERNAME:-}"
    bootstrap_password="${VARUNA_AUTH_PASSWORD:-}"
    bootstrap_role="${VARUNA_AUTH_ROLE:-admin}"

    if [ -z "$bootstrap_username" ] || [ -z "$bootstrap_password" ]; then
        echo "VARUNA_AUTH_BOOTSTRAP=1 requires VARUNA_AUTH_USERNAME and VARUNA_AUTH_PASSWORD." >&2
        exit 1
    fi

    echo "Bootstrapping auth user \"$bootstrap_username\"..."
    auth_args=(
        --username "$bootstrap_username"
        --password "$bootstrap_password"
        --role "$bootstrap_role"
    )

    case "${VARUNA_AUTH_SUPERUSER:-0}" in
        1|true|TRUE|yes|YES|on|ON) auth_args+=(--superuser) ;;
    esac

    case "${VARUNA_AUTH_FORCE_PASSWORD:-0}" in
        1|true|TRUE|yes|YES|on|ON) auth_args+=(--force-password) ;;
    esac

    python manage.py ensure_auth_user "${auth_args[@]}"
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

if [ "${ENABLE_SCHEDULER:-0}" = "1" ]; then
    echo "Starting backend scheduler..."
    python manage.py run_scheduler &
fi

echo "Starting service: $*"
exec "$@"
