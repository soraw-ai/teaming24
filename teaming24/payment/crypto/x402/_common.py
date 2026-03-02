"""Common x402 utilities — price conversion, etc.

Replaces x402.common module.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ._chains import (
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    USDC_BASE_MAINNET,
    USDC_BASE_SEPOLIA,
    USDC_DECIMALS,
)
from ._local_types import Price

# EIP-712 domain for EIP-3009 (ETH token)
EIP712_DOMAIN_BASE_SEPOLIA = {
    "name": "Ether",
    "version": "2",
    "chainId": CHAIN_ID_BASE_SEPOLIA,
    "verifyingContract": USDC_BASE_SEPOLIA,
}
EIP712_DOMAIN_BASE_MAINNET = {
    "name": "Ether",
    "version": "2",
    "chainId": CHAIN_ID_BASE_MAINNET,
    "verifyingContract": USDC_BASE_MAINNET,
}


def process_price_to_atomic_amount(
    price: Price, network: str
) -> tuple[str, str, dict[str, Any]]:
    """Convert price to atomic units and return asset + EIP-712 domain.

    Args:
        price: USD string ("$0.001"), float (0.001), or atomic units (int)
        network: "mock", "base-sepolia", or "base"

    Returns:
        (max_amount_str, asset_address, eip712_domain)
    """
    if network == "mock" or not network:
        asset = "0x0000000000000000000000000000000000000000"
        domain: dict[str, Any] = {}
        if isinstance(price, int) and price >= 0:
            return (str(price), asset, domain)
        amount = _parse_usd_to_float(price)
        atomic = int(amount * (10**USDC_DECIMALS))
        return (str(atomic), asset, domain)

    if network == "base-sepolia":
        asset = USDC_BASE_SEPOLIA
        domain = EIP712_DOMAIN_BASE_SEPOLIA
    elif network == "base":
        asset = USDC_BASE_MAINNET
        domain = EIP712_DOMAIN_BASE_MAINNET
    else:
        asset = USDC_BASE_SEPOLIA
        domain = EIP712_DOMAIN_BASE_SEPOLIA

    if isinstance(price, int) and price >= 0:
        return (str(price), asset, domain)
    amount = _parse_usd_to_float(price)
    atomic = int(amount * (10**USDC_DECIMALS))
    return (str(atomic), asset, domain)


def _parse_usd_to_float(price: Price) -> float:
    """Parse price to float (handles $1.00, 1.0, etc.)."""
    if isinstance(price, (int, float)):
        return float(price)
    if isinstance(price, str):
        s = price.strip()
        if s.startswith("$"):
            s = s[1:].strip()
        return float(Decimal(s))
    raise ValueError(f"Invalid price: {price}")
