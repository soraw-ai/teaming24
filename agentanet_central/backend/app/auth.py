"""
Authentication module for AgentaNet Central Service.

Implements:
- Mock GitHub OAuth flow (for development)
- Session management
- Token authentication for API
"""

import hashlib
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_config
from .database import Token, User, get_db

TOKEN_BEARER = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> tuple[str, str]:
    """Generate a new API token. Returns (plain_token, hashed_token)."""
    import secrets
    config = get_config()
    plain = f"{config.security.token_prefix}{secrets.token_hex(24)}"
    return plain, hash_token(plain)


def create_session_token(user_id: str) -> str:
    """Create a JWT session token."""
    config = get_config()
    expire = time.time() + (config.security.session_expire_hours * 3600)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": time.time(),
    }
    return jwt.encode(payload, config.security.secret_key, algorithm=config.security.algorithm)


def verify_session_token(token: str) -> Optional[str]:
    """Verify session token and return user_id."""
    config = get_config()
    try:
        payload = jwt.decode(token, config.security.secret_key, algorithms=[config.security.algorithm])
        user_id = payload.get("sub")
        if user_id and payload.get("exp", 0) > time.time():
            return user_id
    except JWTError:
        pass
    return None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(TOKEN_BEARER),
    db: Session = Depends(get_db),
) -> User:
    """Get current authenticated user from session token."""
    token = None

    # Try Authorization header first
    if credentials:
        token = credentials.credentials

    # Fallback to cookie
    if not token:
        token = request.cookies.get("session")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    user_id = verify_session_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account suspended: {user.suspended_reason or 'No reason provided'}",
        )

    return user


async def get_token_auth(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
) -> Token:
    """Authenticate using API token (for node operations)."""
    token_hash = hash_token(credentials.credentials)

    token = db.query(Token).filter(
        Token.token_hash == token_hash,
        Token.is_active,
    ).first()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )

    # Check expiration
    if token.expires_at is not None and token.expires_at < time.time():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token has expired. Please refresh or create a new token.",
        )

    # Check if the owning user is suspended
    user = db.query(User).filter(User.id == token.user_id).first()
    if user and user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account suspended: {user.suspended_reason or 'No reason provided'}",
        )

    # Update last used
    token.last_used_at = time.time()
    db.commit()

    return token


async def get_optional_token_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(TOKEN_BEARER),
    db: Session = Depends(get_db),
) -> Optional[Token]:
    """Optionally authenticate using API token.

    - No Authorization header: return None (public access path).
    - Authorization header present: token must be valid, otherwise reject.
    """
    if not credentials:
        return None
    token_hash = hash_token(credentials.credentials)
    token = db.query(Token).filter(
        Token.token_hash == token_hash,
        Token.is_active,
    ).first()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )
    if token.expires_at is not None and token.expires_at < time.time():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token has expired. Please refresh or create a new token.",
        )

    # Keep behavior consistent with mandatory token auth.
    user = db.query(User).filter(User.id == token.user_id).first()
    if user and user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account suspended: {user.suspended_reason or 'No reason provided'}",
        )

    token.last_used_at = time.time()
    db.commit()
    return token


def get_mock_github_users() -> dict:
    """Get mock GitHub users from config."""
    config = get_config()
    users = {}
    for u in config.admin.mock_users:
        users[u.username.lower()] = {
            "id": u.github_id,
            "login": u.username,
            "email": u.email,
            "avatar_url": f"https://avatars.githubusercontent.com/u/{u.github_id}",
            "is_admin": u.is_admin,
        }

    # Fallback defaults if no users configured
    if not users:
        users = {
            "admin": {
                "id": "00000001",
                "login": "admin",
                "email": "admin@agentanet.io",
                "avatar_url": "https://avatars.githubusercontent.com/u/00000001",
                "is_admin": True,
            },
            "demo": {
                "id": "12345678",
                "login": "demo",
                "email": "demo@example.com",
                "avatar_url": "https://avatars.githubusercontent.com/u/12345678",
                "is_admin": False,
            },
        }
    return users


def mock_github_login(username: str) -> Optional[dict]:
    """Mock GitHub login for development."""
    users = get_mock_github_users()
    return users.get(username.lower())


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Verify user is an admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# ---------------------------------------------------------------------------
# Real GitHub OAuth helpers
# ---------------------------------------------------------------------------

async def github_exchange_code(code: str) -> Optional[dict]:
    """Exchange GitHub OAuth code for access token and fetch user profile."""
    import httpx
    config = get_config()
    if not config.github.enabled or not config.github.client_id:
        return None

    # Exchange code for access token
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": config.github.client_id,
                    "client_secret": config.github.client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return None

            # Fetch user profile
            user_resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            if user_resp.status_code != 200:
                return None
            user_data = user_resp.json()
            return {
                "id": str(user_data["id"]),
                "login": user_data["login"],
                "email": user_data.get("email"),
                "avatar_url": user_data.get("avatar_url"),
                "is_admin": False,  # Real GitHub users are not admin by default
            }
    except Exception:
        return None
