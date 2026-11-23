#!/usr/bin/env python3
"""Cross-platform runner to launch both FastAPI backend and Next.js frontend."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,0.0.0.0"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1,0.0.0.0"
VENV_UVICORN = ROOT / ".venv" / "bin" / "uvicorn"
UV_BIN = ROOT / ".bin" / "uv"

if VENV_UVICORN.exists():
    backend_cmd = [str(VENV_UVICORN), "rhp_migrate.server.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
elif UV_BIN.exists():
    backend_cmd = [str(UV_BIN), "run", "uvicorn", "rhp_migrate.server.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
else:
    backend_cmd = ["uvicorn", "rhp_migrate.server.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

processes: list[subprocess.Popen] = []


def cleanup(*args):
    print("\n\033[1;33mShutting down services...\033[0m")
    for p in processes:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
    print("\033[0;32m✔ All services stopped cleanly.\033[0m")
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

print("\033[0;36m======================================================\033[0m")
print("\033[0;36m   🚀 Starting rhp-migrate-ai (Backend + Frontend)   \033[0m")
print("\033[0;36m======================================================\033[0m")

print("\033[0;34m[1/2] Starting FastAPI Backend on port 8000...\033[0m")
backend_proc = subprocess.Popen(backend_cmd, cwd=ROOT)
processes.append(backend_proc)

time.sleep(1.5)

print("\033[0;34m[2/2] Starting Next.js Frontend on port 3000...\033[0m")
frontend_proc = subprocess.Popen(["npm", "run", "dev"], cwd=ROOT)
processes.append(frontend_proc)

print("\n\033[0;32m✔ Both services are running:\033[0m")
print("  ➜ Frontend Workbench: \033[0;36mhttp://localhost:3000\033[0m")
print("  ➜ Backend API:        \033[0;36mhttp://localhost:8000\033[0m")
print("  ➜ Interactive Docs:   \033[0;36mhttp://localhost:8000/docs\033[0m")
print("\033[1;33mPress Ctrl+C to stop all services.\033[0m\n")

try:
    for p in processes:
        p.wait()
except KeyboardInterrupt:
    cleanup()
