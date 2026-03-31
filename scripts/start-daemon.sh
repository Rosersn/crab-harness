#!/usr/bin/env bash
#
# start-daemon.sh - Start all Crab Harness development services in daemon mode
#
# This script starts services in the background without keeping
# the terminal connection. Logs are written to separate files.
#
# Must be run from the repo root directory.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Load environment variables from .env ──────────────────────────────────────
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

# ── Stop existing services ────────────────────────────────────────────────────

echo "Stopping existing services if any..."
pkill -f "uvicorn app.gateway.app:app" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "next-server" 2>/dev/null || true
nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" -s quit 2>/dev/null || true
sleep 1
pkill -9 nginx 2>/dev/null || true
./scripts/cleanup-containers.sh crab-sandbox 2>/dev/null || true
sleep 1

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo " Starting Crab Harness in Daemon Mode"
echo "=========================================="
echo ""

# ── Config check ─────────────────────────────────────────────────────────────

if ! { \
        [ -n "$CRAB_CONFIG_PATH" ] && [ -f "$CRAB_CONFIG_PATH" ] || \
        [ -f backend/config.yaml ] || \
        [ -f config.yaml ]; \
    }; then
    echo "✗ No config file found."
    echo "  Checked these locations:"
    echo "    - $CRAB_CONFIG_PATH (when CRAB_CONFIG_PATH is set)"
    echo "    - backend/config.yaml"
    echo "    - ./config.yaml"
    echo ""
    echo "  Run 'make config' from the repo root to generate ./config.yaml, then set required model API keys in .env or your config file."
    exit 1
fi

# ── Auto-upgrade config ──────────────────────────────────────────────────

"$REPO_ROOT/scripts/config-upgrade.sh"

# ── Cleanup on failure ───────────────────────────────────────────────────────

cleanup_on_failure() {
    echo "Failed to start services, cleaning up..."
    pkill -f "uvicorn app.gateway.app:app" 2>/dev/null || true
    pkill -f "next dev" 2>/dev/null || true
    pkill -f "next-server" 2>/dev/null || true
    nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" -s quit 2>/dev/null || true
    sleep 1
    pkill -9 nginx 2>/dev/null || true
    echo "✓ Cleanup complete"
}

trap cleanup_on_failure INT TERM

# ── Start services ────────────────────────────────────────────────────────────

mkdir -p logs

# ── Check infrastructure dependencies (PostgreSQL + Redis) ────────────────
echo "Checking infrastructure dependencies..."

# Check PostgreSQL
if command -v pg_isready >/dev/null 2>&1; then
    PG_HOST="${CRAB_PG_HOST:-localhost}"
    PG_PORT="${CRAB_PG_PORT:-5432}"
    if ! pg_isready -h "$PG_HOST" -p "$PG_PORT" -q 2>/dev/null; then
        echo "✗ PostgreSQL is not running on $PG_HOST:$PG_PORT"
        echo "  Start it with: cd backend && docker compose -f docker-compose.dev.yml up -d"
        cleanup_on_failure
        exit 1
    fi
    echo "  ✓ PostgreSQL reachable on $PG_HOST:$PG_PORT"
else
    echo "  ⚠ pg_isready not found, skipping PostgreSQL check"
fi

# Check Redis
if command -v redis-cli >/dev/null 2>&1; then
    REDIS_HOST="${CRAB_REDIS_HOST:-localhost}"
    REDIS_PORT="${CRAB_REDIS_PORT:-6379}"
    if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
        echo "✗ Redis is not running on $REDIS_HOST:$REDIS_PORT"
        echo "  Start it with: cd backend && docker compose -f docker-compose.dev.yml up -d"
        cleanup_on_failure
        exit 1
    fi
    echo "  ✓ Redis reachable on $REDIS_HOST:$REDIS_PORT"
else
    echo "  ⚠ redis-cli not found, skipping Redis check"
fi

echo "Starting Gateway API (with embedded Agent runtime)..."
nohup sh -c 'cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 > ../logs/gateway.log 2>&1' &
./scripts/wait-for-port.sh 8001 30 "Gateway API" || {
    echo "✗ Gateway API failed to start. Last log output:"
    tail -60 logs/gateway.log
    echo ""
    echo "Likely configuration errors:"
    grep -E "Failed to load configuration|Environment variable .* not found|config\.yaml.*not found" logs/gateway.log | tail -5 || true
    echo ""
    echo "  Hint: Try running 'make config-upgrade' to update your config.yaml with the latest fields."
    cleanup_on_failure
    exit 1
}
echo "✓ Gateway API started on localhost:8001"

echo "Starting Frontend..."
nohup sh -c 'cd frontend && pnpm run dev > ../logs/frontend.log 2>&1' &
./scripts/wait-for-port.sh 3000 120 "Frontend" || {
    echo "✗ Frontend failed to start. Last log output:"
    tail -60 logs/frontend.log
    cleanup_on_failure
    exit 1
}
echo "✓ Frontend started on localhost:3000"

echo "Starting Nginx reverse proxy..."
nohup sh -c 'nginx -g "daemon off;" -c "$1/docker/nginx/nginx.local.conf" -p "$1" > logs/nginx.log 2>&1' _ "$REPO_ROOT" &
./scripts/wait-for-port.sh 2026 10 "Nginx" || {
    echo "✗ Nginx failed to start. Last log output:"
    tail -60 logs/nginx.log
    cleanup_on_failure
    exit 1
}
echo "✓ Nginx started on localhost:2026"

# ── Ready ─────────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo " Crab Harness is running in daemon mode!"
echo "=========================================="
echo ""
echo " 🌐 Application: http://localhost:2026"
echo " 📡 API Gateway: http://localhost:2026/api/*"
echo ""
echo " 📋 Logs:"
echo " - Gateway: logs/gateway.log"
echo " - Frontend: logs/frontend.log"
echo " - Nginx: logs/nginx.log"
echo ""
echo " 🛑 Stop daemon: make stop"
echo ""
