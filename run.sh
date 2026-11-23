#!/usr/bin/env bash
# ==============================================================================
# rhp-migrate-ai Development Stack Runner
# Runs both FastAPI backend (port 8000) and Next.js frontend (port 3000)
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure local addresses bypass any proxies
export NO_PROXY="localhost,127.0.0.1,::1,0.0.0.0"
export no_proxy="localhost,127.0.0.1,::1,0.0.0.0"

# Colors for terminal output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   🚀 Starting rhp-migrate-ai (Backend + Frontend)   ${NC}"
echo -e "${CYAN}======================================================${NC}"

# 1. Locate uvicorn binary
if [ -x "$SCRIPT_DIR/.venv/bin/uvicorn" ]; then
    UVICORN_CMD=("$SCRIPT_DIR/.venv/bin/uvicorn")
elif [ -x "$SCRIPT_DIR/.bin/uv" ]; then
    UVICORN_CMD=("$SCRIPT_DIR/.bin/uv" "run" "uvicorn")
elif command -v uvicorn &> /dev/null; then
    UVICORN_CMD=("uvicorn")
else
    echo -e "${RED}Error: Could not locate uvicorn or .venv. Please set up the python environment.${NC}"
    exit 1
fi

# 2. Cleanup handler for graceful shutdown on Ctrl+C / SIGTERM / EXIT
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo -e "${GREEN}✔ All services stopped cleanly.${NC}"
}
trap cleanup SIGINT SIGTERM EXIT

# 3. Start FastAPI Backend
echo -e "${BLUE}[1/2] Starting FastAPI Backend on port 8000...${NC}"
"${UVICORN_CMD[@]}" rhp_migrate.server.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

sleep 1.5

# 4. Start Next.js Frontend
echo -e "${BLUE}[2/2] Starting Next.js Frontend on port 3000...${NC}"
npm run dev &
FRONTEND_PID=$!

echo -e "\n${GREEN}✔ Both services are running:${NC}"
echo -e "  ➜ Frontend Workbench: ${CYAN}http://localhost:3000${NC}"
echo -e "  ➜ Backend API:        ${CYAN}http://localhost:8000${NC}"
echo -e "  ➜ Interactive Docs:   ${CYAN}http://localhost:8000/docs${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop all services.${NC}\n"

# Wait for background jobs
wait
