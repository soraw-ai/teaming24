"""
Context window management for agent conversations.

Provides:
  - Token counting (via tiktoken or character-based fallback).
  - Auto-compaction: when messages approach the context limit,
    older messages are summarised into a single system message.
  - Memory flush: before compaction, durable facts are persisted
    to the memory system.

Follows a context-guard pattern to prevent overflows.
"""

from __future__ import annotations

from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# Default context limits per model family
_MODEL_LIMITS: dict[str, int] = {
    "gpt-4": 8192,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16385,
    "claude-3": 200000,
    "claude-3.5": 200000,
    "claude-4": 200000,
}

# Reserve this many tokens for the response
_RESPONSE_RESERVE = 4096


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

_tiktoken_enc = None


def _get_tiktoken():
    """Lazy-load tiktoken encoder."""
    global _tiktoken_enc
    if _tiktoken_enc is not None:
        return _tiktoken_enc
    try:
        import tiktoken
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        return _tiktoken_enc
    except ImportError:
        logger.debug("tiktoken not installed; using char-based token approximation")
        return None


def count_tokens(text: str) -> int:
    """Count tokens in text. Uses tiktoken if available, else ~4 chars/token."""
    enc = _get_tiktoken()
    if enc:
        return len(enc.encode(text))
    return len(text) // 4


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Count total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        total += 4  # per-message overhead
    return total


def get_context_limit(model: str) -> int:
    """Return the context window size for a model."""
    for prefix, limit in _MODEL_LIMITS.items():
        if prefix in model.lower():
            return limit
    return 8192  # conservative default


# ---------------------------------------------------------------------------
# Context guard
# ---------------------------------------------------------------------------

def needs_compaction(messages: list[dict[str, Any]], model: str) -> bool:
    """Check if messages are approaching the context limit."""
    limit = get_context_limit(model)
    used = count_message_tokens(messages)
    threshold = limit - _RESPONSE_RESERVE
    return used > threshold * 0.85  # compact at 85% of available space


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

async def compact_messages(
    messages: list[dict[str, Any]],
    model: str,
    memory_flush: bool = True,
) -> list[dict[str, Any]]:
    """Compact older messages into a summary, preserving recent context.

    Strategy:
      1. Keep the system message (messages[0]) intact.
      2. Keep the last N messages (recent context).
      3. Summarise everything in between into one system message.
      4. Optionally flush important facts to memory before discarding.

    Args:
        messages: The full message list.
        model: Model name (for token counting and summarisation).
        memory_flush: If True, save key facts to memory before compacting.

    Returns:
        A shorter message list that fits within the context window.
    """
    if len(messages) <= 6:
        return messages  # too few to compact

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    start = 1 if system_msg else 0
    keep_recent = 4  # keep last 4 messages
    old_messages = messages[start:-keep_recent]
    recent_messages = messages[-keep_recent:]

    if not old_messages:
        return messages

    # Build summary of old messages
    old_text = "\n".join(
        f"{m.get('role', 'user')}: {str(m.get('content', ''))[:300]}"
        for m in old_messages
    )

    summary = f"[Context compacted — {len(old_messages)} earlier messages summarised]\n"
    summary += f"Key points from earlier conversation:\n{old_text[:2000]}"

    # Optionally flush to memory
    if memory_flush:
        try:
            from teaming24.memory.manager import MemoryManager
            mm = MemoryManager()
            mm.save(
                agent_id="system",
                content=f"Compacted context:\n{old_text[:3000]}",
                tags=["context_compaction"],
                source="compaction",
            )
        except Exception as exc:
            logger.debug("[Context] memory flush failed: %s", exc)

    # Rebuild
    result = []
    if system_msg:
        result.append(system_msg)
    result.append({"role": "system", "content": summary})
    result.extend(recent_messages)

    old_tokens = count_message_tokens(messages)
    new_tokens = count_message_tokens(result)
    logger.info(
        "[Context] compacted %d → %d messages  (%d → %d tokens)",
        len(messages), len(result), old_tokens, new_tokens,
    )
    return result
