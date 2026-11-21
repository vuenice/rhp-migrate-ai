"""Rules and system learnings endpoints for reading, updating, and approving knowledge base files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from rhp_migrate.core.feedback import FeedbackDispatcher
from rhp_migrate.server.config import (
    find_workspace_root,
    get_context_path,
    get_learnings_path,
    get_pending_context_path,
    get_rules_yaml_path,
)
from rhp_migrate.server.models import (
    PendingProposalItem,
    PendingProposalsResponse,
    RuleActionResponse,
    RuleFileAppendRequest,
    RuleFileResponse,
    RuleFileUpdateRequest,
)

router = APIRouter()


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
async def append_learning_entry(req: RuleFileAppendRequest) -> RuleActionResponse:
    """Append a structured learning entry to learnings.md."""
    learnings_path = get_learnings_path()
    learnings_path.parent.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"\n\n### [{today}] {req.title}\n" if req.title else "\n\n"
    append_block = f"{header}{req.content.strip()}\n"

    with open(learnings_path, "a", encoding="utf-8") as f:
        f.write(append_block)

    total_size = learnings_path.stat().st_size
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
    append_block = f"{header}{req.content.strip()}\n"

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
