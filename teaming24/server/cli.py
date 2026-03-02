#!/usr/bin/env python3
# ruff: noqa: E402
"""
Command Line Interface for Teaming24.

Handles argument parsing and server startup.
"""
import argparse
import sys
from pathlib import Path

# Load .env file before importing anything else
from dotenv import load_dotenv

# Find .env file in project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
    print(f"Loaded environment from: {_ENV_FILE}")
else:
    # Also check current directory
    if Path(".env").exists():
        load_dotenv()
        print("Loaded environment from: .env")

from teaming24.config import Config, load_config
from teaming24.utils import LogConfig
from teaming24.utils import setup_logging as init_logging


def setup_logging(config: Config) -> None:
    """
    Setup logging based on configuration.

    Args:
        config: Application configuration
    """
    log_config = LogConfig(
        level=config.logging.level,
        format="text",  # Use colored text for CLI
        file=config.logging.file,
        console=True,
    )
    init_logging(config=log_config)


def print_banner(host: str, port: int, config: Config, config_path: str | None = None) -> None:
    """
    Print startup banner.

    Args:
        host: Server host
        port: Server port
        config: Application configuration
        config_path: Path to config file used
    """
    from pathlib import Path

    config_display = config_path or "teaming24/config/teaming24.yaml"

    # Check if frontend is built
    gui_dist = Path(__file__).parent.parent / "gui" / "dist"
    frontend_built = gui_dist.exists() and (gui_dist / "index.html").exists()

    if frontend_built:
        frontend_info = f"http://{host}:{port}  (built-in)"
    else:
        frontend_info = "Build: cd teaming24/gui && npm run build"

    # ASCII art for TEAMING 24 (block style)
    logo = [
        "████████╗███████╗ █████╗ ███╗   ███╗██╗███╗   ██╗ ██████╗   ██████╗ ██╗  ██╗",
        "╚══██╔══╝██╔════╝██╔══██╗████╗ ████║██║████╗  ██║██╔════╝   ╚════██╗██║  ██║",
        "   ██║   █████╗  ███████║██╔████╔██║██║██╔██╗ ██║██║  ███╗   █████╔╝███████║",
        "   ██║   ██╔══╝  ██╔══██║██║╚██╔╝██║██║██║╚██╗██║██║   ██║  ██╔═══╝ ╚════██║",
        "   ██║   ███████╗██║  ██║██║ ╚═╝ ██║██║██║ ╚████║╚██████╔╝  ███████╗     ██║",
        "   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝ ╚═════╝   ╚══════╝     ╚═╝",
    ]

    # Info lines
    info_lines = [
        "Multi-agent Collaboration Platform",
        "",
        f"Server:    http://{host}:{port}",
        f"API Docs:  http://{host}:{port}/docs",
        f"Frontend:  {frontend_info}",
        "",
        f"Config:    {config_display}",
    ]

    # Calculate dynamic width based on longest content + padding
    logo_width = max(len(line) for line in logo)
    info_width = max(len(line) for line in info_lines)
    # Inner width = max of logo or info, plus 6 for "║  " prefix and "  ║" suffix
    inner_width = max(logo_width, info_width) + 6

    def row(content: str = "") -> str:
        """Create a row with exact padding."""
        return f"║  {content:<{inner_width - 4}}  ║"

    def logo_row(content: str) -> str:
        """Create a logo row (centered)."""
        padding = (inner_width - 4 - len(content)) // 2
        centered = " " * padding + content
        return f"║  {centered:<{inner_width - 4}}  ║"

    # Build banner
    lines = []
    lines.append("╔" + "═" * inner_width + "╗")
    lines.append(row())

    for logo_line in logo:
        lines.append(logo_row(logo_line))

    lines.append(row())
    lines.append("╠" + "═" * inner_width + "╣")

    for info_line in info_lines:
        lines.append(row(info_line))

    lines.append("╚" + "═" * inner_width + "╝")

    print("\n" + "\n".join(lines))


def run_server(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
    workers: int = 1,
    config_path: str | None = None,
) -> None:
    """
    Run the Teaming24 API server.

    Args:
        host: Server host (overrides config)
        port: Server port (overrides config)
        reload: Enable auto-reload
        workers: Number of worker processes
        config_path: Path to config file
    """
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is not installed.")
        print("Please run: uv sync  (or: pip install -r requirements.txt)")
        sys.exit(1)

    # Load configuration
    config = load_config(config_path)

    # Setup logging
    setup_logging(config)

    # Apply defaults from config
    host = host if host is not None else config.server.host
    port = port if port is not None else config.server.port

    # Print banner
    print_banner(host, port, config, config_path)

    # Run server
    uvicorn.run(
        "teaming24.api.server:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
        ws="wsproto",
    )


def create_parser() -> argparse.ArgumentParser:
    """
    Create argument parser.

    Returns:
        Configured argument parser
    """
    parser = argparse.ArgumentParser(
        description="Teaming24 - Multi-agent collaboration platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Start with default config
  python main.py --reload           # Start with auto-reload
  python main.py --port 9000        # Start on port 9000
  python main.py --config my.yaml   # Use custom config file
        """
    )

    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to config file (default: teaming24/config/teaming24.yaml)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind the server to (overrides config)"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        help="Port to bind the server to (overrides config)"
    )
    parser.add_argument(
        "--reload", "-r",
        action="store_true",
        default=False,
        help="Enable auto-reload for development"
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)"
    )

    return parser


def main() -> None:
    """Main entry point for CLI."""
    parser = create_parser()
    args = parser.parse_args()

    try:
        run_server(
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=args.workers,
            config_path=args.config,
        )
    except KeyboardInterrupt:
        print("\nShutting down server...")
        sys.exit(0)


if __name__ == "__main__":
    main()
