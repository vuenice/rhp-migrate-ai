# syntax=docker/dockerfile:1
# ==============================================================================
# Multi-Stage Dockerfile for rhp-migrate-ai Self-Hosted Deployment
#
# Stages:
#   1. frontend-builder: Builds Next.js frontend with standalone output (Node.js 20)
#   2. web: Lean Node.js 20 production runner for Next.js standalone server
#   3. api: Python 3.12 environment with uv, compiler prerequisites, RHP CLI,
#           tree-sitter, FastAPI, and LiteLLM.
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build Next.js Frontend (Node.js 20, standalone output)
# ------------------------------------------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /app

# Install dependencies based on lockfile
COPY package.json package-lock.json ./
RUN npm ci

# Copy frontend source files and configuration
COPY app/ ./app/
COPY components/ ./components/
COPY hooks/ ./hooks/
COPY lib/ ./lib/
COPY types/ ./types/
COPY next.config.mjs postcss.config.js tailwind.config.ts tsconfig.json ./

# Disable Next.js telemetry and compile standalone distribution
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN npm run build


# ------------------------------------------------------------------------------
# Stage: web (Next.js Standalone Production Runner)
# ------------------------------------------------------------------------------
FROM node:20-alpine AS web
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME="0.0.0.0"

# Non-root unprivileged service account
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy standalone build output and static client assets
COPY --from=frontend-builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=frontend-builder --chown=nextjs:nodejs /app/.next/static ./.next/static
RUN mkdir -p public && chown nextjs:nodejs public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:3000/ || exit 1

CMD ["node", "server.js"]


# ------------------------------------------------------------------------------
# Stage 2: api (Python with uv, compiler prerequisites, RHP CLI, and FastAPI)
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS api
WORKDIR /app

# Install system compiler prerequisites and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    make \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv binary from official Astral distribution image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Configure Python, uv, and application paths
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:/usr/local/bin:$PATH" \
    RHP_WORKSPACE_ROOT="/data" \
    RHP_LEARNINGS_PATH="/data/learnings.md" \
    RHP_RULES_DIR="/data/rules" \
    RHP_WORKSPACES_DIR="/data/workspaces" \
    PORT=8000

# Install dependencies first for optimal Docker layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code and initial data templates
COPY rhp_migrate/ ./rhp_migrate/
COPY learnings.md ./learnings.md
COPY bin/ ./bin/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Install project package into virtual environment
RUN uv sync --frozen --no-dev

# Install and configure RHP CLI binary and compiler prerequisites
RUN chmod +x /app/bin/rhp && \
    ln -sf /app/bin/rhp /usr/local/bin/rhp && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

# Verify RHP CLI compiler check prerequisites
RUN rhp --version && rhp check --help

# Create persistent volume directories and templates
RUN mkdir -p /data/rules /data/workspaces && \
    cp /app/learnings.md /data/learnings.md.template && \
    cp -r /app/rhp_migrate/rules/* /data/rules/

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "rhp_migrate.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
