# System Memory & Working Learnings (`learnings.md`)

This file serves as the **persistent working memory** for AI pair-programming and automated migrations in `rhp-migrate-ai`.
Whenever a solution is tested and proven to work (or an edge case/bug is uncovered), document it here. Before attempting complex refactors or migrations, consult this file to avoid recurring traps.

---

## 📋 Memory Log Schema

When appending new learnings, follow this format:

```markdown
### [YYYY-MM-DD] <Concise Title of Problem & Solution>
- **Context / Symptom**: What failed or needed resolution? (Error message, compiler check, test failure)
- **Root Cause**: Why did the previous approach fail?
- **Working Solution**: What exact pattern, code, or configuration fixed it?
- **Verification**: How was it verified? (e.g., `uv run pytest tests/...`, `rhp check`)
- **Key Heuristic**: 1-2 sentence rule-of-thumb for future operations.
```

---

## 🧠 Active Working Learnings

### [2026-11-19] Dual-Loop Learning: Working Memory vs. Repository Context
- **Context / Symptom**: How to balance quick agent self-learning with the formal `approve-context` feedback pipeline.
- **Root Cause**: The application has an automated Critic feedback loop (`.rhp/feedback/pending_context.md` -> `rhp_migrate/rules/context.md`), but it requires explicit approval steps and doesn't capture operational/agent dev learnings in real time.
- **Working Solution**: Establish a two-tier memory system:
  1. **Fast-Loop Working Memory (`learnings.md`)**: Immediate, agent-maintained scratchpad for development insights, bug fixes, CLI gotchas, and proven code transformations.
  2. **Slow-Loop Knowledge Base (`pending_context.md` -> `context.md` / `rules.yaml`)**: Curated, human-approved rules specifically injected into the LLM migration engine and prompt templates.
- **Verification**: Architecture established and documented across workspace.
- **Key Heuristic**: Log working fixes immediately into `learnings.md`; graduate high-value, generalized migration rules into `context.md` / `rules.yaml`.

### [2026-11-19] Package Management & Test Execution
- **Context / Symptom**: Running tests and CLI commands inside this repository.
- **Root Cause**: Project uses `uv` as the package manager and Python virtual environment orchestrator.
- **Working Solution**: Run commands prefixed with `uv run` (e.g., `uv run pytest`, `uv run python -m rhp_migrate.cli`).
- **Verification**: `uv run pytest` executes cleanly in the virtual environment.
- **Key Heuristic**: Always invoke python tooling via `uv run` to ensure pinned dependencies and lockfile consistency.

### [2026-11-19] RHP Type System Disambiguation Rules
- **Context / Symptom**: Dynamic PHP arrays and loose equality cause compiler type-check failures in `rhp check`.
- **Root Cause**: RHP is strictly typed and ahead-of-time compiled without dynamic type coercion or arbitrary heterogeneous PHP arrays.
- **Working Solution**:
  - Convert indexed lists to `T[]` or `list<T>`.
  - Convert key-value hash maps to `dict<string, T>` or `map<string, T>`.
  - Replace `===` / `!==` with `==` / `!=`.
  - Replace `$_GET['k']` / `$_POST['k']` with `Request::query('k')` / `Request::input('k')`.
  - Eliminate `eval`, `$$var`, and reflection in favor of constructor injection or static registries.
- **Verification**: Codified in `skills/rhp-migration/SKILL.md` and `rhp_migrate/rules/context.md`.
- **Key Heuristic**: Treat compiler errors from `rhp check` as ground truth; never bypass with `mixed` unless strictly unavoidable.

### [2026-11-21] RHP Prefix-Typed Variable Declaration Syntax *(Graduated to RHP-SYN-007)*
- **Context / Symptom**: Attempting TypeScript/Rust style colon typing (`$html: string` or `$count: int`) is rejected by the RHP parser with a syntax error.
- **Root Cause**: RHP uses C/Java-style prefix type syntax (`Type varName = value;` or `string html = ...;`), NOT postfix colon type syntax.
- **Working Solution**:
  - Always write: `string html = View::render('...');`
  - Array / list types: `Product[] products = Product::all();` or `list<Product> products = Product::all();`
  - Associative maps: `dict<string, mixed> data = [...];`
  - Never emit `$var: Type`.
- **Verification**: Verified in `../rhp/src/parser/stmt.rs` and codified as `RHP-SYN-007` in `rhp_migrate/rules/rules.yaml`.
- **Key Heuristic**: Always use prefix typing (`Type name = expr;`) for typed variable declarations in RHP.

