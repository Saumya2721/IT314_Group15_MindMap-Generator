from datetime import datetime
from typing import Any

from pydantic import BaseModel


class MessageCreate(BaseModel):
    map_id: str
    content: str
    proposed_action: dict[str, Any] | None = None


class MessageResponse(BaseModel):
    id: str
    map_id: str
    user_id: str | None = None
    role: str
    content: str
    proposed_action: dict[str, Any] | None = None
    is_confirmed: bool | None = False
    created_at: datetime


class ActionConfirm(BaseModel):
    is_confirmed: bool


class ActionConfirmResponse(BaseModel):
    message: str
    is_confirmed: bool
    data: dict[str, Any] | None = None
    updated_message: MessageResponse | None = None
