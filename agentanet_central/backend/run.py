#!/usr/bin/env python3
"""
Run the AgentaNet Central Service backend.

Usage:
    uv run python -m backend.run [--host HOST] [--port PORT] [--config CONFIG]

Or via entry point:
    uv run agentanet-central [--host HOST] [--port PORT]
"""

import argparse
import sys
from pathlib import Path

import uvicorn


def main():
    """Main entry point for AgentaNet Central Service."""
    parser = argparse.ArgumentParser(description="AgentaNet Central Service")
    parser.add_argument("--host", help="Host to bind to (overrides config)")
    parser.add_argument("--port", type=int, help="Port to bind to (overrides config)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--config", help="Path to config file")
    args = parser.parse_args()

    # Add parent directory to path for imports when running directly
    backend_dir = Path(__file__).parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.config import get_config, reload_config

    # Load config
    if args.config:
        reload_config(Path(args.config))

    config = get_config()

    # CLI args override config
    host = args.host or config.server.host
    port = args.port or config.server.port
    reload_enabled = args.reload or config.server.reload

    # Get mock users for display
    mock_users = ", ".join([u.username for u in config.admin.mock_users]) or "admin, demo"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           AgentaNet Central Service                          ║
╠══════════════════════════════════════════════════════════════╣
║  API:        http://{host}:{port:<5}                              ║
║  Docs:       http://{host}:{port:<5}/docs                         ║
║  Health:     http://{host}:{port:<5}/health                       ║
╠══════════════════════════════════════════════════════════════╣
║  Mock Users: {mock_users:<47}║
║  Config:     {str(config._config_path)[:47]:<47}║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        ws="wsproto",
    )


if __name__ == "__main__":
    main()
