"""Rules and system learnings endpoints for reading, updating, and approving knowledge base files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from rhp_migrate.core.feedback import FeedbackDispatcher
from rhp_migrate.rules.registry import RuleRegistry
from rhp_migrate.rules.selector import (
    RuleSelector,
    append_learned_fix,
    detect_ast_tags,
    format_learned_fix_entry,
    select_rules,
)
from rhp_migrate.server.config import (
    find_workspace_root,
    get_context_path,
    get_learnings_path,
    get_pending_context_path,
    get_rules_yaml_path,
)
from rhp_migrate.server.models import (
    ActiveRuleItem,
    ActiveRulesRequest,
    ActiveRulesResponse,
    PendingProposalItem,
    PendingProposalsResponse,
    RuleActionResponse,
    RuleFileAppendRequest,
    RuleFileResponse,
    RuleFileUpdateRequest,
)

router = APIRouter()


def _resolve_code(
    file_path: Optional[str] = None,
    code: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> str:
    """Resolve code from inline string or from filesystem/workspace."""
    if code is not None:
        return code
    if not file_path:
        return ""

    candidates: list[Path] = []
    if workspace_id:
        try:
            from rhp_migrate.server.workspace import DEFAULT_WORKSPACES_ROOT
            candidates.append(DEFAULT_WORKSPACES_ROOT / workspace_id / file_path)
        except Exception:
            pass

    root = find_workspace_root()
    candidates.append(root / file_path)
    candidates.append(Path(file_path))

    for cand in candidates:
        if cand.is_file():
            try:
                return cand.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return ""


# ---------------------------------------------------------------------------
# AST-Targeted Active Rules & Working Memory Inspector Endpoints
# ---------------------------------------------------------------------------

@router.post("/rules/active", response_model=ActiveRulesResponse)
async def get_active_rules_for_file(req: ActiveRulesRequest) -> ActiveRulesResponse:
    """Detect AST language constructs and fetch matching migration rules & heuristics for a file."""
    source = _resolve_code(
        file_path=req.file_path,
        code=req.code,
        workspace_id=req.workspace_id,
    )

    selector = RuleSelector(learnings_path=get_learnings_path())
    detected_tags = sorted(list(selector.detect_tags(source))) if source else []
    matched_rules = selector.select_rules(source) if source else []
    heuristics = selector.get_active_heuristics()
    system_prompt = selector.build_system_prompt(source_or_tree=source, rules=matched_rules, heuristics=heuristics)

    rule_items = [
        ActiveRuleItem(
            id=r.id,
            title=r.title,
            category=r.category.value if hasattr(r.category, "value") else str(r.category),
            strategy=r.strategy.value if hasattr(r.strategy, "value") else str(r.strategy),
            php_pattern=r.php_pattern,
            rhp_replacement=r.rhp_replacement,
            unsupported=r.unsupported,
            doc_url=r.doc_url,
            llm_prompt_guidance=r.llm_prompt_guidance,
            tags=r.tags,
            examples=[
                {
                    "before": eg.before,
                    "after": eg.after,
                    "explanation": eg.explanation,
                }
                for eg in r.examples
            ],
        )
        for r in matched_rules
    ]

    return ActiveRulesResponse(
        file_path=req.file_path,
        detected_tags=detected_tags,
        rules=rule_items,
        heuristics=heuristics,
        rules_count=len(rule_items),
        heuristics_count=len(heuristics),
        system_prompt_preview=system_prompt,
    )


@router.get("/rules/active", response_model=ActiveRulesResponse)
async def get_active_rules_for_file_get(
    file_path: Optional[str] = Query(None, description="Path to file to inspect"),
    workspace_id: Optional[str] = Query(None, description="Workspace ID"),
) -> ActiveRulesResponse:
    """GET endpoint to fetch active rules for a file in a workspace."""
    req = ActiveRulesRequest(file_path=file_path, workspace_id=workspace_id)
    return await get_active_rules_for_file(req)


# ---------------------------------------------------------------------------
# Learnings (learnings.md) Endpoints
# ---------------------------------------------------------------------------

@router.get("/rules/learnings", response_model=RuleFileResponse)
async def get_learnings() -> RuleFileResponse:
    """Read the current content and metadata of learnings.md (system memory)."""
    learnings_path = get_learnings_path()
    if not learnings_path.is_file():
        return RuleFileResponse(
            file_name="learnings.md",
            file_path=str(learnings_path),
            content="",
            exists=False,
            size_bytes=0,
        )

    content = learnings_path.read_text(encoding="utf-8")
    return RuleFileResponse(
        file_name="learnings.md",
        file_path=str(learnings_path),
        content=content,
        exists=True,
        size_bytes=len(content.encode("utf-8")),
    )


@router.put("/rules/learnings", response_model=RuleActionResponse)
async def update_learnings(req: RuleFileUpdateRequest) -> RuleActionResponse:
    """Overwrite the full content of learnings.md."""
    learnings_path = get_learnings_path()
    learnings_path.parent.mkdir(parents=True, exist_ok=True)
    learnings_path.write_text(req.content, encoding="utf-8")

    return RuleActionResponse(
        success=True,
        message="learnings.md successfully updated.",
        file_path=str(learnings_path),
        size_bytes=len(req.content.encode("utf-8")),
    )


@router.post("/rules/learnings", response_model=RuleActionResponse)
@router.post("/rules/learnings/append", response_model=RuleActionResponse)
async def append_learning_entry(req: RuleFileAppendRequest) -> RuleActionResponse:
    """Append a structured learning entry or fix to learnings.md."""
    learnings_path = get_learnings_path()

    # If structured fields are provided, format according to learnings.md schema
    if req.context_symptom or req.key_heuristic or req.working_solution or req.root_cause:
        append_learned_fix(
            learnings_path=learnings_path,
            title=req.title or "Workbench Migration Fix",
            context_symptom=req.context_symptom,
            root_cause=req.root_cause,
            working_solution=req.working_solution,
            verification=req.verification,
            key_heuristic=req.key_heuristic,
        )
    else:
        # Fallback to direct content append
        learnings_path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        header = f"\n\n### [{today}] {req.title}\n" if req.title else "\n\n"
        content_str = req.content.strip() if req.content else ""
        append_block = f"{header}{content_str}\n"

        with open(learnings_path, "a", encoding="utf-8") as f:
            f.write(append_block)

    total_size = learnings_path.stat().st_size if learnings_path.exists() else 0
    return RuleActionResponse(
        success=True,
        message="New entry appended to learnings.md.",
        file_path=str(learnings_path),
        size_bytes=total_size,
    )


# ---------------------------------------------------------------------------
# Context (rhp_migrate/rules/context.md) Endpoints
# ---------------------------------------------------------------------------

@router.get("/rules/context", response_model=RuleFileResponse)
async def get_context() -> RuleFileResponse:
    """Read the current content and metadata of rhp_migrate/rules/context.md."""
    context_path = get_context_path()
    if not context_path.is_file():
        return RuleFileResponse(
            file_name="context.md",
            file_path=str(context_path),
            content="",
            exists=False,
            size_bytes=0,
        )

    content = context_path.read_text(encoding="utf-8")
    return RuleFileResponse(
        file_name="context.md",
        file_path=str(context_path),
        content=content,
        exists=True,
        size_bytes=len(content.encode("utf-8")),
    )


@router.put("/rules/context", response_model=RuleActionResponse)
async def update_context(req: RuleFileUpdateRequest) -> RuleActionResponse:
    """Overwrite the full content of rhp_migrate/rules/context.md."""
    context_path = get_context_path()
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(req.content, encoding="utf-8")

    return RuleActionResponse(
        success=True,
        message="context.md successfully updated.",
        file_path=str(context_path),
        size_bytes=len(req.content.encode("utf-8")),
    )


@router.post("/rules/context", response_model=RuleActionResponse)
async def append_context_section(req: RuleFileAppendRequest) -> RuleActionResponse:
    """Append a rule or section to rhp_migrate/rules/context.md."""
    context_path = get_context_path()
    context_path.parent.mkdir(parents=True, exist_ok=True)

    header = f"\n\n## {req.title}\n\n" if req.title else "\n\n"
    content_str = req.content.strip() if req.content else ""
    append_block = f"{header}{content_str}\n"

    with open(context_path, "a", encoding="utf-8") as f:
        f.write(append_block)

    total_size = context_path.stat().st_size
    return RuleActionResponse(
        success=True,
        message="Section appended to context.md.",
        file_path=str(context_path),
        size_bytes=total_size,
    )


# ---------------------------------------------------------------------------
# Pending Proposals & Approval Endpoints
# ---------------------------------------------------------------------------

@router.get("/rules/pending", response_model=PendingProposalsResponse)
async def get_pending_proposals() -> PendingProposalsResponse:
    """Fetch pending context proposals from .rhp/feedback/pending_context.md."""
    root = find_workspace_root()
    dispatcher = FeedbackDispatcher(base_dir=root)
    raw_proposals = dispatcher.load_pending_contexts()

    items = [
        PendingProposalItem(
            id=p.get("id", ""),
            file=p.get("file", ""),
            php=p.get("php", "auto"),
            content=p.get("content", ""),
        )
        for p in raw_proposals
    ]

    return PendingProposalsResponse(
        count=len(items),
        pending_file=str(dispatcher.pending_context_path),
        proposals=items,
    )


@router.post("/rules/context/approve", response_model=RuleActionResponse)
async def approve_all_pending_rules() -> RuleActionResponse:
    """Approve all pending context proposals and merge them into context.md."""
    root = find_workspace_root()
    context_path = get_context_path()
    dispatcher = FeedbackDispatcher(base_dir=root)

    count = dispatcher.approve_all_pending(context_path)
    total_size = context_path.stat().st_size if context_path.exists() else 0

    return RuleActionResponse(
        success=True,
        message=f"Successfully approved and merged {count} proposal(s) into context.md.",
        file_path=str(context_path),
        size_bytes=total_size,
    )
