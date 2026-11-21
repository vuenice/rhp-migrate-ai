# rhp-migrate-ai

AI-powered migration tool from PHP/Laravel codebases to RHP (high-performance, strictly-typed, ahead-of-time compiled PHP superset).

---

## 🚀 FastAPI Backend Service with BYOK Support

`rhp-migrate-ai` includes a modern FastAPI backend service allowing frontend applications, IDE extensions, or web dashboards to invoke AST analysis, incremental migration, compiler verification, and AI critic loops.

### Bring-Your-Own-Key (BYOK) Inference

The server operates on a strict **zero-server-cost Bring-Your-Own-Key model** powered by **LiteLLM**. Users supply their own API credentials dynamically per-request via HTTP headers:

| Header | Description | Examples |
| :--- | :--- | :--- |
| `x-provider` | LLM Provider | `openai`, `anthropic`, `gemini`, `deepseek`, `ollama`, `mock` |
| `x-api-key` | User's Secret API Key | `sk-...`, `AIzaSy...` |
| `x-model-name` | Model override | `gemini-2.5-pro`, `claude-3-7-sonnet`, `gpt-4o`, `deepseek-chat` |
| `x-api-base` | Custom Base URL (optional) | `http://localhost:11434` (Ollama), `https://my-proxy/v1` |

---

## 🏃 Starting the Server

### Option A: Using the CLI
```bash
uv run rhp-migrate-ai server --port 8000 --host 0.0.0.0
```

### Option B: Using Uvicorn directly
```bash
uv run uvicorn rhp_migrate.server.main:app --port 8000 --host 0.0.0.0 --reload
```

Interactive API documentation is available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 📡 API Endpoints

### 1. `POST /api/analyze`
Runs AST readiness scoring, unsupported feature detection, untyped parameter diagnostics, and topological dependency order.

Supports:
- **Direct PHP snippet**: `{"code": "<?php ...", "file_name": "Calculator.php"}`
- **In-memory project files**: `{"files": {"app/A.php": "<?php ...", "app/B.php": "..."}}`
- **Filesystem directory**: `{"project_path": "/path/to/project"}`
- **Multipart Upload**: Zip archive or uploaded `.php` files (`file=@project.zip`)

### 2. `POST /api/migrate/file`
Synchronously migrates a single PHP file into RHP code with AST fallback rules, LLM healing, compiler verification, and AI critic evaluation.

### 3. `POST /api/migrate/stream`
Streams migration progress in real time via **Server-Sent Events (SSE)** (`text/event-stream`). Emits sequential lifecycle events:
1. `event: ast_parsing` - AST readiness, unsupported constructs, untyped parameters
2. `event: initial_rhp_draft` - Deterministic rule applications & initial LLM draft
3. `event: compiler_check` - Syntax validation & compiler diagnostics
4. `event: critic_reflection` - AI Continuous Improvement Critic evaluation & context proposals
5. `event: final_code` - Final verified RHP code, leftover TODOs, token usage & cost summary
6. `event: done` - Migration lifecycle completion

### 4. Rules & Working Learnings Endpoints
- `GET /api/rules/learnings`: Read `learnings.md` (fast working memory)
- `PUT /api/rules/learnings`: Overwrite `learnings.md`
- `POST /api/rules/learnings`: Append structured learning entry to `learnings.md`
- `GET /api/rules/context`: Read `rhp_migrate/rules/context.md` (curated knowledge base)
- `PUT /api/rules/context`: Overwrite `context.md`
- `POST /api/rules/context`: Append section to `context.md`
- `GET /api/rules/pending`: List pending context proposals
- `POST /api/rules/context/approve`: Approve and merge pending proposals into `context.md`

---

## 💻 Example Curl Commands

### 1. Analyze a PHP Code Snippet
```bash
curl -X POST "http://localhost:8000/api/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<?php function calculateTotal($items, $taxRate) { return array_sum($items) * (1 + $taxRate); }",
    "file_name": "OrderCalculator.php"
  }'
```

### 2. Analyze an Uploaded ZIP Project Archive
```bash
curl -X POST "http://localhost:8000/api/analyze" \
  -F "file=@/path/to/laravel-app.zip"
```

### 3. Synchronous Migration with BYOK (OpenAI GPT-4o)
```bash
curl -X POST "http://localhost:8000/api/migrate/file" \
  -H "Content-Type: application/json" \
  -H "x-provider: openai" \
  -H "x-api-key: sk-proj-YOUR_API_KEY" \
  -H "x-model-name: gpt-4o" \
  -d '{
    "file_path": "app/Services/PaymentService.php",
    "code": "<?php namespace App\\Services; class PaymentService { public function charge($user, $amount) { return Http::post(\"/charge\", [\"amt\" => $amount]); } }",
    "php_version": "8.2",
    "run_critic": true
  }'
```

### 4. Real-time Streaming Migration via SSE (Google Gemini 2.5 Pro)
```bash
curl -N -X POST "http://localhost:8000/api/migrate/stream" \
  -H "Content-Type: application/json" \
  -H "x-provider: gemini" \
  -H "x-api-key: YOUR_GEMINI_API_KEY" \
  -H "x-model-name: gemini-2.5-pro" \
  -d '{
    "file_path": "app/Utils/Math.php",
    "code": "<?php function multiply($a, $b) { return $a * $b; }",
    "php_version": "8.2",
    "run_critic": true
  }'
```

### 5. Local Ollama Inference (Zero Cloud Cost)
```bash
curl -N -X POST "http://localhost:8000/api/migrate/stream" \
  -H "Content-Type: application/json" \
  -H "x-provider: ollama" \
  -H "x-model-name: llama3" \
  -H "x-api-base: http://localhost:11434" \
  -d '{
    "file_path": "app/Utils/Math.php",
    "code": "<?php function multiply($a, $b) { return $a * $b; }",
    "run_critic": false
  }'
```

### 6. Read and Append to Learnings
```bash
# Read learnings.md
curl "http://localhost:8000/api/rules/learnings"

# Append a new learning
curl -X POST "http://localhost:8000/api/rules/learnings" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Strict Type Disambiguation for Associative Arrays",
    "content": "- **Context**: Loose PHP array syntax fails compilation in RHP.\n- **Working Solution**: Convert to dict<string, mixed>."
  }'
```

---

## 🧪 Testing

Run the full automated test suite:
```bash
uv run pytest
```
