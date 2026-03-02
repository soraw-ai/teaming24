"""
AN Router — modular routing system for the Organizer.

The Agentic Node Workforce Pool contains ALL (local team coordinator + all Remote ANs).
Before each execution, the ANRouter selects a SUBSET from the pool and
decides WHICH selected members handle WHICH subtasks.

.. note::

   **ANRouter ≠ LLMRouter**

   - ``ANRouter``  — Routes tasks to ANs from the pool (this module).
   - ``LLMRouter`` — Routes which LLM backend to use (separate concern,
     placeholder for future implementation; currently global/unified).

Architecture
------------

This module follows a **Strategy pattern** with four layers:

1. **Data models** (``RoutingSubtask``, ``RoutingPlan``, ``RoutingDecision``)
   — The stable contract.  Every AN router MUST return a ``RoutingPlan``.

2. **Abstract protocol** (``BaseANRouter``)
   — Defines the minimal interface: ``route(prompt) → RoutingPlan``.

3. **Concrete implementations**
   — ``ANRouter``         (default: uses one LLM call to decide AN allocation)
   — ``ScoringANRouter``  (deterministic capability-based scoring)
   — Future: ``AuctionANRouter``, ``MLANRouter``, …

4. **Factory** (``create_an_router()``)
   — Reads ``an_router.strategy`` from config and returns the right
   implementation.  Consumers call the factory, never a specific class.

**To replace the routing algorithm:**

1. Create a new class that inherits ``BaseANRouter``.
2. Implement ``route(prompt) → RoutingPlan``.
3. Register it via ``register_an_router("my_strategy", MyRouter)``.
4. Set ``an_router.strategy: "my_strategy"`` in ``teaming24.yaml``.
5. Done — zero changes in ``core.py`` or ``server.py``.

Configuration (``teaming24.yaml``)::

    an_router:
      strategy: "organizer_llm"      # ← swap to "scoring" etc.
      model: "flock/gpt-5.2"         # LLM used for routing decision
      min_pool_members: 2
      prefer_remote: false
"""

from __future__ import annotations

import abc
import json
import time
from dataclasses import dataclass, field
from typing import Any

from teaming24.prompting import render_prompt
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS — the stable contract between routers and consumers
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RoutingSubtask:
    """A subtask assigned to a specific pool member.

    ``order`` is only meaningful when the plan's ``execution_mode``
    is ``"sequential"`` — subtasks execute in ascending ``order``,
    with each step receiving the previous step's output as context.
    """
    description: str
    assigned_to: str          # display name (may repeat across ANs)
    is_remote: bool = False
    target_node_id: str = ""  # an_id — the unique identifier for dispatch
    reason: str = ""
    order: int = 0            # execution order (sequential mode only)


@dataclass
class RoutingPlan:
    """The result of ``BaseANRouter.route()`` — a structured routing decision.

    ``execution_mode`` controls how subtasks run:
      - ``"parallel"``   — all subtasks run concurrently (default).
      - ``"sequential"`` — subtasks execute one after another in
        ascending ``order``.  Each step receives the prior step's
        output as additional context.
    """
    subtasks: list[RoutingSubtask] = field(default_factory=list)
    local_prompt: str = ""    # enriched prompt for local CrewAI execution
    reasoning: str = ""
    execution_mode: str = "parallel"  # "parallel" | "sequential"

    @property
    def has_remote(self) -> bool:
        """True if at least one subtask targets a remote AN."""
        return any(s.is_remote for s in self.subtasks)

    @property
    def has_local(self) -> bool:
        """True if at least one subtask targets the local Coordinator."""
        return any(not s.is_remote for s in self.subtasks)

    @property
    def remote_subtasks(self) -> list[RoutingSubtask]:
        return [s for s in self.subtasks if s.is_remote]

    @property
    def local_subtasks(self) -> list[RoutingSubtask]:
        return [s for s in self.subtasks if not s.is_remote]

    @property
    def ordered_subtasks(self) -> list[RoutingSubtask]:
        """Subtasks sorted by ``order`` (for sequential mode)."""
        return sorted(self.subtasks, key=lambda s: s.order)


@dataclass
class RoutingDecision:
    """Immutable record of a routing decision.

    Every decision is logged.  Stored on the task for audit/replay.
    """
    task_id: str
    strategy: str
    pool_size: int
    local_available: bool
    remote_count: int
    selected_ids: list[str] = field(default_factory=list)
    selected_names: list[str] = field(default_factory=list)
    reasoning: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "strategy": self.strategy,
            "pool_size": self.pool_size,
            "local_available": self.local_available,
            "remote_count": self.remote_count,
            "selected_ids": self.selected_ids,
            "selected_names": self.selected_names,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════════════
# ABSTRACT PROTOCOL — the interface every AN router must implement
# ═══════════════════════════════════════════════════════════════════════════

