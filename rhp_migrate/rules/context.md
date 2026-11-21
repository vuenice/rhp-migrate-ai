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
| `$var: Type = ...` | `Type $var = ...` | Prefix typing is mandatory in RHP |
| `ob_start() ... ob_get_clean()` | `View::render()` / string concat | Global buffering eliminated |
| `include 'view.php';` | `View::render('view')` / `@include()` | Standalone includes forbidden at parse time |
| `echo Response::html(...)` | `return Response::html(...)` | Controller `echo` does not write to HTTP buffer |

---

## 3. Rade Templates & View Conventions

1. **Companion Template Files (`.rade.rhp`)**:
   - Views reside under `resources/views/` (or configured RHP view directory).
   - Use extension `.rade.rhp` (e.g. `resources/views/partials/card.rade.rhp`).
   - Callers invoke views via `View::render('path.name', ['key' => $val])`.

2. **Rade Directives**:
   - Conditions: `@if ($cond) ... @elseif ($other) ... @else ... @endif`
   - Loops: `@foreach ($items as $item) ... @endforeach`, `@for ($i = 0; $i < $n; $i++) ... @endfor`
   - Sub-templates: `@include('partials.item_card')`
   - Escaped output: `{{ $var }}` (auto-escapes with `Html::specialChars`)
   - Raw output: `{!! $rawHtml !!}`

3. **Prefix-Typed Variable Declarations**:
   - Always declare typed variables using prefix syntax:
     ```php
     string $html = View::render('home.index');
     Product[] $products = Product::all();
     dict<string, mixed> $meta = ['count' => 10];
     ```
   - **Never** emit colon typing (`$html: string = ...;`), which triggers `Colon type syntax '$var: Type' is not supported`.

---

## 4. Approved Learned Rules & Patterns (Auto-Merged from Agent Feedback)

