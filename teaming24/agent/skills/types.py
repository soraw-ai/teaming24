"""
Skill type definitions.

A Skill is a folder containing a SKILL.md file with YAML frontmatter
and Markdown instructions.  Compatible with the Agent Skills open
standard (https://agentskills.io/specification).

**Spec-required frontmatter:**

    name        — Lowercase, hyphens, 1-64 chars.  Must match directory name.
    description — What the skill does and when to use it.  1-1024 chars.

**Spec-optional frontmatter:**

    license       — License name or reference.
    compatibility — Environment requirements (max 500 chars).
    metadata      — Arbitrary key-value pairs (author, version, etc.).
    allowed-tools — Space-delimited pre-approved tool names.

Teaming24 also supports these custom extensions in frontmatter:

    category, tags, requires (tools/env/bins), enabled, always
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

_VALID_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def validate_skill_name(name: str) -> str | None:
    """Validate a skill name against the Agent Skills spec.

    Returns None if valid, or an error message string.
    """
    if not name:
        return "name is required"
    if len(name) > 64:
        return f"name must be <= 64 chars, got {len(name)}"
    if "--" in name:
        return "name must not contain consecutive hyphens"
    if not _VALID_NAME_RE.match(name):
        return "name must be lowercase alphanumeric and hyphens, not start/end with hyphen"
    return None


@dataclass
class SkillRequirements:
    """What a skill needs to function."""
    tools: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    bins: list[str] = field(default_factory=list)


@dataclass
class SkillMetadata:
    """Structured metadata from SKILL.md frontmatter."""
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    author: str = ""
    version: str = "1.0.0"
    requires: SkillRequirements = field(default_factory=SkillRequirements)
    os: list[str] = field(default_factory=list)
    enabled: bool = True
    always: bool = False
    license: str = ""
    compatibility: str = ""


@dataclass
class Skill:
    """A skill that can be assigned to agents.

    Compatible with the Agent Skills open standard
    (https://agentskills.io/specification).

    Skills differ from tools:
      - Tools are executable functions (shell_exec, file_read, etc.)
      - Skills are knowledge documents with instructions and workflows

    A Skill's ``instructions`` (the SKILL.md body) is injected into
    the agent's system prompt when the skill is assigned to the agent.
    """
    id: str
    name: str
    description: str
    instructions: str = ""
    metadata: SkillMetadata = field(default_factory=SkillMetadata)
    source: str = ""
    file_path: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "category": self.metadata.category,
            "tags": self.metadata.tags,
            "author": self.metadata.author,
            "version": self.metadata.version,
            "license": self.metadata.license,
            "compatibility": self.metadata.compatibility,
            "requires": {
                "tools": self.metadata.requires.tools,
                "env": self.metadata.requires.env,
                "bins": self.metadata.requires.bins,
            },
            "enabled": self.metadata.enabled,
            "source": self.source,
            "file_path": self.file_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Skill:
        req_data = data.get("requires", {})
        if isinstance(req_data, dict):
            reqs = SkillRequirements(
                tools=req_data.get("tools", []),
                env=req_data.get("env", []),
                bins=req_data.get("bins", []),
            )
        else:
            reqs = SkillRequirements()

        meta = SkillMetadata(
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            author=data.get("author", ""),
            version=data.get("version", "1.0.0"),
            requires=reqs,
            enabled=data.get("enabled", True),
            license=data.get("license", ""),
            compatibility=data.get("compatibility", ""),
        )
        return Skill(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            instructions=data.get("instructions", ""),
            metadata=meta,
            source=data.get("source", ""),
            file_path=data.get("file_path", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
