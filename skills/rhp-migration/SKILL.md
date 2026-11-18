---
name: rhp-migration
description: Expert guidelines, type system mappings, and workflow patterns for migrating PHP and Laravel codebases to strictly-typed RHP.
---

# RHP Migration Skill & Knowledge Base

## Overview
This skill guides the AI agent through migrating legacy dynamically-typed PHP (PHP 7.4 through PHP 8.3+) codebases to **RHP** (a strictly-typed PHP superset compiled ahead-of-time).

---

## 1. Migration Pipeline Sequence

1. **Target PHP Version Discovery**:
   - Detect version from `composer.json` (`require.php` or `config.platform.php`).
   - If absent, check `Dockerfile` (`FROM php:8.x...`).
   - If absent, inspect CLI `php -v`.

2. **Deterministic Stage (`rhp migrate`)**:
   - Strip script tags (`<?php`, `?>`).
   - Replace strict equality `===` / `!==` with `==` / `!=`.
   - Strip `declare(strict_types=1);`.
   - Upgrade superglobals (`$_GET`, `$_POST`) to `Request` facade.
   - Convert `curl_*` to `Http::*`.

3. **Linting Stage (`rhp lint`)**:
   - Check code formatting, unused variables, and style rules.
   - Run `--fix` where auto-fixable.

4. **Compiler Type-Checking (`rhp check`)**:
   - Compiler is the source of truth.
   - Run `rhp check --format=json`.

5. **AI Healing & Disambiguation**:
   - Resolve leftover compiler errors iteratively.
   - Disambiguate PHP arrays into `T[]` or `dict<string, T>`.
   - Eliminate runtime reflection (`ReflectionClass`, `eval`, `$$var`) using dependency injection or static registries.

6. **Continuous Improvement Critic & Feedback Loop**:
   - Critique every file migration across 7 dimensions:
     1. Mistakes made by `rhp migrate` (with before/after/expected samples).
     2. Missed deterministic opportunities for `rhp migrate`.
     3. Mistakes made by `rhp lint`.
     4. Missed linting opportunities.
     5. Complexities/difficulties faced by LLM.
     6. Missing RHP language features or standard library gaps.
     7. Formulate a markdown block (`context_proposal`) ready for approval.
   - Dispatches reports to `.rhp/feedback/` and centralized remote endpoint.

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
