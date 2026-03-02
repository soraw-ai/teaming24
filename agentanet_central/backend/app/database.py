"""
Database models and connection for AgentaNet Central Service.

Uses SQLite with SQLAlchemy ORM.
"""

import time

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .config import get_config
from .id_utils import new_id

Base = declarative_base()

# Lazy initialization
_engine = None
_SessionLocal = None


def _get_engine():
    """Get or create database engine."""
    global _engine
    if _engine is None:
        config = get_config()
        db_path = config.database.get_absolute_path(config._config_path.parent)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    return _engine


def _get_session_local():
    """Get or create session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_get_engine())
    return _SessionLocal


def get_db():
    """Database session dependency."""
    SessionLocal = _get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    """User account (linked to GitHub)."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=new_id)
    github_id = Column(String(64), unique=True, nullable=False, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), nullable=True)
    avatar_url = Column(String(256), nullable=True)
    is_admin = Column(Boolean, default=False)  # Super admin flag
    is_suspended = Column(Boolean, default=False)
    suspended_reason = Column(String(256), nullable=True)
    created_at = Column(Float, default=time.time)
    last_login_at = Column(Float, nullable=True)

    # Relationships
    tokens = relationship("Token", back_populates="user", cascade="all, delete-orphan")


class Token(Base):
    """API token for node authentication."""
    __tablename__ = "tokens"

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    node_id = Column(String(64), unique=True, nullable=False, index=True)  # User-specified unique node name
    description = Column(String(256), nullable=True)
    token_hash = Column(String(128), nullable=False)  # Hashed token
    created_at = Column(Float, default=time.time)
    last_used_at = Column(Float, nullable=True)
    expires_at = Column(Float, nullable=True)  # None = no expiry
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="tokens")
    node = relationship("Node", back_populates="token", uselist=False)


class RevokedToken(Base):
    """Record of revoked tokens for global uniqueness."""
    __tablename__ = "revoked_tokens"

    id = Column(String(36), primary_key=True, default=new_id)
    node_id = Column(String(64), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False)
    revoked_at = Column(Float, default=time.time)
    reason = Column(String(64), nullable=True)  # "user_revoked", "refreshed", etc.


class Node(Base):
    """Registered agentic node on the marketplace."""
    __tablename__ = "nodes"

    id = Column(String(64), primary_key=True)  # Same as token.node_id
    an_id = Column(String(128), nullable=True)  # Canonical AN ID (wallet-suffix)
    wallet_address = Column(String(128), nullable=True)  # Wallet bound to an_id
    token_id = Column(String(36), ForeignKey("tokens.id"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    capability = Column(String(64), nullable=True)
    capabilities = Column(Text, nullable=True)  # JSON array
    price = Column(String(64), nullable=True)
    ip = Column(String(64), nullable=True)
    port = Column(Integer, nullable=True)
    region = Column(String(64), nullable=True)
    status = Column(String(16), default="offline")  # online, offline, busy
    is_listed = Column(Boolean, default=False)
    last_seen = Column(Float, nullable=True)
    registered_at = Column(Float, default=time.time)
    extra_data = Column(Text, nullable=True)  # JSON for extra data

    # Relationships
    token = relationship("Token", back_populates="node")

    # Indexes
    __table_args__ = (
        Index("ix_nodes_status_listed", "status", "is_listed"),
    )


class AuditLog(Base):
    """Structured audit trail for security-sensitive operations."""
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=new_id)
    timestamp = Column(Float, default=time.time, index=True)
    user_id = Column(String(36), nullable=True)  # may be null for unauthenticated
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(32), nullable=True)  # user / token / node / setting / doc
    target_id = Column(String(128), nullable=True)
    ip_address = Column(String(64), nullable=True)
    details = Column(Text, nullable=True)  # JSON string for extra context


class SystemSetting(Base):
    """System settings persisted in database."""
    __tablename__ = "system_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String(256), nullable=True)
    updated_at = Column(Float, default=time.time)
    updated_by = Column(String(36), nullable=True)  # User ID who last updated


class DocPage(Base):
    """Documentation pages for the admin docs system."""
    __tablename__ = "doc_pages"

    id = Column(String(36), primary_key=True, default=new_id)
    slug = Column(String(128), unique=True, nullable=False, index=True)  # URL slug
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=True)  # Markdown content
    category = Column(String(64), nullable=True)  # e.g., "getting-started", "api", "admin"
    order = Column(Integer, default=0)  # Display order within category
    is_published = Column(Boolean, default=True)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)
    created_by = Column(String(36), nullable=True)
    updated_by = Column(String(36), nullable=True)


def init_db():
    """Initialize database tables."""
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)
    _migrate_nodes_schema(engine)


def _migrate_nodes_schema(engine) -> None:
    """Best-effort additive migrations for ``nodes`` table columns."""
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(nodes)")).fetchall()
        existing = {str(r[1]) for r in rows} if rows else set()
        if "an_id" not in existing:
            conn.execute(text("ALTER TABLE nodes ADD COLUMN an_id TEXT"))
        if "wallet_address" not in existing:
            conn.execute(text("ALTER TABLE nodes ADD COLUMN wallet_address TEXT"))
