"""
AgentaNet Central Service - Main Application.

Provides:
- User authentication (Mock GitHub OAuth + real GitHub OAuth)
- Token management (max N per user, optional expiry)
- Marketplace node registration and discovery
- Audit logging for security-sensitive operations
- Admin dashboard API
"""

import asyncio
import json
import logging
import math
import time
from contextlib import asynccontextmanager
from typing import Any, List, Optional, TypeVar

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session

from .audit import log_audit
from .auth import (
    create_session_token,
    generate_token,
    get_admin_user,
    get_current_user,
    get_mock_github_users,
    get_optional_token_auth,
    get_token_auth,
    github_exchange_code,
    mock_github_login,
)
from .config import get_config
from .database import (
    AuditLog,
    DocPage,
    Node,
    RevokedToken,
    SystemSetting,
    Token,
    User,
    get_db,
    init_db,
)
from .id_utils import new_id

# Get config
config = get_config()

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.logging.level.upper(), logging.INFO),
    format=config.logging.format,
)
logger = logging.getLogger(__name__)

# Service start time for uptime tracking
_start_time: float = time.time()

# =============================================================================
# Rate Limiting (per-endpoint, with periodic cleanup)
# =============================================================================

# key = f"{client_ip}:{category}", value = list of timestamps
_rate_limit: dict[str, list[float]] = {}


def _get_rate_category(path: str) -> tuple[str, int]:
    """Return (category_name, max_requests) for the given URL path."""
    rl = config.rate_limit
    if path.startswith("/auth/"):
        return "auth", rl.auth_max_requests
    if path.startswith("/api/admin/"):
        return "admin", rl.admin_max_requests
    if path.startswith("/api/marketplace/"):
        return "marketplace", rl.marketplace_max_requests
    return "default", rl.max_requests


def check_rate_limit(client_ip: str, path: str) -> bool:
    """Check if client has exceeded the per-endpoint rate limit."""
    if not config.rate_limit.enabled:
        return True

    category, max_requests = _get_rate_category(path)
    key = f"{client_ip}:{category}"
    now = time.time()
    window = config.rate_limit.window_seconds

    if key not in _rate_limit:
        _rate_limit[key] = []

    # Prune old entries for this key
    _rate_limit[key] = [t for t in _rate_limit[key] if now - t < window]

    if len(_rate_limit[key]) >= max_requests:
        return False

    _rate_limit[key].append(now)
    return True


def _cleanup_rate_limit_entries():
    """Remove stale entries from the global rate-limit dict."""
    now = time.time()
    window = config.rate_limit.window_seconds
    stale_keys = []
    for key, timestamps in _rate_limit.items():
        _rate_limit[key] = [t for t in timestamps if now - t < window]
        if not _rate_limit[key]:
            stale_keys.append(key)
    for key in stale_keys:
        del _rate_limit[key]


# =============================================================================
# Structured Error Response
# =============================================================================

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


_ERROR_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


# =============================================================================
# Pagination
# =============================================================================

T = TypeVar("T")


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int


def paginate_query(query, page: int, page_size: int):
    """Apply pagination to a SQLAlchemy query. Returns (items, total)."""
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def make_paginated(items: list, total: int, page: int, page_size: int) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


# =============================================================================
# LIKE-pattern escaping
# =============================================================================

def _escape_like(value: str) -> str:
    """Escape special LIKE characters so they match literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# =============================================================================
# IP masking
# =============================================================================

def _mask_ip(ip: Optional[str]) -> Optional[str]:
    """Mask the last octet of an IPv4 address, or the host part of IPv6."""
    if not ip:
        return ip
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.***"
    return "***"


def _effective_node_status(node: Node) -> str:
    """Derive user-facing node status with listing/heartbeat consistency."""
    if not node.is_listed:
        return "offline"
    raw = (node.status or "").lower()
    if raw not in {"online", "offline", "busy"}:
        raw = "offline"
    last_seen = node.last_seen or 0
    if raw in {"online", "busy"}:
        if last_seen <= 0:
            return "offline"
        if (time.time() - last_seen) >= config.health_check.offline_threshold:
            return "offline"
    return raw or "offline"


# =============================================================================
# Background Tasks
# =============================================================================

async def node_health_check_loop():
    """Periodically check node health, clean up stale entries, and purge old data."""
    hc_config = config.health_check
    rl_cleanup_counter = 0

    while True:
        await asyncio.sleep(hc_config.interval)
        rl_cleanup_counter += hc_config.interval
        db = None
        try:
            db_gen = get_db()
            db = next(db_gen)
            now = time.time()

            # Mark offline if no heartbeat for offline_threshold
            offline_threshold = now - hc_config.offline_threshold
            db.query(Node).filter(
                Node.status == "online",
                Node.last_seen < offline_threshold,
            ).update({"status": "offline"})

            # Remove from listing if offline for delist_threshold
            delist_threshold = now - hc_config.delist_threshold
            db.query(Node).filter(
                Node.is_listed,
                Node.status == "offline",
                Node.last_seen < delist_threshold,
            ).update({"is_listed": False})

            # Purge nodes that have been offline+unlisted beyond purge_threshold
            purge_threshold = now - hc_config.purge_threshold
            purged = db.query(Node).filter(
                ~Node.is_listed,
                Node.status == "offline",
                Node.last_seen < purge_threshold,
            ).delete()
            if purged:
                logger.info(f"Purged {purged} stale node(s) from database")

            # Purge old revoked tokens beyond purge_threshold
            old_revoked = db.query(RevokedToken).filter(
                RevokedToken.revoked_at < purge_threshold,
            ).delete()
            if old_revoked:
                logger.info(f"Purged {old_revoked} old revoked token(s)")

            db.commit()
            logger.debug("Node health check completed")
        except Exception as e:
            logger.error(f"Node health check error: {e}")
        finally:
            if db:
                db.close()

        # Periodically clean rate limit entries
        if rl_cleanup_counter >= config.rate_limit.cleanup_interval:
            _cleanup_rate_limit_entries()
            rl_cleanup_counter = 0


# =============================================================================
# App Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    global _start_time
    _start_time = time.time()
    init_db()
    health_task = asyncio.create_task(node_health_check_loop())
    logger.info("AgentaNet Central Service started")
    yield
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    logger.info("AgentaNet Central Service stopped")


app = FastAPI(
    title="AgentaNet Central Service",
    description="Central authentication and marketplace service for AgentaNet",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors.allow_origins,
    allow_credentials=config.cors.allow_credentials,
    allow_methods=config.cors.allow_methods,
    allow_headers=config.cors.allow_headers,
)


# =============================================================================
# Middleware
# =============================================================================

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Rate limiting, request body size check, and security headers."""
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit (per-endpoint category)
    if not check_rate_limit(client_ip, request.url.path):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Too many requests. Please try again later.",
                }
            },
        )

    # Request body size limit (only for methods that carry a body)
    if request.method in ("POST", "PUT", "PATCH"):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > config.security.max_request_body_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Request body exceeds {config.security.max_request_body_bytes} bytes limit.",
                    }
                },
            )

    response = await call_next(request)

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    return response


