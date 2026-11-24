---
name: rhp-migration
description: Expert guidelines, type system mappings, and workflow patterns for migrating PHP and Laravel codebases to strictly-typed RHP.
---

# RHP Migration Skill & Knowledge Base

## Overview
This skill guides the AI agent through migrating legacy dynamically-typed PHP (PHP 7.4 through PHP 8.3+) codebases to **RHP** (a strictly-typed PHP superset compiled ahead-of-time).

---

## 1. Migration Pipeline Sequence (Whole-Project 3-Stage Architecture)

The migration pipeline operates **project-wide** across 3 distinct tiers. AI is **never** invoked on every file indiscriminately; instead, deterministic CLI tools execute first across the whole project, and AI is triggered **strictly on the errors and warnings** produced at each stage to fix them and explain the root cause:

1. **Target PHP Version Discovery**:
   - Detect version from `composer.json` (`require.php` or `config.platform.php`).
   - If absent, check `Dockerfile` (`FROM php:8.x...`).
   - If absent, inspect CLI `php -v`.

2. **Stage 1: Whole-Project Deterministic Migration (`rhp migrate`)**:
   - Native `rhp migrate` runs on the whole project first (or single target file).
   - Strips script tags (`<?php`, `?>`), replaces strict equality `===`/`!==`, removes `declare(strict_types=1);`, upgrades superglobals to `Request` facade, converts `curl_*` to `Http::*`.
   - Files with 0 errors and 0 warnings are migrated cleanly with **0 AI cost**.
   - **AI Operation on Errors/Warnings**: Triggered only for files/units where `rhp migrate` flagged errors or warnings, checking why the deterministic command was unsuccessful for that use case and healing the code.

3. **Stage 2: Whole-Project Linting (`rhp lint`)**:
   - Run `rhp lint` across the whole project (with `--fix` where auto-fixable).
   - If lint errors or warnings remain, **AI is invoked** to resolve them, enforce strict typing/naming conventions, and analyze why the migration command did not produce lint-clean code for that use case.

4. **Stage 3: Whole-Project Build & Compiler Contracts (`rhp build`)**:
   - Run `rhp build --format=json` across the project.
   - If build/compiler errors are produced, **AI is invoked in an iterative compiler-guided healing loop** (up to `max_retries`).
   - The AI checks why migration was not successful for that type/symbol contract use case, resolves all errors, and verifies that `rhp build` passes.
   - If unresolved after max retries, tags the failing unit with `// RHP-TODO(failed-build): <error>`.

5. **Continuous Improvement Critic & Feedback Loop**:
   - Evaluates failing use cases across 7 dimensions (deterministic mistakes, missed opportunities, lint issues, build gaps, LLM difficulties, language gaps, context proposals).
   - Saves findings to `.rhp/feedback/pending_context.md` for permanent rule improvement.

---

## 2. Type System Conventions

- **Primitive Types**: `int`, `float`, `string`, `bool`, `void`, `never`.
- **Arrays & Collections**:
  - Sequential Lists: `int[]`, `string[]`, `list<User>`.
  - Associative Maps: `dict<string, mixed>`, `map<string, int>`.
- **Nullable Types**: `?string`, `?User` (explicit `?` prefix).
- **Functions & Methods**: Return types and parameter types are mandatory. Untyped parameters are compile errors.

---

## 3. Reviewing & Approving Learned Context

When the AI critic discovers new patterns, it logs them into `.rhp/feedback/pending_context.md`.
To review and merge pending context proposals into the repository's permanent knowledge base:

```bash
rhp-migrate-ai approve-context --dir . --target rhp_migrate/rules/context.md
```
