"""
Audit logging helper for AgentaNet Central Service.

Records security-sensitive operations to the ``audit_logs`` table
for accountability and forensics.
"""

import json
import time
from typing import Any, Optional

from sqlalchemy.orm import Session

from .database import AuditLog


def log_audit(
    db: Session,
    *,
    action: str,
    user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Create an audit log entry and flush it to the database.

    Args:
        db: Active SQLAlchemy session.
        action: Short verb describing the operation, e.g. ``token_created``,
                ``node_registered``, ``user_deleted``.
        user_id: ID of the acting user (may be ``None`` for anonymous).
        target_type: Category of the affected resource (``user``, ``token``,
                     ``node``, ``setting``, ``doc``).
        target_id: Identifier of the affected resource.
        ip_address: Client IP address.
        details: Arbitrary JSON-serialisable context dict.

    Returns:
        The persisted :class:`AuditLog` row.
    """
    entry = AuditLog(
        timestamp=time.time(),
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        details=json.dumps(details) if details else None,
    )
    db.add(entry)
    # Flush so the caller can read entry.id, but let the endpoint commit.
    db.flush()
    return entry
