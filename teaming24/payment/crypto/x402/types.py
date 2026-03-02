"""x402 Protocol Types, Errors, and Configuration.

Core type definitions for Agentic Node (AN) HTTP 402 payments.

Supports three network modes:
- MOCK: No real blockchain calls, for development/testing (default)
- TESTNET: Base Sepolia testnet with real transactions
- MAINNET: Base mainnet for production
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from teaming24.utils.logger import get_logger

# Local types (no external x402 package dependency)
from ._local_types import (
    EIP3009Authorization,
    ExactPaymentPayload,
    FacilitatorClient,
    FacilitatorConfig,
    PaymentPayload,
    PaymentRequirements,
    Price,
    SettleResponse,
    SupportedNetworks,
    VerifyResponse,
    x402_VERSION,
    x402PaymentRequiredResponse,
)

logger = get_logger(__name__)


# ============================================================================
# Network Mode
# ============================================================================


class NetworkMode(str, Enum):
    """Network mode for x402 payments."""
    MOCK = "mock"        # No real blockchain calls (default for development)
    TESTNET = "testnet"  # Base Sepolia testnet
    MAINNET = "mainnet"  # Base mainnet (production)


# Network presets
NETWORK_PRESETS = {
    NetworkMode.MOCK: {
        "network": "mock",
        "rpc_url": None,
        "facilitator_url": None,
        "asset": "0x0000000000000000000000000000000000000000",
    },
    NetworkMode.TESTNET: {
        "network": "base-sepolia",
        "rpc_url": "https://sepolia.base.org",
        "facilitator_url": "https://x402.org/facilitator",
        "asset": "0x4182528b6660B9c0875c6e94260A2E425F00797f",  # ETH on Base Sepolia
    },
    NetworkMode.MAINNET: {
        "network": "base",
        "rpc_url": "https://mainnet.base.org",
        "facilitator_url": "https://x402.org/facilitator",
        "asset": "0x4182528b6660B9c0875c6e94260A2E425F00797f",  # ETH token on Base
    },
}


# ============================================================================
# Error Types
# ============================================================================


class X402Error(Exception):
    """Base error for x402 protocol operations."""
    pass


class PaymentValidationError(X402Error):
    """Payment signature or requirements validation failed."""
    pass


class PaymentSettlementError(X402Error):
    """Payment settlement on blockchain failed."""
    pass


class PaymentRequiredError(X402Error):
    """Exception raised when payment is required for a resource.

    This exception carries PaymentRequirements that clients can use to
    construct and submit a payment.

    Example:
        raise PaymentRequiredError(
            "Premium feature requires payment",
            requirements
        )
    """

    def __init__(
        self,
        message: str,
        requirements: PaymentRequirements | list[PaymentRequirements],
    ):
        super().__init__(message)
        self.requirements = requirements if isinstance(requirements, list) else [requirements]

    def to_402_response(self) -> x402PaymentRequiredResponse:
        """Convert to standard x402 402 response format."""
        return x402PaymentRequiredResponse(
            x402_version=x402_VERSION,
            accepts=self.requirements,
            error=str(self),
        )


# ============================================================================
# Configuration
# ============================================================================


# Default config file path
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_DEFAULT_CONFIG_PATH = _CONFIG_DIR / "x402.yaml"


@dataclass
class X402Config:
    """Configuration for x402 payment operations.

    Modes:
        MOCK: No real blockchain calls, always succeeds (default)
        TESTNET: Base Sepolia testnet with real transactions
        MAINNET: Base mainnet for production
    """

    # Mode selection (default: MOCK for safety)
    mode: NetworkMode = NetworkMode.MOCK

    # Network settings (auto-configured from mode if not specified)
    network: str | None = None
    rpc_url: str | None = None

    # Facilitator settings
    facilitator_url: str | None = None
    facilitator_timeout: int = 30
    facilitator_max_retries: int = 3

    # Payment settings
    scheme: str = "exact"
    default_timeout_seconds: int = 600
    default_asset: str | None = None

    # Merchant settings
    merchant_address: str | None = None
    default_description: str = "Payment required for this service"

    # Wallet settings
    valid_hours: float = 1.0

    # Mock settings
    mock_always_valid: bool = True
    mock_always_settled: bool = True

    def __post_init__(self):
        """Apply mode presets if values not explicitly set."""
        preset = NETWORK_PRESETS.get(self.mode, NETWORK_PRESETS[NetworkMode.MOCK])

        if self.network is None:
            self.network = preset["network"]
        if self.rpc_url is None and preset["rpc_url"]:
            self.rpc_url = preset["rpc_url"]
        if self.facilitator_url is None and preset["facilitator_url"]:
            self.facilitator_url = preset["facilitator_url"]
        if self.default_asset is None and preset["asset"]:
            self.default_asset = preset["asset"]

    @property
    def is_mock(self) -> bool:
        """Check if running in mock mode."""
        return self.mode == NetworkMode.MOCK

    @property
    def is_testnet(self) -> bool:
        """Check if running on testnet."""
        return self.mode == NetworkMode.TESTNET

    @property
    def is_mainnet(self) -> bool:
        """Check if running on mainnet."""
        return self.mode == NetworkMode.MAINNET

    def get_rpc_url(self) -> str:
        """Get RPC URL from config or environment."""
        if self.is_mock:
            return "mock://localhost"
        return self.rpc_url or os.getenv("TEAMING24_RPC_URL", "https://sepolia.base.org")

    def get_merchant_address(self) -> str | None:
        """Get merchant address from config or environment."""
        return self.merchant_address or os.getenv("TEAMING24_MERCHANT_ADDRESS")

    def get_facilitator_url(self) -> str | None:
        """Get facilitator URL from config or environment."""
        if self.is_mock:
            return None  # Mock mode doesn't use facilitator
        return (
            self.facilitator_url
            or os.getenv("TEAMING24_FACILITATOR_URL")
            or "https://x402.org/facilitator"
        )

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> X402Config:
        """Load configuration from YAML file.

        Args:
            path: Path to YAML file (default: teaming24/config/x402.yaml)

        Returns:
            X402Config instance
        """
        config_path = path or _DEFAULT_CONFIG_PATH

        if not config_path.exists():
            logger.debug("No x402.yaml found, using default MOCK mode")
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        # Extract nested config sections
        network_cfg = data.get("network", {})
        payment_cfg = data.get("payment", {})
        facilitator_cfg = data.get("facilitator", {})
        merchant_cfg = data.get("merchant", {})
        wallet_cfg = data.get("wallet", {})
        mock_cfg = data.get("mock", {})

        # Parse mode from config
        mode_str = data.get("mode", os.getenv("X402_MODE", "mock")).lower()
        try:
            mode = NetworkMode(mode_str)
        except ValueError:
            logger.warning(f"Unknown x402 mode '{mode_str}', defaulting to MOCK")
            mode = NetworkMode.MOCK

        return cls(
            # Mode
            mode=mode,
            # Network
            network=network_cfg.get("name"),
            rpc_url=network_cfg.get("rpc_url"),
            # Facilitator
            facilitator_url=facilitator_cfg.get("url"),
            facilitator_timeout=facilitator_cfg.get("timeout", 30),
            facilitator_max_retries=facilitator_cfg.get("max_retries", 3),
            # Payment
            scheme=payment_cfg.get("scheme", "exact"),
            default_timeout_seconds=payment_cfg.get("timeout_seconds", 600),
            default_asset=payment_cfg.get("default_asset"),
            # Merchant
            merchant_address=merchant_cfg.get("pay_to_address"),
            default_description=merchant_cfg.get("default_description", "Payment required for this service"),
            # Wallet
            valid_hours=wallet_cfg.get("valid_hours", 1.0),
            # Mock
            mock_always_valid=mock_cfg.get("always_valid", True),
            mock_always_settled=mock_cfg.get("always_settled", True),
        )


# Global default configuration (lazy loaded)
_default_config: X402Config | None = None


def get_config() -> X402Config:
    """Get current global configuration (lazy loads from YAML)."""
    global _default_config
    if _default_config is None:
        _default_config = X402Config.from_yaml()
    return _default_config


def configure(
    mode: NetworkMode | None = None,
    network: str | None = None,
    rpc_url: str | None = None,
    facilitator_url: str | None = None,
    config_path: Path | None = None,
) -> None:
    """Configure global x402 settings.

    Args:
        mode: Network mode (MOCK, TESTNET, MAINNET)
        network: Blockchain network (e.g., "base-sepolia", "base")
        rpc_url: RPC endpoint URL
        facilitator_url: Payment facilitator URL
        config_path: Path to YAML config file to load

    Example:
        # Development (default - no real transactions)
        configure(mode=NetworkMode.MOCK)

        # Testing on testnet
        configure(mode=NetworkMode.TESTNET)

        # Production
        configure(mode=NetworkMode.MAINNET)
    """
    global _default_config

    # Load from YAML if path provided, otherwise get/create config
    if config_path:
        _default_config = X402Config.from_yaml(config_path)
    elif _default_config is None:
        _default_config = X402Config.from_yaml()

    # Override with explicit parameters
    if mode is not None:
        _default_config.mode = mode
        # Re-apply presets for new mode
        _default_config.__post_init__()
    if network:
        _default_config.network = network
    if rpc_url:
        _default_config.rpc_url = rpc_url
    if facilitator_url:
        _default_config.facilitator_url = facilitator_url

    logger.info("x402 configured", extra={"mode": _default_config.mode.value, "network": _default_config.network})


# ============================================================================
# Re-exports from x402 package
# ============================================================================


__all__ = [
    # Network Mode
    "NetworkMode",
    "NETWORK_PRESETS",
    # Errors
    "X402Error",
    "PaymentValidationError",
    "PaymentSettlementError",
    "PaymentRequiredError",
    # Configuration
    "X402Config",
    "get_config",
    "configure",
    # Re-exports from x402
    "x402_VERSION",
    "PaymentRequirements",
    "PaymentPayload",
    "ExactPaymentPayload",
    "EIP3009Authorization",
    "x402PaymentRequiredResponse",
    "VerifyResponse",
    "SettleResponse",
    "SupportedNetworks",
    "Price",
    "FacilitatorClient",
    "FacilitatorConfig",
]
