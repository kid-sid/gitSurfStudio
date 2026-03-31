"""
Pydantic request/response models for the GitSurf Studio API.
"""

from typing import Optional, List, Dict
from pydantic import BaseModel, field_validator


# ── Request Models ─────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    query: str
    path: str
    history: Optional[List[Dict[str, str]]] = []
    user_id: Optional[str] = None   # Supabase auth user ID, passed by frontend
    agent_mode: bool = False        # True = Plan→Execute→Verify pipeline

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query cannot be empty")
        if len(v) > 20_000:
            raise ValueError("query exceeds maximum length of 20,000 characters")
        return v

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class IndexRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class InitRequest(BaseModel):
    input: str
    user_id: Optional[str] = None   # Supabase auth user ID (for persistent memory)

    @field_validator("input")
    @classmethod
    def input_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("input cannot be empty")
        return v


class AutocompleteRequest(BaseModel):
    code_context: str
    file_path: str
    path: str

    @field_validator("code_context")
    @classmethod
    def context_max_length(cls, v: str) -> str:
        if len(v) > 100_000:
            raise ValueError("code_context exceeds maximum length of 100,000 characters")
        return v


class WriteRequest(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v

    @field_validator("content")
    @classmethod
    def content_max_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 10 * 1024 * 1024:  # 10 MB
            raise ValueError("content exceeds maximum size of 10 MB")
        return v


class RestoreRequest(BaseModel):
    path: str


class CompleteRequest(BaseModel):
    path: str
    prefix: str
    suffix: str
    language: str = "plaintext"

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class GitStatusRequest(BaseModel):
    path: str


class GitStageRequest(BaseModel):
    path: str
    files: List[str]

    @field_validator("files")
    @classmethod
    def files_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("files list cannot be empty")
        return v


class GitCommitRequest(BaseModel):
    path: str
    message: str

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("commit message cannot be empty")
        return v


class GitForkRequest(BaseModel):
    path: str
    repo_name: str


class GitCheckoutRequest(BaseModel):
    path: str
    branch: str


class GitStashRequest(BaseModel):
    path: str


class GitDiscardRequest(BaseModel):
    path: str
    file: str


class MkdirRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class RenameRequest(BaseModel):
    old_path: str
    new_path: str


class LintRequest(BaseModel):
    file_path: str
    content: str
    workspace: str = ""

    @field_validator("content")
    @classmethod
    def content_max_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 1 * 1024 * 1024:  # 1 MB cap for linting
            raise ValueError("content too large for linting")
        return v


class FixLintRequest(BaseModel):
    file_path: str
    content: str
    diagnostics: list = []


class SessionRequest(BaseModel):
    user_id: str
    repo_identifier: str


class NewSessionRequest(BaseModel):
    user_id: str
    repo_identifier: str
    title: Optional[str] = None


class AgentRollbackRequest(BaseModel):
    changeset_id: str
    file_path: Optional[str] = None  # None = rollback all


class AgentRespondRequest(BaseModel):
    response: str


# ── Response Models (for OpenAPI docs) ─────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    workspace: Optional[str]


class InitResponse(BaseModel):
    status: str
    workspace_path: str
    has_project_context: bool
    is_github: bool


class ReadResponse(BaseModel):
    content: str


class WriteResponse(BaseModel):
    status: str
    message: str


class GitStatusResponse(BaseModel):
    status: List[Dict[str, str]]


class GitMessageResponse(BaseModel):
    message: str


class BranchResponse(BaseModel):
    current: Optional[str]
    branches: List[str]


class SymbolResponse(BaseModel):
    path: str
    symbols: List[Dict]


class AutocompleteResponse(BaseModel):
    completion: str


class CacheStatusResponse(BaseModel):
    total_size_mb: float
    repo_count: int
    repos: List[Dict]
    indexes: Dict[str, float]


class CacheCleanupResponse(BaseModel):
    status: str
    repos_removed: int
    space_freed_mb: float
