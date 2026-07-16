"""Typed contracts for code-to-documentation linking."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CodeElementType(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    VARIABLE = "variable"
    IMPORT = "import"
    DECORATOR = "decorator"


class CodeToken(BaseModel):
    """A semantic token extracted from source code via AST parsing."""

    element_id: str = Field(..., description="Unique identifier (file:path:element:name)")
    element_type: CodeElementType
    name: str
    file_path: str
    line_start: int
    line_end: int
    signature: str = Field("", description="Function or class signature")
    docstring: str = ""
    source_hash: str = Field(..., description="SHA-256 hash of the element source text")
    embedding: list[float] | None = Field(None, description="Vector embedding of the element")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarkdownBlock(BaseModel):
    """A semantic block parsed from a Markdown documentation file."""

    block_id: str = Field(..., description="Unique identifier (file:heading:hash)")
    file_path: str
    heading: str
    content: str
    line_start: int
    line_end: int
    linked_code_path: str | None = Field(None, description="Code file referenced by this block")
    linked_element: str | None = Field(None, description="Code element name referenced")
    embedding: list[float] | None = None
    source_hash: str = ""


class CodeDocLink(BaseModel):
    """A cosine similarity link between a code token and a markdown block."""

    code_token_id: str
    markdown_block_id: str
    cosine_similarity: float = Field(..., ge=-1.0, le=1.0)
    is_stale: bool = False
    staleness_reason: str = ""


class GitDiffEntry(BaseModel):
    """A single file change in a git diff."""

    file_path: str
    change_type: str = Field(..., description="added | modified | deleted | renamed")
    old_path: str | None = None
    added_lines: list[int] = Field(default_factory=list)
    removed_lines: list[int] = Field(default_factory=list)
    diff_content: str = ""


class StalenessReport(BaseModel):
    """Report of documentation staleness detected from git diff analysis."""
    affected_code_tokens: list[CodeToken] = Field(default_factory=list)
    affected_markdown_blocks: list[MarkdownBlock] = Field(default_factory=list)
    stale_links: list[CodeDocLink] = Field(default_factory=list)
    git_diffs: list[GitDiffEntry] = Field(default_factory=list)
    staleness_score: float = Field(..., ge=0.0, le=1.0, description="Fraction of affected links that are stale")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DocPatch(BaseModel):
    """A structural edit patch for a documentation file."""

    file_path: str
    patch_type: str = Field(..., description="update | add | remove")
    block_heading: str
    old_content: str = ""
    new_content: str = ""
    reasoning: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)
    diff_format: str = "unified"


class ReconciliationResult(BaseModel):
    """Result of LLM reconciliation comparing old and new code against docs."""

    patches: list[DocPatch] = Field(default_factory=list)
    overall_staleness: float = Field(0.0, ge=0.0, le=1.0)
    summary: str = ""
    model_used: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