### [2026-11-21] Deterministic Mistake Auditing & Syntax Validation Before LLM Stage
- **Context / Symptom**: Deterministic passes (`rhp migrate` or AST fallback rules) are not perfect and can introduce invalid syntax (e.g. colon typing or unbalanced replacements) that propagate to downstream stages.
- **Root Cause**: Blindly trusting deterministic outputs without verifying syntax validity leads to cascading compiler errors.
- **Working Solution**:
  - Run `DeterministicValidator` immediately after deterministic passes.
  - Check for invalid syntax via AST inspection (`PHPParser`) and compiler check (`rhp check`).
  - If invalid, capture `before` syntax, `after` syntax, `what_is_invalid`, `what_could_have_been_done_better`, and write structured audit records to `.rhp/reports/deterministic_audit/<hash>.json` and `.md`.
  - Append actionable context update proposals to `.rhp/feedback/pending_context.md`.
  - Pass the exact syntax error diagnostics to the LLM stage so it can heal the deterministic tool's mistakes.
- **Verification**: Verified in `tests/test_deterministic_audit.py` and integrated into `rhp_migrate/cli.py`.
- **Key Heuristic**: Never assume deterministic migration is flawless; validate output syntax first and audit mistakes before proceeding to LLM or commit steps.

### [2026-11-21] FastAPI Backend BYOK LiteLLM Dynamic Multi-Provider Architecture
- **Context / Symptom**: Exposing `rhp_migrate` engine via FastAPI with zero server inference cost and Server-Sent Events (SSE) streaming.
- **Root Cause**: Fixed server API keys create billing liabilities and lock users into a single provider; additionally, in newer FastAPI/Starlette versions, `fastapi.UploadFile` and `starlette.datastructures.UploadFile` are not identical classes, causing strict `isinstance(item, UploadFile)` checks on `request.form()` to fail.
- **Working Solution**:
  - Dynamically read `x-provider`, `x-api-key`, `x-model-name`, and `x-api-base` headers per request.
  - Implement `LiteLLMClient(BaseLLMClient)` resolving provider formats (`gemini/`, `anthropic/`, `ollama/`, `deepseek/`) and passing credentials directly into `litellm.completion` / `litellm.acompletion`.
  - Use duck-typing (`hasattr(item, "read")`) for multipart form items to avoid FastAPI vs Starlette `UploadFile` class mismatch.
  - Structure SSE streaming via `StreamingResponse(event_generator(), media_type="text/event-stream")` emitting sequenced stages: AST parsing -> initial draft -> compiler check -> critic reflection -> final code -> done.
- **Verification**: Verified across 13 new unit tests in `tests/test_server_api.py` and 194 total passing tests.
### [2026-11-21] Production Docker Compose Multi-Stage Setup & Air-Gapped Deployment
- **Context / Symptom**: Deploying `rhp-migrate-ai` on internal company servers or private VPCs requires a multi-service setup (Next.js frontend, FastAPI backend, persistent volumes for learnings/rules, compiler check prerequisites, and offline LLMs).
- **Root Cause**: Next.js monolithic runtime image sizes exceed 1GB without standalone output; additionally, persistent volumes mount over container directories, which would wipe default `learnings.md` and `rules.yaml` if not seeded on container initialization.
- **Working Solution**:
  - Configure `output: 'standalone'` in `next.config.mjs` and copy `.next/standalone`, `.next/static`, and `public` into an unprivileged Node 20 runner stage.
  - Implement a Python 3.12 stage using `uv` with compiler prerequisites (`build-essential`, `gcc`, `git`, `curl`), packaging the RHP CLI check tool at `/usr/local/bin/rhp`.
  - Add `docker-entrypoint.sh` to automatically seed `/data/learnings.md` and `/data/rules/` if empty on first volume mount.
  - Support configurable CORS origins via `CORS_ORIGINS`, persistent volume paths via `RHP_LEARNINGS_PATH` / `RHP_RULES_DIR`, and optional offline air-gapped migrations via Docker Compose `--profile local-llm` running Ollama.
- **Verification**: Verified via `npm run build` (standalone output created) and `pytest` (all 220 tests passing).
- **Key Heuristic**: In production container environments, use Next.js standalone output to reduce image footprint; seed persistent volumes via container entrypoint scripts so initial system memory is never blank.

---


## 🔄 Graduation Pipeline (From Working Memory to Core Rules)

When an entry in `learnings.md` proves repeatedly beneficial for PHP-to-RHP transformations:
1. Synthesize the pattern into a formal rule format with `before` and `after` code blocks.
2. Add it to `rhp_migrate/rules/rules.yaml` (for deterministic/LLM prompt targeting) or `rhp_migrate/rules/context.md`.
3. Mark the entry below as **Graduated to Core Rules**.

### Graduated Entries
- **[2026-11-21] Prefix-Typed Variable Declaration Syntax** -> Graduated to [`RHP-SYN-007`](file:///home/yogesh/code/rhp-migrate-ai/rhp_migrate/rules/rules.yaml) and Section 3 in [`context.md`](file:///home/yogesh/code/rhp-migrate-ai/rhp_migrate/rules/context.md).