class BaseANRouter(abc.ABC):
    """Abstract base class for all AN routing implementations.

    AN routers decide which **Agentic Nodes** from the Agentic Node Workforce Pool
    should handle which subtasks of a given task.

    **Minimal contract:**
      - ``route(prompt) → RoutingPlan``

    **Optional lifecycle hooks (override if needed):**
      - ``log_pool_snapshot()`` — snapshot and log pool state
      - ``log_decision(...)`` — record post-execution audit data
      - ``log_execution_result(...)`` — log final summary

    Consumers (``core.py``) only call these methods.  They never access
    implementation internals like LLM prompts or scoring weights.
    """

    def __init__(
        self,
        pool: Any = None,
        task_id: str = "",
        min_pool_members: int = 2,
    ):
        self._pool = pool
        self._task_id = task_id
        self._min_pool_members = min_pool_members
        self._decision: RoutingDecision | None = None
        self._pool_snapshot: list[Any] = []
        self._routing_plan: RoutingPlan | None = None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def decision(self) -> RoutingDecision | None:
        return self._decision

    @property
    def routing_plan(self) -> RoutingPlan | None:
        return self._routing_plan

    @property
    def strategy_name(self) -> str:
        """Human-readable name of this routing strategy."""
        return self.__class__.__name__

    # ── Core contract (MUST implement) ────────────────────────────────

    @abc.abstractmethod
    def route(self, prompt: str) -> RoutingPlan:
        """Given a task prompt, return a routing plan.

        This is the ONLY method that MUST be implemented by every router.
        The returned ``RoutingPlan`` tells the Organizer:
          - Which pool members (ANs) should participate
          - What subtask each should handle
          - Whether to run in parallel or sequential mode
        """
        ...

    # ── Lifecycle hooks (optional, sensible defaults) ─────────────────

    def log_pool_snapshot(self) -> list[Any]:
        """Snapshot the current pool, log every member, and return entries."""
        if not self._pool:
            logger.info(f"[ANRouter] task={self._task_id} | No pool — local-only")
            return []

        entries = self._pool.get_pool()
        self._pool_snapshot = entries

        local_entries = [e for e in entries if e.entry_type == "local"]
        remote_entries = [e for e in entries if e.entry_type == "remote"]

        logger.info(
            f"[ANRouter] task={self._task_id} | "
            f"Pool: {len(entries)} member(s) "
            f"(local={len(local_entries)}, remote={len(remote_entries)})"
        )
        for i, e in enumerate(entries, 1):
            tag = "LOCAL" if e.entry_type == "local" else "REMOTE"
            caps = ", ".join(e.capabilities) if e.capabilities else "general"
            cost_str = f", cost={e.cost}" if e.cost else ""
            desc = getattr(e, "description", "") or ""
            desc_str = f"\n  desc: {desc[:120]}..." if desc else ""
            addr = ""
            ni = getattr(e, "node_info", None)
            if ni and getattr(ni, "ip", None):
                addr = f" @ {ni.ip}:{getattr(ni, 'port', '?')}"
            logger.info(
                f"  {i}. [{tag}] an_id={e.id} name=\"{e.name}\" ({e.status}){addr}: "
                f"capabilities=[{caps}]{cost_str}{desc_str}"
            )
        return entries

    def log_decision(
        self,
        selected_ids: list[str] | None = None,
        selected_names: list[str] | None = None,
        reasoning: str = "",
    ) -> RoutingDecision:
        """Record and log the final routing decision (post-execution)."""
        entries = self._pool_snapshot or (self._pool.get_pool() if self._pool else [])
        local_entries = [e for e in entries if e.entry_type == "local"]
        remote_entries = [e for e in entries if e.entry_type == "remote"]

        decision = RoutingDecision(
            task_id=self._task_id,
            strategy=self.strategy_name,
            pool_size=len(entries),
            local_available=any(e.status == "online" for e in local_entries),
            remote_count=len(remote_entries),
            selected_ids=selected_ids or [],
            selected_names=selected_names or [],
            reasoning=reasoning,
        )
        self._decision = decision

        separator = "─" * 60
        logger.info(f"[ANRouter] {separator}")
        logger.info(f"[ANRouter] ★ ROUTING DECISION (task={self._task_id})")
        logger.info(f"[ANRouter]   strategy={decision.strategy}, pool_size={decision.pool_size}")
        for i, name in enumerate(decision.selected_names or decision.selected_ids or [], 1):
            logger.info(f"[ANRouter]   → {i}. {name}")
        if not (decision.selected_names or decision.selected_ids):
            logger.info("[ANRouter]   → (no agents captured)")
        if reasoning:
            logger.info(f"[ANRouter]   reasoning: {reasoning}")
        selected_count = len(decision.selected_names or decision.selected_ids or [])
        if selected_count < self._min_pool_members:
            logger.warning(
                f"[ANRouter]   ⚠ Only {selected_count} member(s), "
                f"min={self._min_pool_members}"
            )
        logger.info(f"[ANRouter] {separator}")
        return decision

    def log_execution_result(
        self,
        delegated_agents: list[str] | None = None,
        result_status: str = "unknown",
        duration: float = 0.0,
    ) -> None:
        """Log post-execution summary."""
        agents_list = delegated_agents or []
        agents_str = ", ".join(agents_list) if agents_list else "local only"
        logger.info(
            f"[ANRouter] ★ EXECUTION COMPLETE (task={self._task_id}): "
            f"status={result_status}, duration={duration:.1f}s, agents=[{agents_str}]"
        )

    def enrich_prompt(self, prompt: str) -> str:
        """Legacy compatibility — returns prompt as-is."""
        return prompt

    # ── Shared helpers ────────────────────────────────────────────────

    def _record_decision(self, plan: RoutingPlan) -> None:
        """Record a RoutingDecision from the plan for auditing."""
        entries = self._pool_snapshot or []
        local_entries = [e for e in entries if e.entry_type == "local"]
        remote_entries = [e for e in entries if e.entry_type == "remote"]

        self._decision = RoutingDecision(
            task_id=self._task_id,
            strategy=self.strategy_name,
            pool_size=len(entries),
            local_available=any(e.status == "online" for e in local_entries),
            remote_count=len(remote_entries),
            selected_names=[s.assigned_to for s in plan.subtasks],
            reasoning=plan.reasoning,
        )

    @staticmethod
    def _local_only_plan(prompt: str, reason: str) -> RoutingPlan:
        """Convenience: build a plan that routes everything to local team coordinator."""
        return RoutingPlan(
            subtasks=[RoutingSubtask(
                description=prompt,
                assigned_to="local team coordinator",
                is_remote=False,
                reason=reason,
            )],
            local_prompt=prompt,
            reasoning=reason,
        )

    @staticmethod
    def _deduplicate_subtasks(subtasks: list[RoutingSubtask]) -> list[RoutingSubtask]:
        """Merge subtasks that target the same pool member."""
        seen: dict[str, RoutingSubtask] = {}
        order = []
        for st in subtasks:
            if st.is_remote and st.target_node_id:
                key = f"remote:{st.target_node_id}"
            else:
                key = f"local:{st.assigned_to.lower()}"
            if key in seen:
                existing = seen[key]
                existing.description = (
                    existing.description.rstrip(". ")
                    + ". Additionally: "
                    + st.description
                )
                if st.reason and st.reason not in existing.reason:
                    existing.reason = (
                        f"{existing.reason}; {st.reason}" if existing.reason else st.reason
                    )
                logger.warning(
                    f"[ANRouter] Merged duplicate assignment for '{st.assigned_to}' "
                    f"— each pool member can only be selected once"
                )
            else:
                seen[key] = st
                order.append(key)
        return [seen[k] for k in order]


