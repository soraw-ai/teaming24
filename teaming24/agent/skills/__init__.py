"""
Skill system for Teaming24 agents.

Compatible with the Agent Skills open standard
(https://agentskills.io/specification).

Skills are folders containing a SKILL.md file with YAML frontmatter and
Markdown instructions.  They are loaded from multiple directories in
precedence order and injected into agent system prompts.

Skills are distinct from tools:
  - Tools are executable functions (shell_exec, file_read, etc.)
  - Skills are knowledge documents with instructions and workflows
"""

from teaming24.agent.skills.loader import discover_skills, load_skills_from_dir
from teaming24.agent.skills.prompt import format_skills_for_prompt
from teaming24.agent.skills.registry import SkillRegistry, get_skill_registry
from teaming24.agent.skills.types import (
    Skill,
    SkillMetadata,
    SkillRequirements,
    validate_skill_name,
)

__all__ = [
    "Skill",
    "SkillMetadata",
    "SkillRequirements",
    "SkillRegistry",
    "get_skill_registry",
    "load_skills_from_dir",
    "discover_skills",
    "format_skills_for_prompt",
    "validate_skill_name",
]