# Custom HTTPException handler for consistent error format
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code = _ERROR_CODE_MAP.get(exc.status_code, "ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": str(exc.detail),
            }
        },
    )


# =============================================================================
# Health & Info
# =============================================================================

@app.get("/")
async def root():
    """Service info."""
    return {
        "service": "AgentaNet Central",
        "version": "0.2.0",
        "status": "running",
    }


@app.get("/health")
async def health(db: Session = Depends(get_db)):
    """Health check endpoint with DB connectivity verification."""
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    result = {
        "status": "healthy" if db_ok else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "version": "0.2.0",
        "timestamp": time.time(),
    }

    if not db_ok:
        return JSONResponse(status_code=503, content=result)
    return result


# =============================================================================
# Authentication
# =============================================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)


class LoginResponse(BaseModel):
    user_id: str
    username: str
    email: Optional[str]
    avatar_url: Optional[str]
    is_admin: bool
    token_max_per_user: int
    session_token: str


def _set_session_cookie(response: Response, session_token: str):
    """Set session cookie with security flags from config."""
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        max_age=config.security.session_expire_hours * 3600,
        samesite="lax",
        secure=config.security.cookie_secure,
    )


def _login_user(user: User, response: Response, request: Request, db: Session) -> LoginResponse:
    """Shared login logic: create session, set cookie, audit, return response."""
    if user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account suspended: {user.suspended_reason or 'No reason provided'}",
        )

    user.last_login_at = time.time()
    db.commit()
    db.refresh(user)

    session_token = create_session_token(user.id)
    _set_session_cookie(response, session_token)

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="user_login", user_id=user.id,
              target_type="user", target_id=user.id, ip_address=client_ip)
    db.commit()

    logger.info(f"User logged in: {user.username}")

    return LoginResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        avatar_url=user.avatar_url,
        is_admin=user.is_admin,
        token_max_per_user=config.security.token_max_per_user,
        session_token=session_token,
    )


@app.post("/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Mock GitHub login (development mode).

    In production with ``github.enabled = true``, use ``/auth/github`` instead.
    """
    if not config.admin.allow_mock_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mock login is disabled. Use GitHub OAuth login flow.",
        )

    github_user = mock_github_login(body.username)
    if not github_user:
        available = ", ".join(get_mock_github_users().keys())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unknown mock user. Available: {available}",
        )

    # Find or create user
    user = db.query(User).filter(User.github_id == github_user["id"]).first()
    if not user:
        user = User(
            github_id=github_user["id"],
            username=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
            is_admin=github_user.get("is_admin", False),
        )
        db.add(user)
        db.flush()
        logger.info(f"New user created: {user.username}")

    return _login_user(user, response, request, db)


# ---- Real GitHub OAuth ----

@app.get("/auth/github")
async def github_redirect():
    """Redirect user to GitHub for OAuth authorization."""
    if not config.github.enabled or not config.github.client_id:
        raise HTTPException(400, "GitHub OAuth is not enabled")
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={config.github.client_id}"
        f"&redirect_uri={config.github.callback_url}"
        f"&scope=read:user user:email"
    )
    return RedirectResponse(url)


@app.get("/auth/callback")
async def github_callback(
    code: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Handle GitHub OAuth callback."""
    if not config.github.enabled:
        raise HTTPException(400, "GitHub OAuth is not enabled")

    github_user = await github_exchange_code(code)
    if not github_user:
        raise HTTPException(401, "GitHub authentication failed")

    # Find or create user
    user = db.query(User).filter(User.github_id == github_user["id"]).first()
    if not user:
        user = User(
            github_id=github_user["id"],
            username=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
            is_admin=False,
        )
        db.add(user)
        db.flush()
        logger.info(f"New GitHub user created: {user.username}")

    resp = _login_user(user, response, request, db)
    # For browser flow: redirect to frontend root after login
    redirect = RedirectResponse(url="/", status_code=302)
    _set_session_cookie(redirect, resp.session_token)
    return redirect


@app.post("/auth/logout")
async def logout(response: Response, user: User = Depends(get_current_user)):
    """Logout current user."""
    response.delete_cookie("session")
    logger.info(f"User logged out: {user.username}")
    return {"status": "logged_out"}


@app.get("/api/user/me")
async def get_me(user: User = Depends(get_current_user)):
    """Get current user profile."""
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "is_admin": user.is_admin,
        "is_suspended": user.is_suspended,
        "created_at": user.created_at,
        "token_max_per_user": config.security.token_max_per_user,
    }


