"""
Configuration management for AgentaNet Central Service.

Loads configuration from YAML file with environment variable overrides.
"""

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Default config path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1
    reload: bool = False


@dataclass
class DatabaseConfig:
    path: str = "data/agentanet.db"

    def get_absolute_path(self, base_path: Path) -> Path:
        """Get absolute database path."""
        p = Path(self.path)
        if p.is_absolute():
            return p
        return base_path / p


@dataclass
class SecurityConfig:
    secret_key: Optional[str] = None
    algorithm: str = "HS256"
    session_expire_hours: int = 24
    token_max_per_user: int = 5
    token_prefix: str = "agn_"
    token_expire_days: Optional[int] = None  # None = no expiry; set e.g. 365
    cookie_secure: bool = False  # Set True in production (HTTPS)
    max_request_body_bytes: int = 65536  # 64 KB

    def __post_init__(self):
        # Generate secret key if not provided (warn in production)
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)
            logger.warning(
                "No SECRET_KEY configured! Generated random key. "
                "Sessions will be invalidated on restart. "
                "Set AGENTANET_SECRET_KEY in production."
            )


@dataclass
class RateLimitConfig:
    enabled: bool = True
    window_seconds: int = 60
    max_requests: int = 60
    auth_max_requests: int = 10
    marketplace_max_requests: int = 120
    admin_max_requests: int = 30
    cleanup_interval: int = 300


@dataclass
class CorsConfig:
    allow_origins: List[str] = field(default_factory=lambda: ["*"])
    allow_credentials: bool = True
    allow_methods: List[str] = field(default_factory=lambda: ["*"])
    allow_headers: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class HealthCheckConfig:
    interval: int = 60
    offline_threshold: int = 300
    delist_threshold: int = 3600
    purge_threshold: int = 2592000  # 30 days — delete offline+unlisted nodes older than this


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = None


@dataclass
class MockUser:
    username: str
    github_id: str
    email: str
    is_admin: bool = False


@dataclass
class AdminConfig:
    allow_mock_login: bool = False
    mock_users: List[MockUser] = field(default_factory=list)


@dataclass
class MarketplaceConfig:
    expose_ip_publicly: bool = False  # If False, mask IP for unauthenticated requests
    verify_on_register: bool = False  # If True, probe node on register (soft check)
    verify_timeout: float = 3.0
    default_page_size: int = 50
    max_page_size: int = 100


@dataclass
class GithubConfig:
    enabled: bool = False
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    callback_url: str = "http://localhost:8080/auth/callback"


@dataclass
class Config:
    """Main configuration container."""
    server: Optional[ServerConfig] = None
    database: Optional[DatabaseConfig] = None
    security: Optional[SecurityConfig] = None
    rate_limit: Optional[RateLimitConfig] = None
    cors: Optional[CorsConfig] = None
    health_check: Optional[HealthCheckConfig] = None
    logging: Optional[LoggingConfig] = None
    admin: Optional[AdminConfig] = None
    github: Optional[GithubConfig] = None
    marketplace: Optional[MarketplaceConfig] = None

    _config_path: Path = field(default=DEFAULT_CONFIG_PATH, repr=False)


def _get_env(key: str, default: Any = None) -> Any:
    """Get environment variable with optional default."""
    return os.environ.get(key, default)


