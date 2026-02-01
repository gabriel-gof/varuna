#!/bin/bash
# Varuna Development Environment Startup Script

echo "🚀 Starting Varuna Development Environment..."

# Start PostgreSQL (if not running)
if ! docker ps -q -f name=varuna-db; then
    echo "📊 Starting PostgreSQL..."
    docker run --name varuna-db -d \
        -p 5432:5432 \
        -e POSTGRES_PASSWORD=postgres \
        -e POSTGRES_USER=postgres \
        -e POSTGRES_DB=varuna_dev \
        -v postgres_data:/var/lib/postgresql/data \
        postgres:15-alpine
    sleep 5
else
    echo "✅ PostgreSQL already running"
fi

# Start Redis (if not running)
if ! docker ps -q -f name=varuna-redis; then
    echo "📊 Starting Redis..."
    docker run --name varuna-redis -d \
        -p 6379:6379 \
        redis:7-alpine
    sleep 2
else
    echo "✅ Redis already running"
fi

# Wait for PostgreSQL to be healthy
echo "⏳ Waiting for PostgreSQL to be healthy..."
for i in {1..30}; do
    if docker exec varuna-db pg_isready -U postgres -d varuna_dev 2>/dev/null; then
        echo "✅ PostgreSQL is healthy"
        break
    fi
    sleep 1
done

# Start Django Development Server
echo "🐍 Starting Django Development Server..."
cd /Users/gabriel/Documents/varuna/backend

# Activate virtual environment
source venv/bin/activate

# Install missing dependencies if needed
pip install python-decouple django-cors-headers redis psycopg2-binary > /dev/null 2>&1

# Set environment variables
export DJANGO_SETTINGS_MODULE=varuna.settings
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=varuna_dev
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export REDIS_URL=redis://localhost:6379/0
export DEBUG=True
export SECRET_KEY=dev-secret-key-change-in-production

# Run migrations (no-op if already run)
echo "📝 Running migrations..."
python manage.py migrate --run-syncdb 2>&1 | grep -E "Applying|No migrations"

# Create superuser if not exists
if ! python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); exit(0 if User.objects.filter(is_superuser=True).exists() else exit(1))"; then
    echo "👤 Creating superuser..."
    echo "from django.contrib.auth.models import User" | python manage.py shell
    echo "User.objects.create_superuser('admin', 'admin@varuna.com', 'admin123')" | python manage.py shell
else
    echo "✅ Superuser already exists"
fi

# Start Django dev server
echo "🌐 Django Development Server: http://localhost:8000"
echo ""
echo "📚 Available Endpoints:"
echo "   Admin:       http://localhost:8000/admin/"
echo "   API:         http://localhost:8000/api/"
echo "   OLTs:        http://localhost:8000/api/olts/"
echo "   Vendor Profiles: http://localhost:8000/api/vendor-profiles/"
echo ""
echo "⚠️  Press Ctrl+C to stop Django server"
echo ""

python manage.py runserver 0.0.0.0:8000
