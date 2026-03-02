"""
Skill file loader.

Discovers and loads skills from SKILL.md files in multiple directories,
following a precedence order for skill discovery.

Directories (lowest to highest precedence):
  1. Bundled skills:  teaming24/agent/skills/bundled/
  2. Managed skills:  ~/.teaming24/skills/
  3. Workspace skills: <workspace>/skills/

Compatible with the Agent Skills open standard
(https://agentskills.io/specification).

Spec-compliant SKILL.md format::

    ---
    name: my-skill
    description: "What it does and when to use it."
    license: Apache-2.0
    metadata:
      author: "Author Name"
      version: "1.0.0"
    allowed-tools: shell_exec file_read
    ---

    # Instructions
    Detailed procedural knowledge here...

Teaming24 also supports extended frontmatter fields:
``category``, ``tags``, ``requires`` (tools/env/bins),
``enabled``, ``always``.
"""

from __future__ import annotations

import re
from pathlib import Path

from teaming24.agent.skills.types import Skill, SkillMetadata, SkillRequirements
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_SKILLS_PER_DIR = 200

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_yaml_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from a SKILL.md file.

    Uses a lightweight approach to avoid requiring PyYAML as a hard
    dependency.  Falls back to ``yaml.safe_load`` if available.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    raw = match.group(1)
    try:
        import yaml
        return yaml.safe_load(raw) or {}
    except ImportError as e:
        import logging
        logging.getLogger(__name__).debug("PyYAML not available, using simple parser: %s", e)
        return _parse_yaml_simple(raw)


def _parse_yaml_simple(raw: str) -> dict:
    """Minimal YAML-like key:value parser for frontmatter."""
    result: dict = {}
    current_key = ""
    current_list: list | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if stripped.startswith("- ") and current_list is not None:
            current_list.append(stripped[2:].strip().strip("'\""))
            continue

        if indent >= 2 and current_key and isinstance(result.get(current_key), dict) and ":" in stripped:
            sub_key, _, sub_val = stripped.partition(":")
            sub_val = sub_val.strip()
            if sub_val.startswith("[") and sub_val.endswith("]"):
                items = [v.strip().strip("'\"") for v in sub_val[1:-1].split(",") if v.strip()]
                result[current_key][sub_key.strip()] = items
            else:
                result[current_key][sub_key.strip()] = sub_val.strip("'\"")
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if val.startswith("[") and val.endswith("]"):
                items = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
                result[key] = items
                current_key = key
                current_list = None
            elif val == "" or val == "|":
                result[key] = {}
                current_key = key
                current_list = None
            elif val.lower() in ("true", "false"):
                result[key] = val.lower() == "true"
                current_key = key
                current_list = None
            else:
                result[key] = val.strip("'\"")
                current_key = key
                current_list = None

    return result


def _extract_body(text: str) -> str:
    """Extract the markdown body after the frontmatter block."""
    match = _FRONTMATTER_RE.match(text)
    if match:
        return text[match.end():].strip()
    return text.strip()


def load_skill_from_file(path: Path, source: str = "") -> Skill | None:
    """Load a single Skill from a SKILL.md file.

    Supports both the official Agent Skills spec format (``metadata``
    dict, ``allowed-tools``) and teaming24 extensions (``category``,
    ``tags``, ``requires``, ``author``, ``version``).
    """
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_SKILL_FILE_BYTES:
        logger.warning("[skills] Skipping %s — exceeds %dKB limit", path, MAX_SKILL_FILE_BYTES // 1024)
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[skills] Failed to read %s: %s", path, exc)
        return None

    fm = _parse_yaml_frontmatter(text)
    body = _extract_body(text)

    name = fm.get("name", path.parent.name)
    description = fm.get("description", "")
    skill_id = fm.get("id", name.lower().replace(" ", "-").replace("/", "-"))

    # Spec ``metadata`` dict — extract author/version/category/tags if present
    spec_meta = fm.get("metadata", {}) or {}
    if not isinstance(spec_meta, dict):
        spec_meta = {}

    # ``requires`` (teaming24 extension) and ``allowed-tools`` (spec)
    req_data = fm.get("requires", {})
    if isinstance(req_data, dict):
        reqs = SkillRequirements(
            tools=req_data.get("tools", []) or [],
            env=req_data.get("env", []) or [],
            bins=req_data.get("bins", []) or [],
        )
    else:
        reqs = SkillRequirements()

    allowed_tools_raw = fm.get("allowed-tools", "")
    if isinstance(allowed_tools_raw, str) and allowed_tools_raw.strip():
        at_list = allowed_tools_raw.strip().split()
        if at_list and not reqs.tools:
            reqs.tools = at_list

    tags_raw = fm.get("tags", spec_meta.get("tags", []))
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = tags_raw
    else:
        tags = [str(tags_raw)]

    meta = SkillMetadata(
        category=fm.get("category", spec_meta.get("category", "general")),
        tags=tags,
        author=fm.get("author", spec_meta.get("author", "")),
        version=fm.get("version", spec_meta.get("version", "1.0.0")),
        requires=reqs,
        os=fm.get("os", []) or [],
        enabled=fm.get("enabled", True),
        always=fm.get("always", False),
        license=fm.get("license", ""),
        compatibility=fm.get("compatibility", ""),
    )

    mtime = path.stat().st_mtime
    return Skill(
        id=skill_id,
        name=name,
        description=description,
        instructions=body,
        metadata=meta,
        source=source,
        file_path=str(path),
        created_at=mtime,
        updated_at=mtime,
    )


def load_skills_from_dir(directory: Path, source: str = "") -> list[Skill]:
    """Load all SKILL.md files from a directory (one level deep).

    Expected structure::

        directory/
          skill-a/
            SKILL.md
          skill-b/
            SKILL.md
          standalone-skill.md   <-- also supported
    """
    if not directory.is_dir():
        return []

    skills: list[Skill] = []
    count = 0

    for child in sorted(directory.iterdir()):
        if count >= MAX_SKILLS_PER_DIR:
            logger.warning("[skills] Hit max skills limit (%d) in %s", MAX_SKILLS_PER_DIR, directory)
            break

        skill_file: Path | None = None
        if child.is_dir():
            candidate = child / "SKILL.md"
            if candidate.is_file():
                skill_file = candidate
        elif child.suffix == ".md" and child.stem.upper() == "SKILL":
            skill_file = child

        if skill_file:
            skill = load_skill_from_file(skill_file, source=source or str(directory))
            if skill:
                skills.append(skill)
                count += 1

    logger.debug("[skills] Loaded %d skills from %s", len(skills), directory)
    return skills


def discover_skills(workspace: str | None = None) -> list[Skill]:
    """Discover skills from all standard locations.

    Precedence (later entries override earlier ones with same ID):
      1. Bundled:   teaming24/agent/skills/bundled/
      2. Managed:   ~/.teaming24/skills/
      3. Workspace: <workspace>/skills/
    """
    skills_map: dict[str, Skill] = {}

    bundled = Path(__file__).parent / "bundled"
    managed = Path.home() / ".teaming24" / "skills"

    dirs = [
        (bundled, "bundled"),
        (managed, "managed"),
    ]
    if workspace:
        dirs.append((Path(workspace) / "skills", "workspace"))

    for d, src in dirs:
        for sk in load_skills_from_dir(d, source=src):
            skills_map[sk.id] = sk

    return list(skills_map.values())
