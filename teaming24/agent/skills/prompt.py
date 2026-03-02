"""
Skill prompt injection.

Formats skills into an XML-like block that is appended to the agent's
system prompt.  Each skill's full instructions are included so the LLM
can apply the relevant workflow when it matches the user's request.
"""

from __future__ import annotations

from teaming24.agent.skills.types import Skill

MAX_SKILLS_PROMPT_CHARS = 30_000


def format_skills_for_prompt(skills: list[Skill], include_instructions: bool = False) -> str:
    """Format skills for injection into an agent's system prompt.

    Args:
        skills: List of skills to format.
        include_instructions: If True, include full instructions inline.
            If False, only include metadata (name + description).

    Returns:
        Formatted string ready for system prompt injection.
    """
    if not skills:
        return ""

    lines = ["<available_skills>"]
    total_chars = 0

    for sk in skills:
        if not sk.metadata.enabled:
            continue
        entry = _format_entry(sk, include_instructions)
        if total_chars + len(entry) > MAX_SKILLS_PROMPT_CHARS:
            lines.append(f"  <!-- {len(skills) - len(lines) + 1} more skills omitted (prompt size limit) -->")
            break
        lines.append(entry)
        total_chars += len(entry)

    lines.append("</available_skills>")
    return "\n".join(lines)


def _format_entry(skill: Skill, include_instructions: bool) -> str:
    parts = [
        f'  <skill name="{skill.name}">',
        f"    <description>{skill.description}</description>",
    ]
    if skill.metadata.category != "general":
        parts.append(f"    <category>{skill.metadata.category}</category>")
    if skill.metadata.tags:
        parts.append(f"    <tags>{', '.join(skill.metadata.tags)}</tags>")
    if skill.metadata.requires.tools:
        parts.append(f"    <requires_tools>{', '.join(skill.metadata.requires.tools)}</requires_tools>")
    if include_instructions and skill.instructions:
        parts.append(f"    <instructions>\n{skill.instructions}\n    </instructions>")
    parts.append("  </skill>")
    return "\n".join(parts)


def build_skill_system_prompt_section(skills: list[Skill]) -> str:
    """Build the full skill section for a system prompt.

    This includes the preamble with selection instructions plus the
    compact skill listing.
    """
    if not skills:
        return ""

    listing = format_skills_for_prompt(skills, include_instructions=True)
    return f"""## Skills

You have access to the following skills.  Before responding, scan the
skill descriptions.  If a skill clearly applies to the user's request,
follow its instructions.  If multiple skills could apply, choose the
most specific one.  If none apply, proceed without skills.

{listing}

When a skill is selected, incorporate its guidance into your response
while still using the tools available to you."""