# =============================================================================
# Token Management
# =============================================================================

class CreateTokenRequest(BaseModel):
    node_id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: Optional[str] = Field(None, max_length=256)
    expires_in_days: Optional[int] = Field(None, ge=1, le=3650)


class TokenResponse(BaseModel):
    id: str
    node_id: str
    description: Optional[str]
    created_at: float
    last_used_at: Optional[float]
    expires_at: Optional[float]
    is_active: bool
    plain_token: Optional[str] = None  # Only returned on creation


@app.get("/api/tokens", response_model=List[TokenResponse])
async def list_tokens(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List all tokens for current user."""
    tokens = db.query(Token).filter(Token.user_id == user.id).all()
    return [
        TokenResponse(
            id=t.id,
            node_id=t.node_id,
            description=t.description,
            created_at=t.created_at,
            last_used_at=t.last_used_at,
            expires_at=t.expires_at,
            is_active=t.is_active,
        )
        for t in tokens
    ]


@app.post("/api/tokens", response_model=TokenResponse)
async def create_token(
    body: CreateTokenRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new API token.

    - Max N active tokens per user (configurable)
    - node_id must be globally unique
    - Optional expiry via ``expires_in_days``
    """
    max_tokens = config.security.token_max_per_user
    active_count = db.query(Token).filter(
        Token.user_id == user.id,
        Token.is_active,
    ).count()

    if active_count >= max_tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {max_tokens} active tokens allowed. Revoke an existing token first.",
        )

    # Check node_id uniqueness
    existing = db.query(Token).filter(Token.node_id == body.node_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node ID '{body.node_id}' is already taken",
        )

    revoked = db.query(RevokedToken).filter(RevokedToken.node_id == body.node_id).first()
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node ID '{body.node_id}' was previously used and cannot be reused",
        )

    # Determine expiry
    expires_at = None
    expire_days = body.expires_in_days or config.security.token_expire_days
    if expire_days is not None:
        expires_at = time.time() + (expire_days * 86400)

    plain_token, token_hash = generate_token()

    token = Token(
        user_id=user.id,
        node_id=body.node_id,
        description=body.description,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(token)
    db.flush()

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="token_created", user_id=user.id,
              target_type="token", target_id=token.id,
              ip_address=client_ip,
              details={"node_id": body.node_id, "expires_at": expires_at})
    db.commit()
    db.refresh(token)

    logger.info(f"Token created for user {user.username}: node_id={body.node_id}")

    return TokenResponse(
        id=token.id,
        node_id=token.node_id,
        description=token.description,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        is_active=token.is_active,
        plain_token=plain_token,
    )


@app.post("/api/tokens/{token_id}/refresh", response_model=TokenResponse)
async def refresh_token(
    token_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Refresh (regenerate) a token. Old token becomes invalid."""
    token = db.query(Token).filter(
        Token.id == token_id,
        Token.user_id == user.id,
    ).first()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    # Record old token as revoked
    revoked = RevokedToken(
        id=new_id(),
        node_id=token.node_id,
        token_hash=token.token_hash,
        reason="refreshed",
    )
    db.add(revoked)

    # Generate new token — reset expiry based on current config
    plain_token, new_hash = generate_token()
    token.token_hash = new_hash
    token.created_at = time.time()
    token.last_used_at = None
    if config.security.token_expire_days:
        token.expires_at = time.time() + (config.security.token_expire_days * 86400)

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="token_refreshed", user_id=user.id,
              target_type="token", target_id=token.id, ip_address=client_ip)
    db.commit()
    db.refresh(token)

    logger.info(f"Token refreshed for user {user.username}: node_id={token.node_id}")

    return TokenResponse(
        id=token.id,
        node_id=token.node_id,
        description=token.description,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        is_active=token.is_active,
        plain_token=plain_token,
    )


@app.delete("/api/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke (delete) a token."""
    token = db.query(Token).filter(
        Token.id == token_id,
        Token.user_id == user.id,
    ).first()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    # Record as revoked
    revoked = RevokedToken(
        id=new_id(),
        node_id=token.node_id,
        token_hash=token.token_hash,
        reason="user_revoked",
    )
    db.add(revoked)

    # Delete associated node if exists
    db.query(Node).filter(Node.id == token.node_id).delete()

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="token_revoked", user_id=user.id,
              target_type="token", target_id=token.id,
              ip_address=client_ip,
              details={"node_id": token.node_id})

    db.delete(token)
    db.commit()

    logger.info(f"Token revoked for user {user.username}: node_id={token.node_id}")

    return {"status": "revoked", "token_id": token_id}


