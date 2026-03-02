"""
Tool Policy System for Teaming24.

Provides profile-based and group-based tool filtering. Agents can be
assigned a tool_profile and optionally
overlay allow/deny/also_allow lists that reference groups or individual
tool IDs.

Layered Resolution
-----------------
Tools are resolved in this order (each layer modifies the previous):

  1. **Profile** — Base set from ``tool_profile`` (minimal, coding, research,
     networking, full). If ``allow`` is explicitly provided, it overrides
     the profile's allow set entirely.
  2. **allow** — Explicit allow list. When present, replaces the profile's
     base set instead of adding to it.
  3. **also_allow** — Union with base. Adds tools on top of profile/allow.
  4. **deny** — Subtract from result. Removes tools from the final set.

Formula: ``result = (profile OR allow) + also_allow - deny``

Available Profiles
-----------------
- **minimal** — No tools (empty set). For managers/organizers.
- **coding** — Sandbox + memory (shell, python, file I/O, memory).
- **research** — Sandbox + browser + memory (adds web scraping).
- **networking** — Sandbox + network + memory (adds delegate/search).
- **full** — All tools (default when no profile specified).

Available Groups
---------------
- **group:sandbox** — shell_command, python_interpreter, file_read,
  file_write, browser (all run in isolated Docker container)
- **group:browser** — browser
- **group:network** — delegate_to_network, search_network
- **group:memory** — memory_search, memory_save

Registering a New Tool
----------------------
1. Add the tool to ``TOOL_SECTIONS`` in the appropriate section (or create
   a new section). Each tool needs ``id``, ``label``, and ``description``.
2. Add the tool ID to the relevant ``TOOL_GROUPS`` entries (e.g. add to
   ``group:sandbox`` if it's a sandbox tool).
3. If creating a new group, add it to ``TOOL_GROUPS`` and reference it in
   ``TOOL_PROFILES`` as needed.

Creating a Custom Profile
-------------------------
Add an entry to ``TOOL_PROFILES``::

    "my_profile": {"allow": ["group:sandbox", "group:memory"]}

Use ``allow: []`` for empty, ``allow: None`` or omit for all tools.

YAML Config Examples
--------------------
Manager with no tools::

    agents:
      organizer:
        tools: []

Full access (default)::

    agents:
      coordinator:
        tool_profile: "full"

Researcher with network disabled::

    agents:
      workers:
        - id: researcher
          tool_profile: "research"
          tools:
            deny: ["group:network"]

Explicit allow list (overrides profile)::

    agents:
      workers:
        - id: specialist
          tools:
            allow: ["file_read", "file_write", "memory_search"]

Add extra tools on top of profile::

    agents:
      workers:
        - id: coder
          tool_profile: "coding"
          tools:
            also_allow: ["browser"]

Python API Examples
-------------------
Resolve by profile only::

    >>> resolve_agent_tools({"tool_profile": "research"})
    ['browser', 'file_read', 'file_write', 'memory_save', ...]

Resolve with deny overlay::

    >>> resolve_agent_tools({
    ...     "tool_profile": "research",
    ...     "tools": {"deny": ["group:network"]}
    ... })

Explicit allow (ignores profile)::

    >>> resolve_tool_policy(allow=["file_read", "memory_search"])
    ['file_read', 'memory_search']

Plug-and-Play Extension
-----------------------
- **New tool**: Add to ``TOOL_SECTIONS`` + ``TOOL_GROUPS``.
- **New group**: Add to ``TOOL_GROUPS``, use in profiles or YAML.
- **New profile**: Add to ``TOOL_PROFILES``, reference in YAML.
- No changes to resolution logic required.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Tool catalogue: sections (for UI) and flat ID list
# ---------------------------------------------------------------------------

# Canonical tool registry. Each section has id, label, and a list of tools.
# Tools have id, label, description. Used for UI display and as the source
# of truth for ALL_TOOL_IDS. To add a new tool: append to the appropriate
# section and add the tool ID to TOOL_GROUPS as needed.
TOOL_SECTIONS = [
    {
        "id": "sandbox",
        "label": "Sandbox",
        "tools": [
            {"id": "shell_command", "label": "shell_command", "description": "Execute shell commands in isolated Docker container (preferred for all execution)"},
            {"id": "python_interpreter", "label": "python_interpreter", "description": "Execute Python code in isolated Docker container (preferred for code execution)"},
            {"id": "file_read", "label": "file_read", "description": "Read files from isolated workspace (Docker container)"},
            {"id": "file_write", "label": "file_write", "description": "Write files to isolated workspace (Docker container)"},
            {"id": "browser", "label": "browser", "description": "Browser automation for web interaction and scraping"},
        ],
    },
    {
        "id": "network",
        "label": "Network",
        "tools": [
            {"id": "delegate_to_network", "label": "delegate_to_network", "description": "Delegate tasks to remote Agentic Nodes on the network"},
            {"id": "search_network", "label": "search_network", "description": "Search for remote Agentic Nodes by capability"},
        ],
    },
    {
        "id": "memory",
        "label": "Memory",
        "tools": [
            {"id": "memory_search", "label": "memory_search", "description": "Search agent memory for relevant past information"},
            {"id": "memory_save", "label": "memory_save", "description": "Save information to agent memory for future reference"},
        ],
    },
]

ALL_TOOL_IDS: list[str] = [t["id"] for section in TOOL_SECTIONS for t in section["tools"]]

# ---------------------------------------------------------------------------
# Group definitions — each group expands to a set of tool IDs
# ---------------------------------------------------------------------------

# Maps group IDs (e.g. "group:sandbox") to lists of tool IDs. Used by
# expand_groups() when resolving allow/deny/also_allow. Referenced in YAML
# as "group:sandbox", "group:network", etc. To add a group: add a new key
# and list of tool IDs; then reference it in TOOL_PROFILES or agent config.
TOOL_GROUPS: dict[str, list[str]] = {
    "group:sandbox": ["shell_command", "python_interpreter", "file_read", "file_write", "browser"],
    "group:browser": ["browser"],
    "group:network": ["delegate_to_network", "search_network"],
    "group:memory": ["memory_search", "memory_save"],
}

# ---------------------------------------------------------------------------
# Profiles — predefined tool sets (allow=None means ALL tools)
# ---------------------------------------------------------------------------

# Predefined tool sets. Each profile has an "allow" key: list of groups/IDs,
# or None/omit for all tools. allow=[] means empty (minimal). Use cases:
# - minimal: managers, no execution; coding: dev/sandbox; research: +browser;
# - networking: +network; full: all tools (default).
TOOL_PROFILES: dict[str, dict] = {
    "minimal": {"allow": []},
    "coding": {"allow": ["group:sandbox", "group:memory"]},
    "research": {"allow": ["group:sandbox", "group:browser", "group:memory"]},
    "networking": {"allow": ["group:sandbox", "group:network", "group:memory"]},
    "full": {},  # allow=None → all tools
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def expand_groups(entries: list[str]) -> set[str]:
    """Expand group references into individual tool IDs.

    For each entry in ``entries``:
    - If it starts with ``group:`` and exists in TOOL_GROUPS, replace it with
      all tool IDs in that group.
    - Otherwise, treat it as a literal tool ID and add it to the result.

    Example:
        >>> expand_groups(["group:sandbox", "memory_search"])
        {'shell_command', 'python_interpreter', 'file_read', 'file_write',
         'browser', 'memory_search'}
    """
    result: set[str] = set()
    for entry in entries:
        if entry.startswith("group:") and entry in TOOL_GROUPS:
            result.update(TOOL_GROUPS[entry])
        else:
            result.add(entry)
    return result


def resolve_tool_policy(
    profile: str = "full",
    allow: list[str] | None = None,
    also_allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> list[str]:
    """Resolve a tool policy to a sorted list of enabled tool IDs.

    Full resolution chain:

    1. **Base set**: If ``allow`` is provided, use expand_groups(allow).
       Otherwise use the profile's allow set from TOOL_PROFILES. For
       ``full`` or profiles with allow=None, base = ALL_TOOL_IDS.
    2. **Union**: base |= expand_groups(also_allow) if also_allow given.
    3. **Subtract**: base -= expand_groups(deny) if deny given.
    4. Return sorted(base).

    All list params accept ``group:`` refs (e.g. ``group:sandbox``) which
    are expanded via expand_groups().

    Example (research profile + deny network):
        resolve_tool_policy(profile="research", deny=["group:network"])
        -> sandbox + browser + memory, minus network tools
    """
    if allow:
        base = expand_groups(allow)
    else:
        profile_def = TOOL_PROFILES.get(profile, TOOL_PROFILES["full"])
        allow_list = profile_def.get("allow")
        base = set(ALL_TOOL_IDS) if allow_list is None else expand_groups(allow_list)
    if also_allow:
        base |= expand_groups(also_allow)
    if deny:
        base -= expand_groups(deny)
    return sorted(base)


def resolve_agent_tools(agent_config: dict) -> list[str]:
    """Resolve tool list for an agent config dict.

    Parses agent config and delegates to resolve_tool_policy():

    - **tool_profile** (str): Profile name, default "full".
    - **tools** (optional):
      - ``None`` or absent: use profile only.
      - List: legacy format, treated as explicit allow (profile ignored).
      - Dict: allow, also_allow, deny keys passed to resolve_tool_policy;
        profile used as base when allow not specified.

    Returns a flat sorted list of enabled tool IDs.
    """
    profile = agent_config.get("tool_profile", "full")
    tools_spec = agent_config.get("tools")

    if tools_spec is None:
        return resolve_tool_policy(profile=profile)

    # Legacy: tools is a flat list of tool IDs
    if isinstance(tools_spec, list):
        if not tools_spec:
            return []
        return resolve_tool_policy(allow=tools_spec)

    # New format: tools is a dict with allow/deny/also_allow
    if isinstance(tools_spec, dict):
        return resolve_tool_policy(
            profile=profile,
            allow=tools_spec.get("allow"),
            also_allow=tools_spec.get("also_allow"),
            deny=tools_spec.get("deny"),
        )

    return resolve_tool_policy(profile=profile)
