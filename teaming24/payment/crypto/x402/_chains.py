"""Chain and token utilities for Base Sepolia and Base mainnet.

Replaces x402.chains module — provides chain ID, token decimals, and token name
for ETH on Base networks.
"""

from __future__ import annotations

# Chain IDs
CHAIN_ID_BASE_SEPOLIA = 84532
CHAIN_ID_BASE_MAINNET = 8453

# ETH token contract addresses
TOKEN_BASE_SEPOLIA = "0x4182528b6660B9c0875c6e94260A2E425F00797f"
TOKEN_BASE_MAINNET = "0x4182528b6660B9c0875c6e94260A2E425F00797f"

# Token has 6 decimals
TOKEN_DECIMALS = 6

# Legacy aliases (keep for any internal imports)
USDC_BASE_SEPOLIA = TOKEN_BASE_SEPOLIA
USDC_BASE_MAINNET = TOKEN_BASE_MAINNET
USDC_DECIMALS = TOKEN_DECIMALS


def get_chain_id(network: str | None) -> int:
    """Get chain ID for a network name."""
    if not network or network == "mock":
        return 0
    if network == "base-sepolia":
        return CHAIN_ID_BASE_SEPOLIA
    if network == "base":
        return CHAIN_ID_BASE_MAINNET
    return 0


def get_token_decimals(chain_id: int, asset: str | None) -> int:
    """Get token decimals (ETH = 6)."""
    return TOKEN_DECIMALS


def get_token_name(chain_id: int, asset: str | None) -> str:
    """Get token symbol for display."""
    return "ETH"
