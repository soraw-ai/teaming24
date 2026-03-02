"""Config loader helpers extracted from config.__init__."""

from __future__ import annotations

from typing import Any


def apply_env_overrides(
    data: dict[str, Any],
    *,
    environ: dict[str, str],
    logger: Any,
) -> dict[str, Any]:
    """Apply environment variable overrides to a config dictionary."""
    if "system" not in data:
        data["system"] = {}
    if "server" not in data["system"]:
        data["system"]["server"] = {}
    if "logging" not in data["system"]:
        data["system"]["logging"] = {}
    if "database" not in data["system"]:
        data["system"]["database"] = {}
    if "api" not in data["system"]:
        data["system"]["api"] = {}
    if "network" not in data:
        data["network"] = {}
    if "local_node" not in data["network"]:
        data["network"]["local_node"] = {}

    if environ.get("TEAMING24_HOST"):
        data["system"]["server"]["host"] = environ["TEAMING24_HOST"]

    if environ.get("TEAMING24_PORT"):
        try:
            port = int(environ["TEAMING24_PORT"])
        except ValueError:
            logger.error("TEAMING24_PORT must be a valid integer, got: %r", environ["TEAMING24_PORT"])
            port = None
    else:
        port = None
    if port is not None:
        data["system"]["server"]["port"] = port
        data["network"]["local_node"]["port"] = port
        host = data["system"]["server"].get("host", "localhost")
        if host == "0.0.0.0":
            host = "localhost"
        data["system"]["api"]["base_url"] = f"http://{host}:{port}"

    if environ.get("TEAMING24_NODE_HOST"):
        data["network"]["local_node"]["host"] = environ["TEAMING24_NODE_HOST"]
    if environ.get("TEAMING24_WALLET_ADDRESS"):
        data["network"]["local_node"]["wallet_address"] = environ["TEAMING24_WALLET_ADDRESS"]
    if environ.get("TEAMING24_NODE_NAME"):
        data["network"]["local_node"]["name"] = environ["TEAMING24_NODE_NAME"]

    if environ.get("TEAMING24_LOG_LEVEL"):
        data["system"]["logging"]["level"] = environ["TEAMING24_LOG_LEVEL"]

    if environ.get("TEAMING24_DB_PATH"):
        data["system"]["database"]["path"] = environ["TEAMING24_DB_PATH"]

    if environ.get("TEAMING24_OUTPUT_DIR"):
        if "output" not in data:
            data["output"] = {}
        data["output"]["base_dir"] = environ["TEAMING24_OUTPUT_DIR"]

    if environ.get("TEAMING24_CORS_ORIGINS"):
        if "system" not in data:
            data["system"] = {}
        if "cors" not in data["system"]:
            data["system"]["cors"] = {}
        extra = [o.strip() for o in environ["TEAMING24_CORS_ORIGINS"].split(",") if o.strip()]
        if extra:
            existing = data["system"]["cors"].get("allow_origins") or []
            if isinstance(existing, list):
                data["system"]["cors"]["allow_origins"] = list(existing) + extra
            else:
                data["system"]["cors"]["allow_origins"] = extra

    return data
