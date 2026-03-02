"""
Core single-agent execution loop.

    prompt  →  system message (role/goal/backstory)
            →  LLM call (with tool schemas)
            →  tool invocations (if any)
            →  repeat until final text answer or max iterations
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

try:
    import litellm
except ImportError:  # pragma: no cover - depends on optional dependency
    import logging as _logging

    _logging.getLogger(__name__).debug("litellm is not installed for native runtime")
    litellm = None

from teaming24.agent.framework.base import AgentSpec, StepCallback, StepOutput, ToolSpec
from teaming24.agent.tools.base import execute_tool
from teaming24.config import get_config
from teaming24.llm.model_resolver import resolve_model_and_call_params
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)
if litellm is None:
    logger.debug("litellm is not installed; native runtime will fail fast on execution")


def _require_litellm() -> None:
    if litellm is None:
        raise RuntimeError(
            "Native runtime requires 'litellm'. Install with: uv pip install litellm"
        )


def _build_system_message(agent: AgentSpec) -> str:
    """Construct the system prompt from an AgentSpec."""
    parts = [f"You are a {agent.role}."]
    if agent.goal:
        parts.append(f"Goal: {agent.goal}")
    if agent.backstory:
        parts.append(f"Background: {agent.backstory}")
    if agent.system_prompt:
        parts.append(agent.system_prompt)
    return "\n\n".join(parts)


class AgentRuntime:
    """Single-agent agentic loop using litellm.

    The loop:
      1. Send messages (including tool results) to the LLM.
      2. If the LLM returns tool_calls → execute tools, append results, loop.
      3. If the LLM returns a text response → done.
      4. Safety cap at ``max_iterations`` to avoid infinite loops.
    """

    def __init__(self, max_iterations: int = 25):
        self.max_iterations = max_iterations

    async def run(
        self,
        agent: AgentSpec,
        prompt: str,
        step_callback: StepCallback | None = None,
        extra_context: str = "",
    ) -> str:
        """Execute the agent loop and return the final text answer.

        Args:
            agent: The agent specification.
            prompt: User task / instruction.
            step_callback: Optional callback fired on every tool call.
            extra_context: Additional context prepended to the user message.
        """
        tool_map: dict[str, ToolSpec] = {t.name: t for t in agent.tools}
        tool_schemas = [t.to_openai_schema() for t in agent.tools] or None

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _build_system_message(agent)},
        ]
        user_content = f"{extra_context}\n\n{prompt}" if extra_context else prompt
        messages.append({"role": "user", "content": user_content})

        try:
            resolved_model, resolved_params, _provider = resolve_model_and_call_params(
                agent.model,
                get_config().llm,
            )
        except Exception as exc:
            logger.warning(
                "[NativeRuntime] Failed to resolve model/provider for %s: %s",
                agent.role,
                exc,
                exc_info=True,
            )
            resolved_model, resolved_params = agent.model, {}
        metadata_params = {}
        if isinstance(agent.metadata, dict):
            raw_params = agent.metadata.get("llm_call_params")
            if isinstance(raw_params, dict):
                metadata_params = raw_params
        call_params = {**resolved_params, **metadata_params}

        for _iteration in range(self.max_iterations):
            # Context window guard — compact if approaching limit
            try:
                from teaming24.agent.context import compact_messages, needs_compaction
                if needs_compaction(messages, resolved_model):
                    messages = await compact_messages(messages, resolved_model)
            except ImportError:
                logger.debug("Context compaction module unavailable; continuing without compaction")
                pass

            try:
                _require_litellm()
                response = await litellm.acompletion(
                    model=resolved_model,
                    messages=messages,
                    tools=tool_schemas,
                    temperature=0.7,
                    **call_params,
                )
            except Exception as exc:
                logger.error("[NativeRuntime] LLM call failed: %s", exc, exc_info=True)
                return f"[error] LLM call failed: {exc}"

            choice = response.choices[0]
            msg = choice.message

            # --- tool calls ---------------------------------------------------
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                messages.append(msg.model_dump(exclude_none=True))
                for tc in tool_calls:
                    fn_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning(
                            "[NativeRuntime] Invalid tool arguments for %s: %s; using empty args",
                            fn_name,
                            exc,
                            exc_info=True,
                        )
                        args = {}

                    spec = tool_map.get(fn_name)
                    if spec is None:
                        result = f"[error] unknown tool: {fn_name}"
                    else:
                        heartbeat_task: asyncio.Task | None = None
                        heartbeat_stop = asyncio.Event()
                        tool_args_json = json.dumps(args, ensure_ascii=False)
                        if step_callback:
                            step_callback(StepOutput(
                                agent=agent.role,
                                action="tool_start",
                                tool=fn_name,
                                tool_input=tool_args_json,
                                content=f"Running {fn_name}...",
                                metadata={"tool_status": "running"},
                            ))

                            async def _tool_heartbeat() -> None:
                                start_ts = asyncio.get_running_loop().time()
                                while not heartbeat_stop.is_set():
                                    await asyncio.sleep(15)
                                    if heartbeat_stop.is_set():
                                        break
                                    elapsed = int(asyncio.get_running_loop().time() - start_ts)
                                    step_callback(StepOutput(
                                        agent=agent.role,
                                        action="tool_heartbeat",
                                        tool=fn_name,
                                        tool_input=tool_args_json,
                                        content=f"{fn_name} still running ({elapsed}s elapsed)",
                                        metadata={
                                            "tool_status": "running",
                                            "elapsed_seconds": elapsed,
                                        },
                                    ))

                            heartbeat_task = asyncio.create_task(_tool_heartbeat())

                        result = await execute_tool(spec, args)
                        if spec is not None:
                            heartbeat_stop.set()
                            if heartbeat_task is not None:
                                heartbeat_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await heartbeat_task

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                    if step_callback:
                        step_callback(StepOutput(
                            agent=agent.role,
                            action="tool_call",
                            tool=fn_name,
                            tool_input=json.dumps(args, ensure_ascii=False),
                            content=result[:2000],
                            metadata={"tool_status": "completed"},
                        ))
                continue

            # --- final text answer --------------------------------------------
            content = getattr(msg, "content", "") or ""
            if step_callback:
                step_callback(StepOutput(
                    agent=agent.role,
                    action="final_answer",
                    content=content[:3000],
                ))
            return content

        # max iterations reached
        last = messages[-1].get("content", "") if messages else ""
        logger.warning("[NativeRuntime] max iterations (%d) reached", self.max_iterations)
        return str(last)