# =============================================================================
# Marketplace API (Token Auth)
# =============================================================================

class CapabilityItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field("", max_length=512)


class NodeRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=1024)
    capability: Optional[str] = Field(None, max_length=64)
    capabilities: Optional[List[str | CapabilityItem]] = None
    an_id: Optional[str] = Field(None, max_length=128)
    wallet_address: Optional[str] = Field(None, max_length=128)
    price: Optional[str] = Field(None, max_length=64)
    ip: Optional[str] = Field(None, max_length=64)
    port: Optional[int] = Field(None, ge=1, le=65535)
    region: Optional[str] = Field(None, max_length=64)


class NodeResponse(BaseModel):
    id: str
    an_id: Optional[str]
    wallet_address: Optional[str]
    name: str
    description: Optional[str]
    capability: Optional[str]
    capabilities: Optional[List[CapabilityItem]]
    price: Optional[str]
    ip: Optional[str]
    port: Optional[int]
    region: Optional[str]
    status: str
    is_listed: bool
    last_seen: Optional[float]


def _normalize_capabilities_for_storage(
    capabilities: Optional[List[str | CapabilityItem]],
) -> list[dict[str, str]]:
    """Normalize capabilities into a stable list[{name, description}] shape."""
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in capabilities or []:
        if isinstance(item, CapabilityItem):
            name = item.name.strip()
            description = item.description.strip()
        elif isinstance(item, str):
            name = item.strip()
            description = ""
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            description = str(item.get("description", "")).strip()
        else:
            name = str(item).strip()
            description = ""

        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append({"name": name, "description": description})
    return normalized


