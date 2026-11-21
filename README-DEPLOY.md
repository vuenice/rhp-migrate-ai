# Self-Hosted Enterprise Deployment Guide (`rhp-migrate-ai`)

This guide covers deploying `rhp-migrate-ai` on internal company infrastructure, on-premise servers, or private VPCs (AWS, GCP, Azure) with a single Docker Compose command.

---

## 🏛️ Architecture Overview

```
                          ┌────────────────────────────────────────────────────────┐
                          │                Private VPC / Corporate LAN             │
                          │                                                        │
[Developer Browser] ─────►│  :3000 ──► [ web: Next.js 20 Standalone UI ]          │
                          │                     │                                  │
                          │                     │ (Internal Proxy /api/*)          │
                          │                     ▼                                  │
[IDE / CI/CD Scripts] ───►│  :8000 ──► [ api: FastAPI Backend Service ]            │
                          │                     │                                  │
                          │         ┌───────────┴────────────┐                     │
                          │         ▼                        ▼                     │
                          │  [ Persistent Volume ]     [ Optional Ollama ]         │
                          │    - /data/learnings.md     (Local LLM on :11434)      │
                          │    - /data/rules/           100% Offline Air-Gapped    │
                          │    - /data/workspaces/                                 │
                          └────────────────────────────────────────────────────────┘
```

The system comprises three core services:
1. **`web`**: A lightweight Next.js 20 frontend container built with standalone output. Serves the interactive Migration Workbench, Monaco Diff Viewer, and Rule Inspector.
2. **`api`**: A Python 3.12 FastAPI backend powered by `uv`, Tree-sitter PHP, LiteLLM BYOK routing, and the RHP compiler CLI (`rhp check`). Mounts a persistent volume for continuous system learning and custom rules.
3. **`ollama` (Optional Profile `local-llm`)**: Local LLM runner providing zero-egress, 100% offline air-gapped code migration without external API dependencies.

---

## 📋 System Requirements

| Deployment Mode | CPU | RAM | Disk | Acceleration |
|---|---|---|---|---|
| **Cloud BYOK (OpenAI/Gemini/Anthropic)** | 2 vCPU | 4 GB | 20 GB SSD | None |
| **Local LLM (Ollama 7B/8B Models)** | 4–8 vCPU | 16–32 GB | 50 GB SSD | Recommended: NVIDIA GPU (CUDA 12+) |

**Prerequisites:**
- Docker Engine 24.0+
- Docker Compose v2.20+

---

## 🚀 Step-by-Step Deployment

### 1. Clone Repository & Prepare Directory

```bash
git clone https://github.com/your-org/rhp-migrate-ai.git
cd rhp-migrate-ai
```

### 2. Configure Environment Variables

Copy the template configuration file:
```bash
cp .env.example .env
```

Edit `.env` to match your internal networking and security requirements:

```ini
# Host Port Bindings
WEB_PORT=3000
API_PORT=8000

# CORS Configuration (restrict to your internal domain)
CORS_ORIGINS=https://rhp-migrate.internal.company.com,http://localhost:3000

# Optional Server-Default API Keys (leave empty if developers bring their own keys)
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
```

### 3. Launch the Stack

#### Standard Cloud / BYOK Mode
For deployments where users connect their own API keys (Gemini, Claude, GPT-4o, DeepSeek):

```bash
docker compose up -d --build
```

#### 100% Offline Air-Gapped Mode (Local Ollama Profile)
For high-security, zero-egress networks with no internet access:

```bash
docker compose --profile local-llm up -d --build
```

Download the recommended migration model into the local Ollama container:
```bash
# Pull Llama 3 or Qwen 2.5 Coder
docker compose exec ollama ollama pull llama3
# or:
docker compose exec ollama ollama pull qwen2.5-coder:7b
```

### 4. Verify Health & Status

Check container health status:
```bash
docker compose ps
```

Verify the API backend health probe:
```bash
curl -f http://localhost:8000/api/health
# Response: {"status":"healthy","service":"rhp-migrate-ai","version":"0.1.0"}
```

Access the Web Workbench in your browser at:
```
http://<server-ip-or-domain>:3000
```

---

## 💾 Persistent Storage & Knowledge Base

The `api` container persists all working knowledge into the `rhp_data` volume mounted at `/data`:

| Container Path | Description |
|---|---|
| `/data/learnings.md` | Fast-loop system working memory capturing proven fixes and AST heuristics. |
| `/data/rules/rules.yaml` | Machine-executable declarative migration rules. |
| `/data/rules/context.md` | Core migration heuristics and reference patterns. |
| `/data/workspaces/` | Isolated workspace sandboxes for uploaded zip archives and clones. |

### Seeding on First Launch
On first startup, the entrypoint script automatically initializes `/data/learnings.md` and `/data/rules/` from the repository templates if the persistent volume is empty.

### Backup and Restore
To create a complete snapshot of your organization's custom rules and learnings:

```bash
# Backup
docker run --rm \
  -v rhp_migrate_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/rhp-data-backup-$(date +%Y%m%d).tar.gz -C /data .

# Restore
docker run --rm \
  -v rhp_migrate_data:/data \
  -v $(pwd):/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/rhp-data-backup-<date>.tar.gz -C /data"
```

---

## ⚙️ RHP Compiler Tooling (`rhp check`)

The container includes a built-in RHP compiler check tool installed at `/usr/local/bin/rhp`:
- Validates syntax and type boundary contracts via tree-sitter AST traversal.
- Emits newline-delimited JSON (NDJSON) diagnostics matching `rhp check --format=json`.
- Feeds compiler diagnostics into the automated auto-healing feedback loop.

### Using Native RHP Engine Binaries
If your enterprise has compiled the native Rust RHP binary (`rhp`), you can mount it into the container:

Add the volume mount to `docker-compose.yml` under `api`:
```yaml
    volumes:
      - rhp_data:/data
      - /usr/local/bin/rhp:/usr/local/bin/rhp.native:ro
```
The internal bridge script will automatically delegate commands directly to the native binary.

---

## 🚀 GPU Acceleration for Local LLMs (NVIDIA)

To enable GPU acceleration for the `ollama` container, ensure the **NVIDIA Container Toolkit** is installed on the host, then add the `deploy` reservation to `docker-compose.yml`:

```yaml
  ollama:
    image: ollama/ollama:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

---

## 🔒 Enterprise Reverse Proxy Configuration (Nginx)

For corporate SSL termination, place an Nginx reverse proxy in front of the services:

```nginx
server {
    listen 443 ssl http2;
    server_name rhp-migrate.internal.company.com;

    ssl_certificate     /etc/ssl/certs/company.crt;
    ssl_certificate_key /etc/ssl/private/company.key;

    # Frontend Web Workbench
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Backend API & Server-Sent Events (SSE) Streaming
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Critical for SSE live migration streaming:
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
```

---

## 🛠️ Maintenance & Operations

### View Real-Time Logs
```bash
# Follow logs for all services
docker compose logs -f

# Follow API service only
docker compose logs -f api
```

### Update and Rebuild Containers
```bash
git pull
docker compose build --no-cache
docker compose up -d
```

### Stop or Teardown Containers
```bash
# Stop containers (preserves all data in persistent volumes)
docker compose down

# Stop and purge volumes (CAUTION: Deletes learnings and rules)
docker compose down -v
```
