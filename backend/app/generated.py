from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from contracts.models import GenerationResult, JobType
from core.supabase import bearer
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ValidationError

from db.connection import transaction
from db.models import EdgeRecord, NodeRecord, SeedAssetType
from db.repository import (
    get_or_create_chat_thread,
    insert_chat_message,
    insert_seed_asset,
    persist_generated_graph,
    replace_map_graph,
    replace_node_branch,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal API"])


class CallbackAuthRequest(BaseModel):
    token: str


async def _require_internal_token(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = authorization.credentials
    expected = request.app.state.internal_api_token
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid internal token")


@router.post("/jobs/{job_id}/complete")
async def report_complete(
    job_id: UUID,
    payload: GenerationResult,
    _auth: None = Depends(_require_internal_token),
) -> dict[str, Any]:
    return {"status": "received", "job_id": str(job_id)}


@router.post("/jobs/{job_id}/fail")
async def report_fail(
    job_id: UUID,
    payload: GenerationResult,
    _auth: None = Depends(_require_internal_token),
) -> dict[str, Any]:
    return {"status": "received", "job_id": str(job_id)}


chat_callback_router = APIRouter(prefix="/internal/chat", tags=["Internal Chat Callbacks"])

__all__ = ["router", "chat_callback_router"]
