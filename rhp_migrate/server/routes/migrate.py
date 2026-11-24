"""File migration and real-time Server-Sent Events (SSE) streaming routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import traceback
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from rhp_migrate.ai.client import LLMRunner
from rhp_migrate.ai.critic import MigrationCritic
from rhp_migrate.analyzer.parser import PHPParser
from rhp_migrate.analyzer.readiness import ReadinessAnalyzer
from rhp_migrate.engine.fallback_rules import apply_fallback_rules
from rhp_migrate.rules.selector import RuleSelector
from rhp_migrate.engine.rhp_cli import (
    RhpNotFoundError,
    find_rhp_binary,
    run_check,
    run_lint,
    run_migrate,
)
from rhp_migrate.server.byok import (
    BYOKCredentials,
    create_runner_from_byok,
    extract_byok_from_headers,
)
from rhp_migrate.server.models import (
    CompanionFileItem,
    MigrateFileRequest,
    MigrateFileResponse,
    MigrateStreamRequest,
)
from rhp_migrate.verify.compiler_loop import (
    CodeUnit,
    clean_code_fence,
    find_code_unit,
    insert_todo_above_unit,
    is_syntactically_valid,
    replace_unit_in_source,
)
from rhp_migrate.verify.deterministic_audit import DeterministicValidator

router = APIRouter()


def format_sse(event: str, data: dict[str, Any]) -> str:
    """Format an SSE frame with event name and JSON payload."""
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/migrate/file", response_model=MigrateFileResponse)
async def migrate_file_endpoint(
    req: MigrateFileRequest,
    byok: BYOKCredentials = Depends(extract_byok_from_headers),
) -> MigrateFileResponse:
    """Synchronously migrate a PHP file into strictly-typed RHP code with BYOK LLM integration."""
    llm_runner = create_runner_from_byok(byok, max_retries=req.max_retries)
    file_path = Path(req.file_path or "input.php")
    original_code = req.code
    current_content = original_code
    applied_rules: list[str] = []
    companion_files: list[CompanionFileItem] = []
    deterministic_edits = 0
    todos_marked = 0

    # 1. Native rhp migrate (if binary present)
    rhp_bin = find_rhp_binary()
    migrate_rep = None
    if rhp_bin:
        try:
            migrate_rep = run_migrate(
                Path(file_path).parent,
                file_path=file_path,
                dry_run=True,
                bin_path=rhp_bin,
            )
            file_res = migrate_rep.get_file_result(file_path)
            if file_res and file_res.modified:
                deterministic_edits += 1
        except Exception:
            pass

    # 2. Deterministic AST fallback rules
    fb_res = apply_fallback_rules(current_content)
    if fb_res.modified:
        deterministic_edits += len(fb_res.edits)
        current_content = fb_res.transformed_code
        applied_rules.extend(fb_res.applied_rules)

    initial_draft = current_content

    # 3. Deterministic Syntax Audit
    validator = DeterministicValidator()
    audit_record = validator.validate(
        before_code=original_code,
        after_code=current_content,
        file_path=file_path,
        bin_path=rhp_bin,
    )
    deterministic_compiler_errors: list[str] = []
    if not audit_record.is_valid:
        for err in audit_record.errors:
            deterministic_compiler_errors.append(f"line {err.line}:{err.column}: {err.message}")

    # 4. LLM Migration of untyped/unsupported units
    selector = RuleSelector()
    file_rules = selector.select_rules(current_content)
    applied_rules.extend([r.id for r in file_rules])

    analyzer = ReadinessAnalyzer()
    file_rep = analyzer.analyze_file(file_path, source=current_content)
    units_to_migrate: list[CodeUnit] = []
    seen_units: set[str] = set()

    for diag in file_rep.diagnostics:
        unit = find_code_unit(current_content, diag.line, file_path=file_path)
        if unit.identifier not in seen_units and not unit.is_whole_file:
            seen_units.add(unit.identifier)
            units_to_migrate.append(unit)

    if not units_to_migrate and not audit_record.is_valid and audit_record.errors:
        unit = find_code_unit(current_content, audit_record.errors[0].line, file_path=file_path)
        if unit.identifier not in seen_units:
            seen_units.add(unit.identifier)
            units_to_migrate.append(unit)

    # If no specific functions/methods flagged, but file has untyped parameters or diagnostics, migrate whole unit
    if not units_to_migrate and (file_rep.untyped_param_count > 0 or file_rep.diagnostics):
        unit = find_code_unit(current_content, 1, file_path=file_path)
        units_to_migrate.append(unit)

    healing_log: list[str] = []
    for unit in units_to_migrate:
        try:
            unit_rules = selector.select_rules(unit.code) or file_rules
            ai_resp = llm_runner.migrate_unit(
                unit_identifier=unit.identifier,
                code=unit.code,
                rules=unit_rules,
                compiler_errors=deterministic_compiler_errors if deterministic_compiler_errors else None,
            )
            transformed = clean_code_fence(ai_resp.result.transformed_code)
            if transformed and transformed != unit.code:
                current_content = replace_unit_in_source(current_content, unit, transformed)
                applied_rules.extend(ai_resp.result.applied_rules)

            if ai_resp.result.companion_files:
                for comp in ai_resp.result.companion_files:
                    companion_files.append(
                        CompanionFileItem(file_path=comp.file_path, content=comp.content)
                    )

            if ai_resp.result.needs_human_review:
                current_content = insert_todo_above_unit(
                    current_content,
                    line_number=unit.start_line,
                    error_message=ai_resp.result.review_reason or "Review required",
                    file_path=file_path,
                )
                todos_marked += 1
        except Exception as exc:
            healing_log.append(f"LLM unit migration failed for {unit.identifier}: {exc}")

    # 5. Final Validation Check
    final_audit = validator.validate(
        before_code=original_code,
        after_code=current_content,
        file_path=file_path,
        bin_path=rhp_bin,
    )
    is_valid = final_audit.is_valid and is_syntactically_valid(current_content)
    compiler_errors = [f"line {e.line}:{e.column}: {e.message}" for e in final_audit.errors]

    # Count // RHP-TODO
    file_todos = len(re.findall(r"//\s*RHP-TODO", current_content))
    if file_todos > todos_marked:
        todos_marked = file_todos

    # 6. Critic Reflection (if enabled)
    critic_report_dict: Optional[dict[str, Any]] = None
    if req.run_critic:
        try:
            critic = MigrationCritic(runner=llm_runner)
            report = critic.analyze(
                file_path=file_path,
                php_version=req.php_version or "8.2",
                original_code=original_code,
                migrated_code=initial_draft,
                final_code=current_content,
                healing_log=healing_log,
            )
            critic_report_dict = report.model_dump(mode="json")
        except Exception:
            pass

    return MigrateFileResponse(
        file_path=str(file_path),
        original_code=original_code,
        migrated_code=initial_draft,
        final_code=current_content,
        is_valid=is_valid,
        deterministic_edits=deterministic_edits,
        applied_rules=sorted(list(set(applied_rules))),
        todos_marked=todos_marked,
        companion_files=companion_files,
        compiler_errors=compiler_errors,
        critic_report=critic_report_dict,
        stats=llm_runner.get_usage_summary(),
    )


@router.post("/migrate/stream")
async def migrate_stream_endpoint(
    req: MigrateStreamRequest,
    byok: BYOKCredentials = Depends(extract_byok_from_headers),
) -> StreamingResponse:
    """Stream migration progress in real-time via Server-Sent Events (SSE).

    SSE Stages Emitted:
    1. ast_parsing: AST readiness, unsupported features, untyped signatures
    2. initial_rhp_draft: Deterministic syntax rules & initial LLM draft
    3. compiler_check: Syntax verification & compiler diagnostic feedback
    4. critic_reflection: AI critic evaluation, mistake audit & context proposal
    5. final_code: Final verified RHP code, TODOs, token usage & cost summary
    6. done: Migration lifecycle completion
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        file_path = Path(req.file_path or "input.php")
        original_code = req.code
        current_content = original_code
        llm_runner = create_runner_from_byok(byok, max_retries=req.max_retries)
        applied_rules: list[str] = []
        companion_files: list[dict[str, str]] = []
        healing_log: list[str] = []
        deterministic_edits = 0
        todos_marked = 0

        try:
            # ---------------------------------------------------------------
            # Stage 1: AST Parsing & Readiness
            # ---------------------------------------------------------------
            yield format_sse(
                "status",
                {"step": "analyzing", "message": f"Analyzing AST and readiness for {file_path.name}"},
            )
            yield format_sse(
                "ast_parsing",
                {
                    "stage": "ast_parsing",
                    "status": "started",
                    "message": f"Analyzing AST and readiness for {file_path.name}",
                },
            )
            await asyncio.sleep(0.01)

            analyzer = ReadinessAnalyzer()
            file_rep = analyzer.analyze_file(file_path, source=current_content)

            yield format_sse(
                "ast_parsing",
                {
                    "stage": "ast_parsing",
                    "status": "completed",
                    "data": {
                        "file_path": str(file_path),
                        "readiness_percentage": file_rep.readiness_percentage,
                        "unsupported_count": file_rep.unsupported_count,
                        "untyped_param_count": file_rep.untyped_param_count,
                        "classes": file_rep.classes,
                        "functions": file_rep.functions,
                        "diagnostics": [
                            {
                                "line": d.line,
                                "column": d.column,
                                "severity": d.severity.value if hasattr(d.severity, "value") else str(d.severity),
                                "rule_id": d.rule_id,
                                "message": d.message,
                            }
                            for d in file_rep.diagnostics
                        ],
                    },
                },
            )
            await asyncio.sleep(0.01)

            # ---------------------------------------------------------------
            # Stage 2: Initial RHP Draft (Deterministic + LLM Migration)
            # ---------------------------------------------------------------
            yield format_sse(
                "status",
                {"step": "transpiling", "message": "Transpiling PHP code into strictly typed RHP"},
            )
            yield format_sse(
                "initial_rhp_draft",
                {
                    "stage": "initial_rhp_draft",
                    "status": "started",
                    "message": "Applying deterministic AST rules and generating initial RHP draft",
                },
            )
            await asyncio.sleep(0.01)

            # Deterministic AST rules
            fb_res = apply_fallback_rules(current_content)
            if fb_res.modified:
                deterministic_edits += len(fb_res.edits)
                current_content = fb_res.transformed_code
                applied_rules.extend(fb_res.applied_rules)

            initial_draft = current_content

            # Find units to migrate
            units_to_migrate: list[CodeUnit] = []
            seen_units: set[str] = set()
            for diag in file_rep.diagnostics:
                unit = find_code_unit(current_content, diag.line, file_path=file_path)
                if unit.identifier not in seen_units and not unit.is_whole_file:
                    seen_units.add(unit.identifier)
                    units_to_migrate.append(unit)

            if not units_to_migrate and (file_rep.untyped_param_count > 0 or file_rep.diagnostics):
                unit = find_code_unit(current_content, 1, file_path=file_path)
                units_to_migrate.append(unit)

            selector = RuleSelector()
            file_rules = selector.select_rules(current_content)
            applied_rules.extend([r.id for r in file_rules])

            for unit in units_to_migrate:
                try:
                    unit_rules = selector.select_rules(unit.code) or file_rules
                    ai_resp = llm_runner.migrate_unit(
                        unit_identifier=unit.identifier,
                        code=unit.code,
                        rules=unit_rules,
                        extra_instructions=req.user_instruction,
                    )
                    transformed = clean_code_fence(ai_resp.result.transformed_code)
                    if transformed and transformed != unit.code:
                        current_content = replace_unit_in_source(current_content, unit, transformed)
                        applied_rules.extend(ai_resp.result.applied_rules)

                    if ai_resp.result.companion_files:
                        for comp in ai_resp.result.companion_files:
                            companion_files.append({"file_path": comp.file_path, "content": comp.content})

                    if ai_resp.result.needs_human_review:
                        current_content = insert_todo_above_unit(
                            current_content,
                            line_number=unit.start_line,
                            error_message=ai_resp.result.review_reason or "Review required",
                            file_path=file_path,
                        )
                        todos_marked += 1
                except Exception as exc:
                    healing_log.append(f"LLM migration failed for {unit.identifier}: {exc}")

            # Stream incremental code chunks
            chunk_size = 64
            for i in range(0, len(current_content), chunk_size):
                chunk = current_content[i : i + chunk_size]
                yield format_sse("code_chunk", {"chunk": chunk})
                await asyncio.sleep(0.005)

            yield format_sse(
                "initial_rhp_draft",
                {
                    "stage": "initial_rhp_draft",
                    "status": "completed",
                    "data": {
                        "draft_code": current_content,
                        "deterministic_edits": deterministic_edits,
                        "units_migrated": len(units_to_migrate),
                        "applied_rules": sorted(list(set(applied_rules))),
                        "companion_files": companion_files,
                    },
                },
            )
            await asyncio.sleep(0.01)

            # ---------------------------------------------------------------
            # Stage 3: Compiler Check Output
            # ---------------------------------------------------------------
            yield format_sse(
                "status",
                {"step": "compiling", "message": "Executing deterministic syntax verification and compiler check"},
            )
            yield format_sse(
                "compiler_check",
                {
                    "stage": "compiler_check",
                    "status": "started",
                    "message": "Executing deterministic syntax verification and compiler check",
                },
            )
            await asyncio.sleep(0.01)

            rhp_bin = find_rhp_binary()
            validator = DeterministicValidator()
            audit_record = validator.validate(
                before_code=original_code,
                after_code=current_content,
                file_path=file_path,
                bin_path=rhp_bin,
            )
            is_valid = audit_record.is_valid and is_syntactically_valid(current_content)
            compiler_errors = [f"line {e.line}:{e.column}: {e.message}" for e in audit_record.errors]
            typed_diagnostics = [
                {
                    "line": e.line,
                    "col": e.column,
                    "message": e.message,
                }
                for e in audit_record.errors
            ]

            # Emit typed diagnostic errors for frontend editor gutter highlighting
            yield format_sse("diagnostic", {"errors": typed_diagnostics})

            yield format_sse(
                "compiler_check",
                {
                    "stage": "compiler_check",
                    "status": "completed",
                    "data": {
                        "is_valid": is_valid,
                        "errors": compiler_errors,
                        "what_is_invalid": audit_record.what_is_invalid,
                    },
                },
            )
            await asyncio.sleep(0.01)

            # ---------------------------------------------------------------
            # Stage 4: Critic Loop for Automated Repair
            # ---------------------------------------------------------------
            critic_data: Optional[dict[str, Any]] = None
            if not is_valid and audit_record.errors:
                yield format_sse(
                    "status",
                    {"step": "critic_review", "message": "Passing compiler diagnostics to Critic loop for automated repair"},
                )
                if req.max_retries > 0:
                    try:
                        failing_line = audit_record.errors[0].line
                        unit = find_code_unit(current_content, failing_line, file_path=file_path)
                        heal_resp = llm_runner.migrate_unit(
                            unit_identifier=unit.identifier,
                            code=unit.code,
                            compiler_errors=compiler_errors,
                            extra_instructions=req.user_instruction,
                        )
                        healed = clean_code_fence(heal_resp.result.transformed_code)
                        if healed and healed != unit.code:
                            current_content = replace_unit_in_source(current_content, unit, healed)
                            healing_log.append(f"Healed unit {unit.identifier}")

                            # Stream healed code chunks
                            for i in range(0, len(current_content), chunk_size):
                                chunk = current_content[i : i + chunk_size]
                                yield format_sse("code_chunk", {"chunk": chunk})
                                await asyncio.sleep(0.005)

                            # Re-verify
                            re_audit = validator.validate(
                                before_code=original_code,
                                after_code=current_content,
                                file_path=file_path,
                                bin_path=rhp_bin,
                            )
                            is_valid = re_audit.is_valid and is_syntactically_valid(current_content)
                            re_typed = [
                                {
                                    "line": e.line,
                                    "col": e.column,
                                    "message": e.message,
                                }
                                for e in re_audit.errors
                            ]
                            yield format_sse("diagnostic", {"errors": re_typed})
                    except Exception as exc:
                        healing_log.append(f"Healing retry failed: {exc}")
            elif req.run_critic:
                yield format_sse(
                    "status",
                    {"step": "critic_review", "message": "Running AI Continuous Improvement Critic evaluation"},
                )

            if req.run_critic:
                yield format_sse(
                    "critic_reflection",
                    {
                        "stage": "critic_reflection",
                        "status": "started",
                        "message": "Running AI Continuous Improvement Critic evaluation",
                    },
                )
                await asyncio.sleep(0.01)

                try:
                    critic = MigrationCritic(runner=llm_runner)
                    report = critic.analyze(
                        file_path=file_path,
                        php_version=req.php_version or "8.2",
                        original_code=original_code,
                        migrated_code=initial_draft,
                        final_code=current_content,
                        healing_log=healing_log,
                    )
                    critic_data = report.model_dump(mode="json")
                except Exception as exc:
                    critic_data = {"error": f"Critic evaluation skipped: {exc}"}

                yield format_sse(
                    "critic_reflection",
                    {
                        "stage": "critic_reflection",
                        "status": "completed",
                        "data": {
                            "critic_report": critic_data,
                            "healing_log": healing_log,
                        },
                    },
                )
                await asyncio.sleep(0.01)

            # ---------------------------------------------------------------
            # Stage 5: Final Verified RHP Code & Complete
            # ---------------------------------------------------------------
            file_todos = len(re.findall(r"//\s*RHP-TODO", current_content))
            todos_marked = max(todos_marked, file_todos)

            # Emit typed status: done
            yield format_sse(
                "status",
                {"step": "done", "message": "Migration completed successfully."},
            )

            # Emit typed complete event
            yield format_sse(
                "complete",
                {
                    "final_code": current_content,
                    "verified": is_valid,
                },
            )

            yield format_sse(
                "final_code",
                {
                    "stage": "final_code",
                    "status": "completed",
                    "data": {
                        "file_path": str(file_path),
                        "final_code": current_content,
                        "is_verified": is_valid,
                        "todos_count": todos_marked,
                        "stats": llm_runner.get_usage_summary(),
                    },
                },
            )
            await asyncio.sleep(0.01)

            # ---------------------------------------------------------------
            # Stage 6: Done
            # ---------------------------------------------------------------
            yield format_sse(
                "done",
                {
                    "stage": "done",
                    "status": "success",
                    "message": "Migration completed successfully.",
                },
            )

        except Exception as exc:
            yield format_sse(
                "error",
                {
                    "stage": "error",
                    "status": "failed",
                    "message": str(exc),
                    "detail": traceback.format_exc(),
                },
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
