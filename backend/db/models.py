"""
Backend database models mirroring the PostgreSQL / Supabase schema.

Source of truth for the schema: supabase/migrations/ (applied with
`supabase db push`). backend/db/schema.sql is the same final schema as one
script, for a fresh project set up through the SQL Editor.

Enum values match the CHECK constraints in the database and the wire
contract in llm/mindmap_llm/contracts/models.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class JobType(StrEnum):
    GENERATE = "generate"
    REGENERATE_FULL = "regenerate_full"
    REGENERATE_NODE = "regenerate_node"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


# Terminal statuses. A job in one of these never changes again.
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED}
)


class SeedAssetType(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"


class EmbeddingSource(StrEnum):
    UPLOADED_SEED = "uploaded_seed"
    WEB_SEARCH = "web_search"
    ARXIV = "arxiv"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


# Column limits from the schema. repository.py clips values to these so a
# long LLM string can never make a whole worker callback fail.
NODE_LABEL_MAX = 255
NODE_TYPE_MAX = 50
EDGE_LABEL_MAX = 100
FILE_NAME_MAX = 255
MIME_TYPE_MAX = 100
ERROR_MESSAGE_MAX = 4000


@dataclass
class GenerationJob:
    id: UUID
    map_id: UUID
    triggered_by_message_id: UUID | None
    job_type: JobType
    target_node_id: UUID | None
    status: JobStatus
    progress_percent: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    callback_token_hash: str | None = None


@dataclass
class SeedAsset:
    id: UUID
    generation_job_id: UUID
    asset_type: SeedAssetType
    file_name: str | None
    storage_key: str | None
    mime_type: str | None
    content_text: str | None
    file_size_bytes: int | None
    checksum_sha256: str | None


@dataclass
class NodeRecord:
    id: UUID
    map_id: UUID
    label: str
    description: str | None
    pos_x: float = 0.0
    pos_y: float = 0.0
    background_color: str | None = None
    text_color: str | None = None
    node_type: str = "default"
    source_job_id: UUID | None = None
    node_data: dict | None = None


@dataclass
class EdgeRecord:
    id: UUID
    map_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    label: str | None = None
    edge_data: dict | None = None


@dataclass
class EmbeddingRecord:
    id: UUID | None
    generation_job_id: UUID
    map_id: UUID
    chunk_text: str
    embedding_model: str
    source: EmbeddingSource
    pinecone_vector_id: str | None = None
    source_title: str | None = None
    source_url: str | None = None


@dataclass
class ChatMessage:
    id: UUID
    thread_id: UUID
    role: str  # "user" | "assistant"
    content: str
