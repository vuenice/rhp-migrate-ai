# RHP Migration Context & Knowledge Base

This document serves as the living knowledge base for migrating PHP/Laravel codebases to strictly-typed, ahead-of-time compiled RHP.

---

## 1. Core Architectural Tenets

1. **Strict Static Typing Ahead-of-Time**:
   - Every parameter, return type, and property must have an explicit static type.
   - Dynamic typing (`mixed` without guards) should be minimized.
   - Polymorphic arrays are disambiguated: sequential lists are `T[]` / `list<T>`, associative dictionaries are `dict<string, T>` / `map<string, T>`.

2. **Zero Runtime Reflection**:
   - PHP reflection (`ReflectionClass`, `ReflectionMethod`, variable variables `$$var`, `eval`) is prohibited in RHP.
   - Refactor into constructor dependency injection, explicit interfaces, or compile-time registries.

3. **Deterministic First, AI Last**:
   - Native `rhp migrate` handles deterministic syntax updates (tags, operators, basic casts, globals).
   - Native `rhp lint` enforces coding standards and flags syntax anti-patterns.
   - Native `rhp check` acts as the compiler source of truth.
   - AI agent mediates complex type disambiguation, refactoring dynamic patterns, and continuous feedback.

---

## 2. Standard Replacements Table

| PHP Construct | RHP Equivalent | Reason |
| :--- | :--- | :--- |
| `<?php` / `?>` | Removed | Direct pure source execution |
| `===` / `!==` | `==` / `!=` | RHP has no dynamic type coercion |
| `declare(strict_types=1);` | Stripped | Strict typing is compiler default |
| `$_GET['k']` / `$_POST['k']` | `Request::query('k')` / `Request::input('k')` | Framework Request Facade |
| `curl_init` / `curl_exec` | `Http::get()` / `Http::post()` | High-performance async HTTP |
| `@error_suppress()` | Stripped | Proper exception handling mandatory |
| `var_dump()` | `dd()` | RHP debug utility |

---

## 3. Approved Learned Rules & Patterns (Auto-Merged from Agent Feedback)