def _parse_capabilities_from_db(raw: Optional[str]) -> Optional[list[CapabilityItem]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    normalized = _normalize_capabilities_for_storage(parsed if isinstance(parsed, list) else [])
    if not normalized:
        return None
    return [CapabilityItem(**item) for item in normalized]


def _summarize_node_description(
    description: Optional[str],
    capability: Optional[str],
    capabilities: Optional[List[str | CapabilityItem]],
) -> str:
    """Guarantee description quality for marketplace listing cards."""
    clean = (description or "").strip()
    if clean:
        return clean

    normalized = _normalize_capabilities_for_storage(capabilities)
    names = [c["name"] for c in normalized]
    if capability and capability not in names:
        names.insert(0, capability)
    names = names[:4]
    if names:
        return (
            "Agentic node specialized in "
            + names[0]
            + ("; additional capabilities: " + ", ".join(names[1:]) if len(names) > 1 else "")
        )
    return "Agentic node available for distributed task execution."


def _wallet_from_an_id(an_id: Optional[str]) -> Optional[str]:
    """Extract wallet prefix from ``an_id`` formatted as ``0x...-suffix``."""
    value = (an_id or "").strip()
    if not value or "-" not in value:
        return None
    wallet = value.split("-", 1)[0].strip()
    if not wallet.lower().startswith("0x") or len(wallet) < 4:
        return None
    return wallet


def _normalize_node_identity(
    an_id: Optional[str],
    wallet_address: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Normalize and validate AN identity fields."""
    clean_an_id = (an_id or "").strip() or None
    clean_wallet = (wallet_address or "").strip() or None
    inferred_wallet = _wallet_from_an_id(clean_an_id)

    if not clean_wallet and inferred_wallet:
        clean_wallet = inferred_wallet

    if clean_wallet and inferred_wallet and clean_wallet.lower() != inferred_wallet.lower():
        logger.warning(
            "Identity mismatch on register: an_id wallet prefix %s != wallet_address %s; using an_id prefix",
            inferred_wallet,
            clean_wallet,
        )
        # Prefer an_id wallet prefix for consistency; avoid 422 for clients that send both
        clean_wallet = inferred_wallet

    return clean_an_id, clean_wallet


async def _verify_node_reachability(ip: Optional[str], port: Optional[int]) -> bool:
    """Best-effort reachability probe; never raises."""
    if not ip or not port:
        return False
    try:
        import httpx

        timeout = config.marketplace.verify_timeout
        base = f"http://{ip}:{port}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            for path in ("/health", "/api/network/status"):
                try:
                    resp = await client.get(f"{base}{path}")
                    if resp.status_code < 500:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def _node_to_response(node: Node, mask_ip: bool = False) -> NodeResponse:
    """Convert a Node ORM object to a NodeResponse, optionally masking IP."""
    caps = _parse_capabilities_from_db(node.capabilities)
    return NodeResponse(
        id=node.id,
        an_id=node.an_id,
        wallet_address=node.wallet_address,
        name=node.name,
        description=node.description,
        capability=node.capability,
        capabilities=caps,
        price=node.price,
        ip=_mask_ip(node.ip) if mask_ip else node.ip,
        port=None if mask_ip else node.port,
        region=node.region,
        status=_effective_node_status(node),
        is_listed=node.is_listed,
        last_seen=node.last_seen,
    )


@app.post("/api/marketplace/register", response_model=NodeResponse)
async def register_node(
    body: NodeRegisterRequest,
    request: Request,
    token: Token = Depends(get_token_auth),
    db: Session = Depends(get_db),
):
    """Register or update node on marketplace."""
    node = db.query(Node).filter(Node.id == token.node_id).first()
    normalized_caps = _normalize_capabilities_for_storage(body.capabilities)
    primary_capability = (body.capability or "").strip()
    if not primary_capability and normalized_caps:
        primary_capability = str(normalized_caps[0].get("name") or "").strip()
    if primary_capability and all(
        str(c.get("name", "")).strip().lower() != primary_capability.lower()
        for c in normalized_caps
    ):
        normalized_caps.insert(0, {"name": primary_capability, "description": ""})
    description = _summarize_node_description(
        body.description,
        primary_capability or None,
        normalized_caps,
    )
    an_id, wallet_address = _normalize_node_identity(body.an_id, body.wallet_address)

    if not node:
        node = Node(
            id=token.node_id,
            token_id=token.id,
            name=body.name,
        )
        db.add(node)

    node.name = body.name
    node.description = description
    node.capability = primary_capability or None
    node.capabilities = json.dumps(normalized_caps) if normalized_caps else None
    node.an_id = an_id
    node.wallet_address = wallet_address
    node.price = body.price
    node.ip = body.ip
    node.port = body.port
    node.region = body.region
    node.status = "online"
    node.is_listed = True
    node.last_seen = time.time()

    client_ip = request.client.host if request.client else "unknown"
    verify_ok: bool | None = None
    if config.marketplace.verify_on_register:
        verify_ok = await _verify_node_reachability(body.ip, body.port)
        if not verify_ok:
            logger.warning(
                "Node registered but reachability probe failed: node_id=%s target=%s:%s",
                token.node_id,
                body.ip,
                body.port,
            )

    log_audit(db, action="node_registered", user_id=token.user_id,
              target_type="node", target_id=node.id, ip_address=client_ip,
              details={"verify_ok": verify_ok} if verify_ok is not None else None)
    db.commit()
    db.refresh(node)

    logger.info(f"Node registered/updated: {node.id}")

    # Authenticated requester sees full IP
    return _node_to_response(node, mask_ip=False)


@app.post("/api/marketplace/heartbeat")
async def heartbeat(
    token: Token = Depends(get_token_auth),
    db: Session = Depends(get_db),
):
    """Update node heartbeat (keep-alive)."""
    node = db.query(Node).filter(Node.id == token.node_id).first()

    if not node:
        raise HTTPException(status_code=404, detail="Node not registered. Call /register first.")
    if not node.is_listed:
        raise HTTPException(status_code=409, detail="Node is unlisted. Call /register first.")

    node.status = "online"
    node.last_seen = time.time()
    db.commit()

    return {"status": "ok", "node_id": token.node_id, "last_seen": node.last_seen}


@app.post("/api/marketplace/unlist")
async def unlist_node(
    request: Request,
    token: Token = Depends(get_token_auth),
    db: Session = Depends(get_db),
):
    """Remove node from marketplace listing."""
    node = db.query(Node).filter(Node.id == token.node_id).first()

    if node:
        node.is_listed = False
        node.status = "offline"
        node.last_seen = time.time()
        client_ip = request.client.host if request.client else "unknown"
        log_audit(db, action="node_unlisted", user_id=token.user_id,
                  target_type="node", target_id=token.node_id, ip_address=client_ip)
        db.commit()
        logger.info(f"Node unlisted: {token.node_id}")

    return {"status": "unlisted", "node_id": token.node_id}


@app.get("/api/marketplace/nodes")
async def list_marketplace_nodes(
    search: Optional[str] = Query(None, max_length=128),
    capability: Optional[str] = Query(None, max_length=128),
    region: Optional[str] = Query(None, max_length=128),
    status: Optional[str] = Query(None, max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(None, ge=1),
    auth_token: Optional[Token] = Depends(get_optional_token_auth),
    db: Session = Depends(get_db),
):
    """List marketplace nodes (public endpoint, IP masked for unauthenticated requests)."""
    mp = config.marketplace
    if page_size is None:
        page_size = mp.default_page_size
    page_size = min(page_size, mp.max_page_size)

    query = db.query(Node).filter(Node.is_listed)

    if search:
        safe = _escape_like(search)
        query = query.filter(
            (Node.id.ilike(f"%{safe}%", escape="\\")) |
            (Node.an_id.ilike(f"%{safe}%", escape="\\")) |
            (Node.wallet_address.ilike(f"%{safe}%", escape="\\")) |
            (Node.name.ilike(f"%{safe}%", escape="\\")) |
            (Node.description.ilike(f"%{safe}%", escape="\\"))
        )

    if capability:
        safe_cap = _escape_like(capability)
        query = query.filter(
            (Node.capability.ilike(f"%{safe_cap}%", escape="\\")) |
            (Node.capabilities.ilike(f"%{safe_cap}%", escape="\\"))
        )

    if region:
        query = query.filter(Node.region.ilike(f"%{_escape_like(region)}%", escape="\\"))

    if status:
        normalized_status = status.lower().strip()
        if normalized_status not in {"online", "offline", "busy"}:
            raise HTTPException(status_code=400, detail="Invalid status filter")

        now_ts = time.time()
        threshold = max(0.0, float(config.health_check.offline_threshold))
        stale_cutoff = now_ts - threshold

        if normalized_status == "online":
            query = query.filter(
                Node.status == "online",
                Node.last_seen.is_not(None),
                Node.last_seen > 0,
                Node.last_seen >= stale_cutoff,
            )
        elif normalized_status == "busy":
            query = query.filter(
                Node.status == "busy",
                Node.last_seen.is_not(None),
                Node.last_seen > 0,
                Node.last_seen >= stale_cutoff,
            )
        else:
            # Effective offline for listed nodes:
            # - explicit offline
            # - unknown/invalid status
            # - online/busy but stale/missing heartbeat
            query = query.filter(
                or_(
                    Node.status == "offline",
                    ~Node.status.in_(["online", "offline", "busy"]),
                    and_(
                        Node.status.in_(["online", "busy"]),
                        or_(
                            Node.last_seen.is_(None),
                            Node.last_seen <= 0,
                            Node.last_seen < stale_cutoff,
                        ),
                    ),
                )
            )

    query = query.order_by(Node.last_seen.desc())
    items, total = paginate_query(query, page, page_size)

    # Mask IP if not authenticated AND config says so
    should_mask = not mp.expose_ip_publicly and auth_token is None

    result = [_node_to_response(n, mask_ip=should_mask) for n in items]

    return make_paginated(result, total, page, page_size)


@app.get("/api/marketplace/nodes/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: str,
    auth_token: Optional[Token] = Depends(get_optional_token_auth),
    db: Session = Depends(get_db),
):
    """Get specific node by ID."""
    node = db.query(Node).filter(Node.id == node_id).first()

    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    # Hide unlisted nodes unless requester is the same node token owner.
    if not node.is_listed:
        requester_id = auth_token.node_id if auth_token else None
        if requester_id != node_id:
            raise HTTPException(status_code=404, detail="Node not found")

    mp = config.marketplace
    should_mask = not mp.expose_ip_publicly and auth_token is None
    return _node_to_response(node, mask_ip=should_mask)


@app.get("/api/marketplace/me")
async def get_my_node(
    token: Token = Depends(get_token_auth),
    db: Session = Depends(get_db),
):
    """Get listing state for the token owner node."""
    node = db.query(Node).filter(Node.id == token.node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not registered")
    return {
        "listed": bool(node.is_listed),
        "node": _node_to_response(node, mask_ip=False),
    }


# =============================================================================
# Admin API (Super Admin Only)
# =============================================================================

@app.get("/api/admin/stats")
async def get_admin_stats(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get system statistics (admin only)."""
    total_users = db.query(User).count()
    total_tokens = db.query(Token).count()
    active_tokens = db.query(Token).filter(Token.is_active).count()
    all_nodes = db.query(Node).all()
    total_nodes = len(all_nodes)
    listed_nodes = sum(1 for node in all_nodes if node.is_listed)
    online_nodes = sum(1 for node in all_nodes if _effective_node_status(node) == "online")
    revoked_tokens_count = db.query(RevokedToken).count()
    suspended_users = db.query(User).filter(User.is_suspended).count()
    audit_entries = db.query(AuditLog).count()

    return {
        "users": {
            "total": total_users,
            "suspended": suspended_users,
        },
        "tokens": {
            "total": total_tokens,
            "active": active_tokens,
            "revoked": revoked_tokens_count,
        },
        "nodes": {
            "total": total_nodes,
            "online": online_nodes,
            "listed": listed_nodes,
        },
        "audit_entries": audit_entries,
        "timestamp": time.time(),
    }


class AdminUserResponse(BaseModel):
    id: str
    username: str
    email: Optional[str]
    avatar_url: Optional[str]
    is_admin: bool
    is_suspended: bool
    suspended_reason: Optional[str]
    created_at: float
    last_login_at: Optional[float]
    token_count: int


@app.get("/api/admin/users")
async def get_all_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all users (admin only, paginated)."""
    query = db.query(User).order_by(User.created_at.desc())
    items, total = paginate_query(query, page, page_size)

    result = []
    for user in items:
        token_count = db.query(Token).filter(Token.user_id == user.id).count()
        result.append(AdminUserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            avatar_url=user.avatar_url,
            is_admin=user.is_admin,
            is_suspended=user.is_suspended,
            suspended_reason=user.suspended_reason,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            token_count=token_count,
        ))

    return make_paginated(result, total, page, page_size)


class AdminTokenResponse(BaseModel):
    id: str
    user_id: str
    username: str
    node_id: str
    description: Optional[str]
    created_at: float
    last_used_at: Optional[float]
    expires_at: Optional[float]
    is_active: bool


@app.get("/api/admin/tokens")
async def get_all_tokens(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all tokens (admin only, paginated)."""
    query = db.query(Token).order_by(Token.created_at.desc())
    items, total = paginate_query(query, page, page_size)

    result = []
    for token in items:
        user = db.query(User).filter(User.id == token.user_id).first()
        result.append(AdminTokenResponse(
            id=token.id,
            user_id=token.user_id,
            username=user.username if user else "unknown",
            node_id=token.node_id,
            description=token.description,
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            expires_at=token.expires_at,
            is_active=token.is_active,
        ))

    return make_paginated(result, total, page, page_size)


class AdminNodeResponse(BaseModel):
    id: str
    an_id: Optional[str]
    wallet_address: Optional[str]
    name: str
    description: Optional[str]
    capability: Optional[str]
    ip: Optional[str]
    port: Optional[int]
    region: Optional[str]
    status: str
    is_listed: bool
    last_seen: Optional[float]
    registered_at: float
    owner_username: str


@app.get("/api/admin/nodes")
async def get_all_nodes(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all nodes (admin only, paginated)."""
    query = db.query(Node).order_by(Node.last_seen.desc())
    items, total = paginate_query(query, page, page_size)

    result = []
    for node in items:
        token = db.query(Token).filter(Token.id == node.token_id).first()
        owner_username = "unknown"
        if token:
            user = db.query(User).filter(User.id == token.user_id).first()
            if user:
                owner_username = user.username

        result.append(AdminNodeResponse(
            id=node.id,
            an_id=node.an_id,
            wallet_address=node.wallet_address,
            name=node.name,
            description=node.description,
            capability=node.capability,
            ip=node.ip,
            port=node.port,
            region=node.region,
            status=_effective_node_status(node),
            is_listed=node.is_listed,
            last_seen=node.last_seen,
            registered_at=node.registered_at,
            owner_username=owner_username,
        ))

    return make_paginated(result, total, page, page_size)


@app.delete("/api/admin/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a user (admin only)."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Delete associated nodes first
    tokens = db.query(Token).filter(Token.user_id == user_id).all()
    for token in tokens:
        db.query(Node).filter(Node.token_id == token.id).delete()

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="admin_delete_user", user_id=admin.id,
              target_type="user", target_id=user_id, ip_address=client_ip,
              details={"deleted_username": user.username})

    db.delete(user)
    db.commit()

    logger.info(f"Admin deleted user: {user.username}")
    return {"status": "deleted", "user_id": user_id}


@app.delete("/api/admin/nodes/{node_id}")
async def admin_delete_node(
    node_id: str,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a node (admin only)."""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="admin_delete_node", user_id=admin.id,
              target_type="node", target_id=node_id, ip_address=client_ip)

    db.delete(node)
    db.commit()

    logger.info(f"Admin deleted node: {node_id}")
    return {"status": "deleted", "node_id": node_id}


# =============================================================================
# Admin — User Suspension
# =============================================================================

class SuspendRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=256)


@app.post("/api/admin/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    body: SuspendRequest,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Suspend a user account (admin only)."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot suspend yourself")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_suspended = True
    user.suspended_reason = body.reason

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="admin_suspend_user", user_id=admin.id,
              target_type="user", target_id=user_id, ip_address=client_ip,
              details={"reason": body.reason})
    db.commit()

    logger.info(f"Admin suspended user: {user.username}")
    return {"status": "suspended", "user_id": user_id, "reason": body.reason}


@app.post("/api/admin/users/{user_id}/unsuspend")
async def unsuspend_user(
    user_id: str,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Unsuspend a user account (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_suspended = False
    user.suspended_reason = None

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="admin_unsuspend_user", user_id=admin.id,
              target_type="user", target_id=user_id, ip_address=client_ip)
    db.commit()

    logger.info(f"Admin unsuspended user: {user.username}")
    return {"status": "unsuspended", "user_id": user_id}


# =============================================================================
# Admin — Audit Log
# =============================================================================

class AuditLogResponse(BaseModel):
    id: str
    timestamp: float
    user_id: Optional[str]
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    ip_address: Optional[str]
    details: Optional[str]


@app.get("/api/admin/audit")
async def get_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    action: Optional[str] = Query(None, max_length=64),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List audit log entries (admin only, paginated)."""
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())

    if action:
        query = query.filter(AuditLog.action == action)

    items, total = paginate_query(query, page, page_size)

    result = [
        AuditLogResponse(
            id=a.id,
            timestamp=a.timestamp,
            user_id=a.user_id,
            action=a.action,
            target_type=a.target_type,
            target_id=a.target_id,
            ip_address=a.ip_address,
            details=a.details,
        )
        for a in items
    ]

    return make_paginated(result, total, page, page_size)


# =============================================================================
# Admin Settings API
# =============================================================================

class SettingUpdate(BaseModel):
    value: str
    description: Optional[str] = None


class SettingResponse(BaseModel):
    key: str
    value: Optional[str]
    description: Optional[str]
    updated_at: float


@app.get("/api/admin/settings", response_model=List[SettingResponse])
async def get_all_settings(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get all system settings (admin only)."""
    settings = db.query(SystemSetting).order_by(SystemSetting.key).all()
    return [
        SettingResponse(
            key=s.key,
            value=s.value,
            description=s.description,
            updated_at=s.updated_at,
        )
        for s in settings
    ]


@app.get("/api/admin/settings/{key}", response_model=SettingResponse)
async def get_setting(
    key: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get a specific setting (admin only)."""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    return SettingResponse(
        key=setting.key,
        value=setting.value,
        description=setting.description,
        updated_at=setting.updated_at,
    )


@app.put("/api/admin/settings/{key}", response_model=SettingResponse)
async def update_setting(
    key: str,
    data: SettingUpdate,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update or create a setting (admin only)."""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()

    if setting:
        setting.value = data.value
        if data.description:
            setting.description = data.description
        setting.updated_at = time.time()
        setting.updated_by = admin.id
    else:
        setting = SystemSetting(
            key=key,
            value=data.value,
            description=data.description,
            updated_at=time.time(),
            updated_by=admin.id,
        )
        db.add(setting)

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="setting_updated", user_id=admin.id,
              target_type="setting", target_id=key, ip_address=client_ip)
    db.commit()
    db.refresh(setting)

    logger.info(f"Admin updated setting: {key}")
    return SettingResponse(
        key=setting.key,
        value=setting.value,
        description=setting.description,
        updated_at=setting.updated_at,
    )


@app.delete("/api/admin/settings/{key}")
async def delete_setting(
    key: str,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a setting (admin only)."""
    setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    client_ip = request.client.host if request.client else "unknown"
    log_audit(db, action="setting_deleted", user_id=admin.id,
              target_type="setting", target_id=key, ip_address=client_ip)

    db.delete(setting)
    db.commit()

    logger.info(f"Admin deleted setting: {key}")
    return {"status": "deleted", "key": key}


# =============================================================================
# Admin Docs API
# =============================================================================

class DocPageCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=256)
    content: Optional[str] = None
    category: Optional[str] = None
    order: int = 0
    is_published: bool = True


class DocPageUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    order: Optional[int] = None
    is_published: Optional[bool] = None


class DocPageResponse(BaseModel):
    id: str
    slug: str
    title: str
    content: Optional[str]
    category: Optional[str]
    order: int
    is_published: bool
    created_at: float
    updated_at: float


@app.get("/api/admin/docs", response_model=List[DocPageResponse])
async def get_all_docs(
    category: Optional[str] = None,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get all documentation pages (admin only)."""
    query = db.query(DocPage)
    if category:
        query = query.filter(DocPage.category == category)

    pages = query.order_by(DocPage.category, DocPage.order, DocPage.title).all()
    return [
        DocPageResponse(
            id=p.id,
            slug=p.slug,
            title=p.title,
            content=p.content,
            category=p.category,
            order=p.order,
            is_published=p.is_published,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in pages
    ]


@app.get("/api/admin/docs/{slug}", response_model=DocPageResponse)
async def get_doc(
    slug: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get a specific documentation page (admin only)."""
    page = db.query(DocPage).filter(DocPage.slug == slug).first()
    if not page:
        raise HTTPException(status_code=404, detail="Doc page not found")

    return DocPageResponse(
        id=page.id,
        slug=page.slug,
        title=page.title,
        content=page.content,
        category=page.category,
        order=page.order,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


@app.post("/api/admin/docs", response_model=DocPageResponse)
async def create_doc(
    data: DocPageCreate,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Create a new documentation page (admin only)."""
    existing = db.query(DocPage).filter(DocPage.slug == data.slug).first()
    if existing:
        raise HTTPException(status_code=400, detail="Slug already exists")

    page = DocPage(
        slug=data.slug,
        title=data.title,
        content=data.content,
        category=data.category,
        order=data.order,
        is_published=data.is_published,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(page)
    db.commit()
    db.refresh(page)

    logger.info(f"Admin created doc page: {data.slug}")
    return DocPageResponse(
        id=page.id,
        slug=page.slug,
        title=page.title,
        content=page.content,
        category=page.category,
        order=page.order,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


@app.put("/api/admin/docs/{slug}", response_model=DocPageResponse)
async def update_doc(
    slug: str,
    data: DocPageUpdate,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update a documentation page (admin only)."""
    page = db.query(DocPage).filter(DocPage.slug == slug).first()
    if not page:
        raise HTTPException(status_code=404, detail="Doc page not found")

    if data.title is not None:
        page.title = data.title
    if data.content is not None:
        page.content = data.content
    if data.category is not None:
        page.category = data.category
    if data.order is not None:
        page.order = data.order
    if data.is_published is not None:
        page.is_published = data.is_published

    page.updated_at = time.time()
    page.updated_by = admin.id

    db.commit()
    db.refresh(page)

    logger.info(f"Admin updated doc page: {slug}")
    return DocPageResponse(
        id=page.id,
        slug=page.slug,
        title=page.title,
        content=page.content,
        category=page.category,
        order=page.order,
        is_published=page.is_published,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


@app.delete("/api/admin/docs/{slug}")
async def delete_doc(
    slug: str,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a documentation page (admin only)."""
    page = db.query(DocPage).filter(DocPage.slug == slug).first()
    if not page:
        raise HTTPException(status_code=404, detail="Doc page not found")

    db.delete(page)
    db.commit()

    logger.info(f"Admin deleted doc page: {slug}")
    return {"status": "deleted", "slug": slug}


@app.post("/api/admin/reset")
async def reset_all_data(
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Wipe all data and return the service to a fresh-deployment state (admin only)."""
    body = await request.json()
    if body.get("confirm") != "RESET ALL DATA":
        raise HTTPException(status_code=400, detail="Confirmation text does not match")

    # Clear every table
    db.query(AuditLog).delete()
    db.query(Node).delete()
    db.query(RevokedToken).delete()
    db.query(Token).delete()
    db.query(SystemSetting).delete()
    db.query(DocPage).delete()
    db.query(User).delete()
    db.commit()

    logger.warning("Full data reset performed by admin %s", admin.username)
    return {"status": "reset"}


# =============================================================================
# API Versioning — mount all /api/* routes under /api/v1/* as well
# =============================================================================

# Collect all existing /api/ routes and duplicate them under /api/v1/
_v1_router = APIRouter(prefix="/api/v1")
for route in list(app.routes):
    if hasattr(route, "path") and route.path.startswith("/api/"):
        # Clone the route with the /api/ prefix stripped (router adds /api/v1/)
        suffix = route.path[len("/api"):]  # e.g. "/tokens" from "/api/tokens"
        _v1_router.add_api_route(
            suffix,
            route.endpoint,
            methods=list(route.methods or {"GET"}),
            response_model=getattr(route, "response_model", None),
            name=f"v1_{route.name}" if route.name else None,
        )
app.include_router(_v1_router)


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, ws="wsproto")
