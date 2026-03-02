#!/bin/bash
# =============================================================================
# Teaming24 — Development Startup Script
# =============================================================================
# Uses uv as the primary package manager. Falls back to pip if uv is missing.
#
# Usage:
#   ./scripts/start_dev.sh              # Full auto (install + start)
#   ./scripts/start_dev.sh --install    # Install dependencies only
#   ./scripts/start_dev.sh --backend    # Start backend only
#   ./scripts/start_dev.sh --frontend   # Start frontend only
# =============================================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUI_DIR="$PROJECT_ROOT/teaming24/gui"
INSTALL_ONLY=false
BACKEND_ONLY=false
FRONTEND_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --install)   INSTALL_ONLY=true ;;
        --backend)   BACKEND_ONLY=true ;;
        --frontend)  FRONTEND_ONLY=true ;;
    esac
done

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()  { echo -e "${CYAN}---${NC} $1"; }

# ---------------------------------------------------------------------------
# 1. Pre-flight checks
# ---------------------------------------------------------------------------
echo ""
echo "========================================="
echo "  Teaming24 — Development Environment"
echo "========================================="
echo ""

HAS_UV=false
if command -v uv &> /dev/null; then
    HAS_UV=true
    info "uv: $(uv --version)"
else
    warn "uv not found — falling back to pip"
    warn "  Install uv for faster workflow: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if ! command -v node &> /dev/null; then
    fail "Node.js is not installed. Please install Node.js 18+."
fi
info "Node.js: $(node --version)"

# ---------------------------------------------------------------------------
# 2. Backend dependencies
# ---------------------------------------------------------------------------
step "Backend dependencies"
cd "$PROJECT_ROOT"

if $HAS_UV; then
    uv sync --group dev 2>&1 | tail -3
    info "Backend dependencies installed (uv sync)"
    PYTHON_CMD="uv run python"
else
    if ! python -c "import fastapi" 2>/dev/null; then
        warn "Installing Python dependencies via pip..."
        pip install -r requirements.txt
    fi
    info "Backend dependencies installed (pip)"
    PYTHON_CMD="python"
fi

# ---------------------------------------------------------------------------
# 3. Frontend dependencies
# ---------------------------------------------------------------------------
if ! $BACKEND_ONLY; then
    step "Frontend dependencies"
    cd "$GUI_DIR"
    npm install --silent 2>&1 | tail -2
    info "Frontend dependencies installed"
fi

# ---------------------------------------------------------------------------
# Install-only mode stops here
# ---------------------------------------------------------------------------
if $INSTALL_ONLY; then
    echo ""
    info "Dependencies installed. Run without --install to start servers."
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. Start servers
# ---------------------------------------------------------------------------
echo ""
step "Starting servers"
cd "$PROJECT_ROOT"

if ! $FRONTEND_ONLY; then
    info "Starting backend ($PYTHON_CMD main.py --reload) ..."
    $PYTHON_CMD main.py --reload &
    BACKEND_PID=$!
    sleep 2
fi

if ! $BACKEND_ONLY; then
    cd "$GUI_DIR"
    info "Starting frontend (npm run dev) ..."
    npm run dev &
    FRONTEND_PID=$!
fi

# ---------------------------------------------------------------------------
# 5. Cleanup on exit
# ---------------------------------------------------------------------------
cleanup() {
    echo ""
    warn "Shutting down servers..."
    [ -n "$BACKEND_PID" ]  && kill $BACKEND_PID  2>/dev/null || true
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null || true
    [ -n "$BACKEND_PID" ]  && wait $BACKEND_PID  2>/dev/null || true
    [ -n "$FRONTEND_PID" ] && wait $FRONTEND_PID 2>/dev/null || true
    info "All servers stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

echo ""
echo "========================================="
echo "  Development environment is ready!"
echo ""
[ -n "$BACKEND_PID" ]  && echo "  Backend API:  http://localhost:8000"
[ -n "$FRONTEND_PID" ] && echo "  Frontend GUI: http://localhost:8088"
[ -n "$BACKEND_PID" ]  && echo "  API Docs:     http://localhost:8000/docs"
echo ""
echo "  Press Ctrl+C to stop all servers."
echo "========================================="
echo ""

wait
