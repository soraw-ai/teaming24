"""Teaming24 formatting utilities."""

from datetime import UTC, datetime


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp to human-readable string (YYYY-MM-DD HH:MM)."""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M")
