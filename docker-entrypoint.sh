#!/usr/bin/env bash
set -e

# Initialize persistent volume directories
mkdir -p /data/rules /data/workspaces

# Seed default learnings.md into persistent /data volume if not present
if [ ! -f /data/learnings.md ]; then
    if [ -f /app/learnings.md ]; then
        cp /app/learnings.md /data/learnings.md
        echo "[Entrypoint] Initialized /data/learnings.md from template."
    else
        touch /data/learnings.md
    fi
fi

# Seed default migration rules into persistent /data volume if not present
if [ ! -f /data/rules/rules.yaml ] && [ -d /app/rhp_migrate/rules ]; then
    cp -r /app/rhp_migrate/rules/* /data/rules/
    echo "[Entrypoint] Initialized /data/rules from built-in rules."
fi

# Execute the main command
exec "$@"
