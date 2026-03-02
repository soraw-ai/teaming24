"""
Skill registry — singleton that manages all available skills.

Skills are loaded from:
  1. File-system directories (bundled, managed, workspace)
  2. Database (user-created skills via the UI)

The registry merges both sources, with DB skills overriding file-based
ones when IDs collide.
"""

from __future__ import annotations

import threading

from teaming24.agent.skills.loader import discover_skills
from teaming24.agent.skills.types import Skill
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

MAX_SKILLS_IN_PROMPT = 150


class SkillRegistry:
    """Central registry for all skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    def load(self, workspace: str | None = None) -> None:
        """Load skills from all file-system sources."""
        file_skills = discover_skills(workspace)
        for sk in file_skills:
            self._skills[sk.id] = sk
        self._loaded = True
        logger.info("[SkillRegistry] Loaded %d skills from filesystem", len(file_skills))

    def merge_db_skills(self, db_skills: list[Skill]) -> None:
        """Merge database-persisted skills (higher precedence)."""
        for sk in db_skills:
            self._skills[sk.id] = sk
        logger.debug("[SkillRegistry] Merged %d DB skills", len(db_skills))

    def register(self, skill: Skill) -> None:
        self._skills[skill.id] = skill

    def unregister(self, skill_id: str) -> bool:
        return self._skills.pop(skill_id, None) is not None

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def list_enabled(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.metadata.enabled]

    def list_by_category(self, category: str) -> list[Skill]:
        return [s for s in self._skills.values() if s.metadata.category == category]

    def get_for_agent(self, skill_ids: list[str]) -> list[Skill]:
        """Return skills matching the given IDs, ordered and filtered."""
        result = []
        for sid in skill_ids:
            sk = self._skills.get(sid)
            if sk and sk.metadata.enabled:
                result.append(sk)
        always = [s for s in self._skills.values() if s.metadata.always and s.id not in skill_ids]
        return (result + always)[:MAX_SKILLS_IN_PROMPT]

    def categories(self) -> list[str]:
        cats = sorted({s.metadata.category for s in self._skills.values()})
        return cats

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self._skills


_registry: SkillRegistry | None = None
_lock = threading.Lock()


def get_skill_registry() -> SkillRegistry:
    """Return the global SkillRegistry singleton."""
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = SkillRegistry()
    return _registry
