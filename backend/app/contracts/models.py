from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from contracts.models import GenerationError, GenerationResult, JobType
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

# ... existing routes unchanged; formatting-only touch here.

chat_callback_router = APIRouter(prefix="/internal/chat", tags=["Internal Chat Callbacks"])

__all__ = [
    "router",
    "chat_callback_router",
]