# ═══════════════════════════════════════════════════════════════════════════
# AN ROUTER — default implementation (LLM-assisted AN routing)
# ═══════════════════════════════════════════════════════════════════════════

class ANRouter(BaseANRouter):
    """Default AN Router — routes tasks to Agentic Nodes via LLM decision.

    Uses a single LLM call (via ``litellm``) to analyze the task and the
    Agentic Node Workforce Pool, then produces a ``RoutingPlan`` assigning subtasks to
    the best-matching pool members (local team coordinator + Remote ANs).

    The LLM here is used as a **decision engine for AN routing** — it is
    NOT the same as ``LLMRouter`` (which will route LLM backend selection).

    This is the default strategy (``an_router.strategy: "organizer_llm"``).
    """

    STRATEGY_ORGANIZER_LLM = "organizer_llm"

    def __init__(
        self,
        pool: Any = None,
        task_id: str = "",
        model: str = "",
        min_pool_members: int = 2,
        prefer_remote: bool = False,
        routing_temperature: float = 0.1,
        routing_max_tokens: int = 1000,
        llm_call_params: dict[str, Any] | None = None,
    ):
        super().__init__(pool=pool, task_id=task_id, min_pool_members=min_pool_members)
        self._model = model
        self._prefer_remote = prefer_remote
        self._routing_temperature = routing_temperature
        self._routing_max_tokens = routing_max_tokens
        self._llm_call_params = dict(llm_call_params or {})

    @property
    def strategy_name(self) -> str:
        return "ANRouter"

    # ── Core contract ─────────────────────────────────────────────────

    def route(self, prompt: str) -> RoutingPlan:
        entries = self._pool_snapshot or (self._pool.get_pool() if self._pool else [])
        remote_entries = [e for e in entries if e.entry_type == "remote"]

        # Fast path: no remote ANs → local only
        if not remote_entries:
            plan = self._local_only_plan(prompt, "No remote ANs in pool — local-only")
            self._routing_plan = plan
            if self._min_pool_members > 1:
                logger.warning(
                    f"[ANRouter] task={self._task_id} | "
                    f"min_pool_members={self._min_pool_members} but pool has only 1 member "
                    f"(local team coordinator). Connect remote ANs to enable multi-AN routing."
                )
            else:
                logger.info(
                    f"[ANRouter] task={self._task_id} | "
                    f"Local-only (no remote ANs) — skipping routing LLM call"
                )
            self._record_decision(plan)
            return plan

        # LLM-assisted routing decision
        plan = self._llm_route(prompt, entries, remote_entries)

        # Post-validation: enforce min_pool_members
        plan = self._enforce_min_pool_members(plan, prompt, entries)

        self._routing_plan = plan
        self._record_decision(plan)
        return plan

    def _enforce_min_pool_members(
        self,
        plan: RoutingPlan,
        prompt: str,
        entries: list[Any],
    ) -> RoutingPlan:
        """Ensure the plan meets the min_pool_members constraint.

        If the LLM returned fewer members than required and the pool is
        large enough, add the missing members.  Uses entry IDs (not names)
        to correctly handle duplicate-name entries.
        """
        min_m = min(self._min_pool_members, len(entries))

        if len(plan.subtasks) >= min_m:
            return plan

        # Collect IDs of already-selected entries
        selected_ids: set = set()
        for s in plan.subtasks:
            if s.is_remote and s.target_node_id:
                selected_ids.add(s.target_node_id)
            elif not s.is_remote:
                # Find the local entry's ID
                for e in entries:
                    if e.entry_type == "local":
                        selected_ids.add(e.id)
                        break

        # Find unselected online pool members
        unselected = [
            e for e in entries
            if e.id not in selected_ids and e.status == "online"
        ]

        needed = min_m - len(plan.subtasks)
        to_add = unselected[:needed]

        if not to_add:
            logger.warning(
                f"[ANRouter] min_pool_members={min_m} but only "
                f"{len(plan.subtasks)} member(s) in plan and no more online members available"
            )
            return plan

        new_subtasks = list(plan.subtasks)
        for entry in to_add:
            new_subtasks.append(RoutingSubtask(
                description=prompt,
                assigned_to=entry.name,
                is_remote=(entry.entry_type == "remote"),
                target_node_id=entry.id if entry.entry_type == "remote" else "",
                reason=f"Added to meet min_pool_members={min_m}",
                order=max((s.order for s in new_subtasks), default=0) + 1,
            ))
            logger.info(
                f"[ANRouter] Added '{entry.name}' (id={entry.id}) to meet "
                f"min_pool_members={min_m} (was {len(plan.subtasks)})"
            )

        # Rebuild local prompt
        local_descs = [s.description for s in new_subtasks if not s.is_remote]
        local_prompt = "\n\n".join(local_descs) if local_descs else prompt

        return RoutingPlan(
            subtasks=new_subtasks,
            local_prompt=local_prompt,
            reasoning=plan.reasoning + f" [+{len(to_add)} added for min_pool_members]",
            execution_mode=plan.execution_mode,
        )

    # ── LLM-assisted routing internals ────────────────────────────────

    @staticmethod
    def _build_unique_display_names(entries: list[Any]) -> dict[str, Any]:
        """Build a mapping of unique display names → pool entries.

        When multiple entries share the same ``name`` (e.g. three remote
        ANs all named ``"127.0.0.1:8000"``), disambiguate using the real
        network address or a numeric suffix so the LLM can distinguish
        each member and the response parser can map back to the correct
        entry.

        Returns:
            ``{display_name: entry}`` — every display name is unique.
        """
        # Count occurrences of each raw name
        name_counts: dict[str, int] = {}
        for e in entries:
            key = e.name.lower()
            name_counts[key] = name_counts.get(key, 0) + 1

        display_map: dict[str, Any] = {}
        seen_names: dict[str, int] = {}

        for e in entries:
            raw = e.name
            key = raw.lower()

            if name_counts[key] == 1:
                # Unique name — use as-is
                display = raw
            else:
                # Duplicate name — disambiguate with real address or index
                ni = getattr(e, "node_info", None)
                real_ip = getattr(ni, "ip", None) if ni else None
                real_port = getattr(ni, "port", None) if ni else None

                if real_ip and real_ip != raw.split(":")[0]:
                    display = f"{raw} ({real_ip}:{real_port})" if real_port else f"{raw} ({real_ip})"
                elif real_ip and real_port:
                    display = f"{raw} @ {real_ip}:{real_port}"
                else:
                    idx = seen_names.get(key, 0) + 1
                    seen_names[key] = idx
                    display = f"{raw} #{idx}"

            # Ensure true uniqueness (defensive)
            while display.lower() in display_map:
                display = display + "'"

            display_map[display] = e

        return display_map

    @staticmethod
    def _entry_endpoint(entry: Any) -> str:
        endpoint = str(getattr(entry, "endpoint", "") or "").strip()
        if endpoint:
            return endpoint
        node = getattr(entry, "node_info", None)
        ip = str(getattr(node, "ip", "") or "").strip() if node else ""
        port = getattr(node, "port", None) if node else None
        try:
            port_num = int(port) if port is not None else 0
        except (TypeError, ValueError):
            port_num = 0
        if ip and port_num > 0:
            return f"{ip}:{port_num}"
        return "N/A"

    @staticmethod
    def _short_wallet(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "N/A"
        if len(raw) <= 14:
            return raw
        return f"{raw[:8]}…{raw[-6:]}"

    def _llm_route(
        self,
        prompt: str,
        entries: list[Any],
        remote_entries: list[Any],
    ) -> RoutingPlan:
        # Build unique display names so the LLM can distinguish every member
        display_map = self._build_unique_display_names(entries)

        pool_lines = []
        for i, (display_name, e) in enumerate(display_map.items(), 1):
            tag = "LOCAL" if e.entry_type == "local" else "REMOTE"
            caps = e.capabilities[:12] if e.capabilities else ["general"]
            caps_str = ", ".join(caps)
            if e.capabilities and len(e.capabilities) > 12:
                caps_str += f" (+{len(e.capabilities) - 12} more)"
            cost_str = f", cost={e.cost}" if getattr(e, "cost", None) else ""
            endpoint = self._entry_endpoint(e)
            source = str(getattr(e, "source", "") or "").strip() or (
                "local" if e.entry_type == "local" else "unknown"
            )
            region = str(getattr(e, "region", "") or "").strip() or "N/A"
            wallet = self._short_wallet(getattr(e, "wallet_address", None))
            agent_id = str(getattr(e, "agent_id", "") or "").strip() or "N/A"
            desc = getattr(e, "description", "") or ""
            if desc and len(desc) > 360:
                desc = desc[:360] + "…"
            pool_lines.append(
                f"  {i}. [{tag}] an_id=\"{e.id}\", name=\"{display_name}\""
                f"{cost_str}\n"
                f"     status: {e.status}, source: {source}, endpoint: {endpoint}\n"
                f"     region: {region}, wallet: {wallet}, agent_id: {agent_id}\n"
                f"     capabilities: [{caps_str}]\n"
                f"     description: {desc or 'N/A'}"
            )

        min_m = min(self._min_pool_members, len(entries))

        system_prompt = render_prompt(
            "an_router.system.organizer_selection",
            pool_lines="\n".join(pool_lines),
            min_members=min_m,
            prefer_remote_rule=(
                "- Prefer [REMOTE] members when they have matching capabilities.\n"
                if self._prefer_remote else ""
            ),
        )

        try:
            import litellm
            logger.info(
                f"[ANRouter] task={self._task_id} | "
                f"Routing via LLM decision (model={self._model}, pool={len(entries)}, min={min_m})"
            )
            response = litellm.completion(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._routing_temperature,
                max_tokens=self._routing_max_tokens,
                **self._llm_call_params,
            )
            raw = response.choices[0].message.content.strip()
            logger.debug(f"[ANRouter] Raw routing response: {raw[:500]}")
            return self._parse_routing_response(raw, prompt, entries, display_map)

        except ImportError:
            logger.warning("[ANRouter] litellm not available — fallback to local-only")
            return self._local_only_plan(prompt, "litellm not available")
        except Exception as e:
            logger.error(f"[ANRouter] Routing LLM call failed: {e}", exc_info=True)
            return self._local_only_plan(prompt, f"Routing LLM call failed: {e}")

    def _parse_routing_response(
        self,
        raw: str,
        prompt: str,
        entries: list[Any],
        display_map: dict[str, Any] | None = None,
    ) -> RoutingPlan:
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            clean = "\n".join(lines)

        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.warning(f"[ANRouter] JSON parse failed: {e} — raw: {raw[:300]}")
            return self._local_only_plan(prompt, f"JSON parse error: {e}")

        if not isinstance(data, dict):
            logger.warning(f"[ANRouter] LLM returned non-dict JSON ({type(data).__name__}) — raw: {raw[:300]}")
            return self._local_only_plan(prompt, "LLM returned non-dict JSON")

        # ── Lookup tables ──
        # an_id is the ONLY unique identifier for ANs. Names can repeat.
        # All resolution, dispatch, and tracking MUST use an_id.
        entry_by_id: dict[str, Any] = {e.id: e for e in entries}

        # Name-based fallback (only if LLM returns a name instead of an_id)
        name_to_entries: dict[str, list[Any]] = {}
        for e in entries:
            name_to_entries.setdefault(e.name.lower(), []).append(e)
            if e.entry_type == "local":
                for alias in ("local coordinator", "coordinator",
                              "local team coordinator"):
                    name_to_entries.setdefault(alias, []).append(e)
        if display_map:
            for dname, e in display_map.items():
                name_to_entries.setdefault(dname.lower(), []).append(e)

        assigned_an_ids: set = set()

        def _resolve_by_an_id(value: str) -> Any:
            """Resolve an LLM-assigned value to a pool entry.

            an_id is the sole unique identifier. Resolution:
              1. Exact an_id match (guaranteed unique — primary path)
              2. Name-based fallback (pick first unassigned entry with that name)
            """
            key = value.strip()

            # 1. Exact an_id match — definitive
            direct = entry_by_id.get(key)
            if direct:
                return direct

            # 2. Name fallback — pick the first entry whose an_id hasn't been
            #    assigned yet. This handles the case where the LLM returns a
            #    display name instead of an_id.
            key_lower = key.lower()
            candidates = name_to_entries.get(key_lower, [])
            for c in candidates:
                if c.id not in assigned_an_ids:
                    logger.debug(
                        f"[ANRouter] Resolved name '{key}' → an_id='{c.id}'"
                    )
                    return c

            # 3. Partial name fallback
            for name, elist in name_to_entries.items():
                if key_lower in name or name in key_lower:
                    for e in elist:
                        if e.id not in assigned_an_ids:
                            logger.debug(
                                f"[ANRouter] Partial-matched '{key}' → "
                                f"an_id='{e.id}' (via '{name}')"
                            )
                            return e

            return None

        subtasks: list[RoutingSubtask] = []
        reasoning = data.get("reasoning", "")
        execution_mode = data.get("execution_mode", "parallel").lower()
        if execution_mode not in ("parallel", "sequential"):
            execution_mode = "parallel"

        llm_subtask_list = data.get("subtasks", [])
        if not isinstance(llm_subtask_list, list):
            logger.warning(
                "[ANRouter] LLM returned non-list subtasks (%s) — fallback to local-only",
                type(llm_subtask_list).__name__,
            )
            return self._local_only_plan(prompt, "LLM returned non-list subtasks")

        skipped_count = 0
        logger.info(
            f"[ANRouter] LLM returned {len(llm_subtask_list)} subtask(s), "
            f"execution_mode={execution_mode}, "
            f"pool_size={len(entries)} (an_ids: {[e.id for e in entries]})"
        )

        for idx, st in enumerate(llm_subtask_list, 1):
            if not isinstance(st, dict):
                skipped_count += 1
                logger.warning(
                    "[ANRouter] Skipping non-dict subtask item at index=%d (%s)",
                    idx,
                    type(st).__name__,
                )
                continue

            desc = (st.get("description", "") or "").strip() or prompt
            assigned = str(st.get("assigned_to", "") or "")
            reason = str(st.get("reason", "") or "")
            try:
                order = int(st.get("order", idx))
            except (TypeError, ValueError):
                logger.debug(
                    "[ANRouter] Invalid subtask order for item %d (%r), using index",
                    idx,
                    st.get("order"),
                )
                order = idx

            logger.info(
                f"[ANRouter] Subtask {idx}: assigned_to='{assigned}'"
            )
            entry = _resolve_by_an_id(assigned)

            if not entry:
                logger.warning(
                    f"[ANRouter] Could not resolve '{assigned}' to any pool "
                    f"member — assigning to local team coordinator"
                )

            an_id = entry.id if entry else None
            entry_name = entry.name if entry else None

            # an_id is unique and never conflicts.
            # If the LLM assigned the same an_id twice, skip duplicates.
            if an_id and an_id in assigned_an_ids:
                skipped_count += 1
                logger.warning(
                    f"[ANRouter] LLM assigned an_id='{an_id}' "
                    f"({entry_name}) twice — skipping duplicate subtask "
                    f"(each AN can only be selected once)"
                )
                continue

            if an_id:
                assigned_an_ids.add(an_id)

            if entry and entry.entry_type == "remote":
                subtasks.append(RoutingSubtask(
                    description=desc, assigned_to=entry.name,
                    is_remote=True, target_node_id=an_id,
                    reason=reason, order=order,
                ))
                logger.info(
                    f"[ANRouter]   → REMOTE subtask: an_id={an_id}, "
                    f"name={entry.name}"
                )
            else:
                subtasks.append(RoutingSubtask(
                    description=desc,
                    assigned_to=entry.name if entry else "local team coordinator",
                    is_remote=False, reason=reason, order=order,
                ))
                logger.info(
                    f"[ANRouter]   → LOCAL subtask: an_id={an_id}"
                )

        if not subtasks:
            subtasks = [RoutingSubtask(
                description=prompt, assigned_to="local team coordinator",
                is_remote=False, reason="No subtasks parsed from routing response",
            )]

        remote_st = [s for s in subtasks if s.is_remote]
        local_st = [s for s in subtasks if not s.is_remote]
        logger.info(
            f"[ANRouter] Parse result: {len(subtasks)} subtask(s) "
            f"({len(remote_st)} remote, {len(local_st)} local)"
            + (f", {skipped_count} skipped (LLM duplicate)" if skipped_count else "")
        )

        local_descs = [s.description for s in local_st]
        local_prompt = "\n\n".join(local_descs) if local_descs else prompt

        plan = RoutingPlan(
            subtasks=subtasks, local_prompt=local_prompt,
            reasoning=reasoning, execution_mode=execution_mode,
        )

        separator = "─" * 60
        logger.info(f"[ANRouter] {separator}")
        logger.info(f"[ANRouter] ★ ROUTING PLAN (task={self._task_id})")
        logger.info(f"[ANRouter]   execution_mode: {execution_mode}")
        logger.info(
            f"[ANRouter]   subtasks: {len(subtasks)} total, "
            f"{len(plan.remote_subtasks)} remote, {len(plan.local_subtasks)} local"
        )
        logger.info(f"[ANRouter]   reasoning: {reasoning}")
        display = plan.ordered_subtasks if execution_mode == "sequential" else subtasks
        for i, st in enumerate(display, 1):
            tag = "[REMOTE]" if st.is_remote else "[LOCAL]"
            order_str = f" (step {st.order})" if execution_mode == "sequential" else ""
            node_id_str = f" (an_id={st.target_node_id})" if st.target_node_id else ""
            logger.info(f"[ANRouter]   → {i}. {tag} {st.assigned_to}{node_id_str}{order_str}: {st.description[:80]}...")
        logger.info(f"[ANRouter] {separator}")

        return plan


# ═══════════════════════════════════════════════════════════════════════════
# SCORING AN ROUTER — deterministic capability-based scoring
# ═══════════════════════════════════════════════════════════════════════════

class ScoringANRouter(BaseANRouter):
    """Route tasks using deterministic capability-based scoring.

    No LLM call — uses the ``RoutingStrategy`` scoring function to
    rank pool members by capability overlap, then assigns the top-N.

    Useful for low-latency, deterministic routing where LLM cost or
    latency is unacceptable.

    Activated via ``an_router.strategy: "scoring"`` in config.
    """

    def __init__(
        self,
        pool: Any = None,
        task_id: str = "",
        min_pool_members: int = 2,
        required_capabilities: list[str] | None = None,
    ):
        super().__init__(pool=pool, task_id=task_id, min_pool_members=min_pool_members)
        self._required_capabilities = required_capabilities or []

    @property
    def strategy_name(self) -> str:
        return "ScoringANRouter"

    def route(self, prompt: str) -> RoutingPlan:
        entries = self._pool_snapshot or (self._pool.get_pool() if self._pool else [])
        remote_entries = [e for e in entries if e.entry_type == "remote"]

        if not remote_entries:
            plan = self._local_only_plan(prompt, "No remote ANs — local-only")
            self._routing_plan = plan
            self._record_decision(plan)
            return plan

        from teaming24.agent.routing_strategy import RoutingStrategy
        strategy = RoutingStrategy()
        ranked = strategy.rank(entries, self._required_capabilities or None)

        selected = ranked[:max(self._min_pool_members, 1)]
        if not selected:
            plan = self._local_only_plan(prompt, "No online pool members")
            self._routing_plan = plan
            self._record_decision(plan)
            return plan

        subtasks = []
        for entry in selected:
            subtasks.append(RoutingSubtask(
                description=prompt,
                assigned_to=entry.name,
                is_remote=(entry.entry_type == "remote"),
                target_node_id=entry.id if entry.entry_type == "remote" else "",
                reason=f"Capability score: top-{len(selected)} match",
            ))

        subtasks = self._deduplicate_subtasks(subtasks)
        local_descs = [s.description for s in subtasks if not s.is_remote]
        local_prompt = "\n\n".join(local_descs) if local_descs else prompt

        plan = RoutingPlan(
            subtasks=subtasks, local_prompt=local_prompt,
            reasoning=f"ScoringANRouter: selected top {len(selected)} by capability score",
            execution_mode="parallel",
        )
        self._routing_plan = plan
        self._record_decision(plan)

        logger.info(
            f"[ScoringANRouter] task={self._task_id} | "
            f"Selected {len(selected)} members: "
            f"{[s.assigned_to for s in subtasks]}"
        )
        return plan


# ═══════════════════════════════════════════════════════════════════════════
# LLM ROUTER — PLACEHOLDER (future: route which LLM backend to use)
# TODO(feature): Implement LLMRouter for per-task/agent model selection
# ═══════════════════════════════════════════════════════════════════════════

class LLMRouter:
    """Placeholder for future LLM backend routing.

    **This is NOT the AN Router.**

    ``LLMRouter`` will handle selecting which LLM backend (model/provider)
    to use for different tasks or agents.  Currently the LLM backend is
    globally configured in ``teaming24.yaml`` → ``llm.model``.

    Future features:
      - Per-agent model selection
      - Cost-aware model routing (cheap model for simple tasks)
      - Fallback chains (primary → secondary → tertiary)
      - Load balancing across providers
      - Context-length-aware routing

    .. note::

       Not yet implemented.  The global ``llm.model`` config is used
       everywhere.  This class is a placeholder for the future design.
    """

    def __init__(self) -> None:
        raise NotImplementedError(
            "LLMRouter is not yet implemented. "
            "LLM backend is currently configured globally via teaming24.yaml → llm.model. "
            "For AN routing, use ANRouter or create_an_router()."
        )


# ═══════════════════════════════════════════════════════════════════════════
# AN ROUTER REGISTRY & FACTORY
# ═══════════════════════════════════════════════════════════════════════════

# Register all built-in AN routing strategies here.
# Third-party code can call ``register_an_router()`` to add custom strategies.
AN_ROUTER_REGISTRY: dict[str, type[BaseANRouter]] = {
    "organizer_llm": ANRouter,
    "scoring": ScoringANRouter,
    "algorithmic": ScoringANRouter,  # alias
}


def register_an_router(strategy_name: str, router_class: type[BaseANRouter]) -> None:
    """Register a custom AN router implementation.

    Args:
        strategy_name: The name used in ``an_router.strategy`` config.
        router_class: A subclass of ``BaseANRouter``.
    """
    if not issubclass(router_class, BaseANRouter):
        raise TypeError(f"{router_class} must inherit from BaseANRouter")
    AN_ROUTER_REGISTRY[strategy_name] = router_class
    logger.info(f"[ANRouter] Registered strategy: {strategy_name} → {router_class.__name__}")


def create_an_router(
    pool: Any = None,
    task_id: str = "",
    strategy: str | None = None,
    **kwargs,
) -> BaseANRouter:
    """Factory: create the right AN router from config.

    Reads ``an_router.*`` from ``teaming24.yaml`` and instantiates the
    matching ``BaseANRouter`` subclass.

    Args:
        pool: AgenticNodeWorkforcePool instance.
        task_id: Current task ID for logging.
        strategy: Override strategy name (default: from config).
        **kwargs: Extra kwargs forwarded to the router constructor.

    Returns:
        A ``BaseANRouter`` instance ready to call ``.route(prompt)``.
    """
    from teaming24.config import get_config
    cfg = get_config().an_router

    strategy = strategy or cfg.strategy
    router_cls = AN_ROUTER_REGISTRY.get(strategy)
    llm_call_params = kwargs.pop("llm_call_params", None)

    if router_cls is None:
        logger.warning(
            f"[ANRouter] Unknown strategy '{strategy}' — "
            f"available: {list(AN_ROUTER_REGISTRY.keys())}. "
            f"Falling back to ANRouter (default)."
        )
        router_cls = ANRouter

    # Build kwargs from config + overrides
    init_kwargs: dict[str, Any] = {
        "pool": pool,
        "task_id": task_id,
        "min_pool_members": max(
            1,
            kwargs.pop("min_pool_members", None)
            or cfg.min_pool_members
        ),
    }

    # ANRouter-specific config (LLM decision model, etc.)
    if issubclass(router_cls, ANRouter):
        init_kwargs["model"] = kwargs.pop("model", None) or cfg.model
        init_kwargs["prefer_remote"] = kwargs.pop("prefer_remote", cfg.prefer_remote)
        init_kwargs["routing_temperature"] = kwargs.pop(
            "routing_temperature", cfg.routing_temperature
        )
        init_kwargs["routing_max_tokens"] = kwargs.pop(
            "routing_max_tokens", cfg.routing_max_tokens
        )
        if isinstance(llm_call_params, dict) and llm_call_params:
            init_kwargs["llm_call_params"] = dict(llm_call_params)

    # Forward remaining kwargs
    init_kwargs.update(kwargs)

    router = router_cls(**init_kwargs)
    logger.info(
        f"[ANRouter] Created: {router.strategy_name} "
        f"(strategy={strategy}, task={task_id})"
    )
    return router


# ═══════════════════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY aliases
# ═══════════════════════════════════════════════════════════════════════════

# Old names → new names (no consumer changes needed)
BaseRouter = BaseANRouter
"""Deprecated alias. Use ``BaseANRouter``."""

AlgorithmicRouter = ScoringANRouter
"""Deprecated alias. Use ``ScoringANRouter``."""

ROUTER_REGISTRY = AN_ROUTER_REGISTRY
"""Deprecated alias. Use ``AN_ROUTER_REGISTRY``."""

register_router = register_an_router
"""Deprecated alias. Use ``register_an_router()``."""

create_router = create_an_router
"""Deprecated alias. Use ``create_an_router()``."""