def _expand_env_vars(value: Any) -> Any:
    """Expand ${VAR} patterns in string values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var)
    return value


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load configuration from YAML file with env overrides."""
    path = config_path or Path(_get_env("AGENTANET_CONFIG", DEFAULT_CONFIG_PATH))

    # Load YAML if exists
    data = {}
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        logger.info(f"Loaded config from {path}")
    else:
        logger.warning(f"Config file not found: {path}, using defaults")

    # Build config with env overrides
    config = Config(_config_path=path)

    # Server
    server_data = data.get("server", {})
    config.server = ServerConfig(
        host=_get_env("AGENTANET_HOST", server_data.get("host", "0.0.0.0")),
        port=int(_get_env("AGENTANET_PORT", server_data.get("port", 8080))),
        workers=server_data.get("workers", 1),
        reload=server_data.get("reload", False),
    )

    # Database
    db_data = data.get("database", {})
    config.database = DatabaseConfig(
        path=_get_env("AGENTANET_DB_PATH", db_data.get("path", "data/agentanet.db")),
    )

    # Security
    sec_data = data.get("security", {})
    token_data = sec_data.get("token", {})
    expire_days_raw = token_data.get("expire_days", None)
    config.security = SecurityConfig(
        secret_key=_get_env("AGENTANET_SECRET_KEY", _expand_env_vars(sec_data.get("secret_key"))),
        algorithm=sec_data.get("algorithm", "HS256"),
        session_expire_hours=sec_data.get("session_expire_hours", 24),
        token_max_per_user=token_data.get("max_per_user", 5),
        token_prefix=token_data.get("prefix", "agn_"),
        token_expire_days=int(expire_days_raw) if expire_days_raw is not None else None,
        cookie_secure=sec_data.get("cookie_secure", False),
        max_request_body_bytes=sec_data.get("max_request_body_bytes", 65536),
    )

    # Rate limit
    rl_data = data.get("rate_limit", {})
    config.rate_limit = RateLimitConfig(
        enabled=rl_data.get("enabled", True),
        window_seconds=rl_data.get("window_seconds", 60),
        max_requests=rl_data.get("max_requests", 60),
        auth_max_requests=rl_data.get("auth_max_requests", 10),
        marketplace_max_requests=rl_data.get("marketplace_max_requests", 120),
        admin_max_requests=rl_data.get("admin_max_requests", 30),
        cleanup_interval=rl_data.get("cleanup_interval", 300),
    )

    # CORS
    cors_data = data.get("cors", {})
    origins_env = _get_env("AGENTANET_CORS_ORIGINS")
    origins = origins_env.split(",") if origins_env else cors_data.get("allow_origins", ["*"])
    config.cors = CorsConfig(
        allow_origins=origins,
        allow_credentials=cors_data.get("allow_credentials", True),
        allow_methods=cors_data.get("allow_methods", ["*"]),
        allow_headers=cors_data.get("allow_headers", ["*"]),
    )
    # Browsers reject wildcard origin with credentials=true; also risky for auth cookies.
    if "*" in config.cors.allow_origins and config.cors.allow_credentials:
        logger.warning(
            "CORS allow_origins contains '*' with allow_credentials=true; forcing allow_credentials=false"
        )
        config.cors.allow_credentials = False

    # Health check
    hc_data = data.get("health_check", {})
    config.health_check = HealthCheckConfig(
        interval=hc_data.get("interval", 60),
        offline_threshold=hc_data.get("offline_threshold", 300),
        delist_threshold=hc_data.get("delist_threshold", 3600),
        purge_threshold=hc_data.get("purge_threshold", 2592000),
    )

    # Logging
    log_data = data.get("logging", {})
    config.logging = LoggingConfig(
        level=_get_env("AGENTANET_LOG_LEVEL", log_data.get("level", "INFO")),
        format=log_data.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
        file=log_data.get("file"),
    )

    # Admin mock users
    admin_data = data.get("admin", {})
    mock_users = []
    for u in admin_data.get("mock_users", []):
        mock_users.append(MockUser(
            username=u.get("username"),
            github_id=u.get("github_id"),
            email=u.get("email", ""),
            is_admin=u.get("is_admin", False),
        ))
    config.admin = AdminConfig(
        allow_mock_login=admin_data.get("allow_mock_login", False),
        mock_users=mock_users,
    )

    # GitHub OAuth
    gh_data = data.get("github", {})
    config.github = GithubConfig(
        enabled=gh_data.get("enabled", False),
        client_id=_expand_env_vars(gh_data.get("client_id")),
        client_secret=_expand_env_vars(gh_data.get("client_secret")),
        callback_url=gh_data.get("callback_url", "http://localhost:8080/auth/callback"),
    )
    if not config.admin.allow_mock_login and not config.github.enabled:
        logger.warning(
            "Both admin.allow_mock_login and github.enabled are false; "
            "no interactive login flow is currently available."
        )

    # Marketplace
    mp_data = data.get("marketplace", {})
    config.marketplace = MarketplaceConfig(
        expose_ip_publicly=mp_data.get("expose_ip_publicly", False),
        verify_on_register=mp_data.get("verify_on_register", False),
        verify_timeout=mp_data.get("verify_timeout", 3.0),
        default_page_size=mp_data.get("default_page_size", 50),
        max_page_size=mp_data.get("max_page_size", 100),
    )

    return config


# Global config instance (loaded lazily)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global config instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: Optional[Path] = None) -> Config:
    """Reload configuration from file."""
    global _config
    _config = load_config(config_path)
    return _config
