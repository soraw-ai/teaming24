"""Server-Side Payment Requirements for x402 Protocol.

Functions for merchants/servers to create and manage payment requirements
for protected resources.
"""

from __future__ import annotations

from typing import cast

from teaming24.utils.logger import get_logger

from ._common import process_price_to_atomic_amount
from .types import (
    PaymentRequiredError,
    PaymentRequirements,
    Price,
    SupportedNetworks,
    get_config,
)

logger = get_logger(__name__)


def create_requirements(
    price: Price,
    pay_to: str,
    resource: str,
    network: str | None = None,
    description: str = "Payment required",
    scheme: str = "exact",
    timeout_seconds: int | None = None,
) -> PaymentRequirements:
    """Create payment requirements for a protected resource.

    Args:
        price: Amount in USD string ("$1.00"), float (1.0), or atomic units
        pay_to: Ethereum address to receive payment
        resource: Resource identifier (e.g., "/api/v1/generate")
        network: Blockchain network (default: from config)
        description: Human-readable description
        scheme: Payment scheme ("exact" for EIP-3009)
        timeout_seconds: Payment validity duration

    Returns:
        PaymentRequirements for inclusion in 402 response

    Example:
        requirements = create_requirements(
            price="$0.50",
            pay_to="0x742d35Cc6634C0532925a3b844Bc9e7595f0Ab13",
            resource="/api/v1/generate-image",
            description="Image generation fee"
        )
    """
    config = get_config()
    net = network or config.network
    timeout = timeout_seconds or config.default_timeout_seconds

    max_amount, asset_address, eip712_domain = process_price_to_atomic_amount(price, net)

    return PaymentRequirements(
        scheme=scheme,
        network=cast(SupportedNetworks, net),
        asset=asset_address,
        pay_to=pay_to,
        max_amount_required=max_amount,
        resource=resource,
        description=description,
        mime_type="application/json",
        max_timeout_seconds=timeout,
        extra=eip712_domain,
    )


def require_payment(
    price: Price,
    pay_to: str,
    resource: str,
    message: str = "Payment required for this resource",
    **kwargs,
) -> PaymentRequiredError:
    """Create a payment required exception for immediate raising.

    Args:
        price: Payment amount
        pay_to: Recipient address
        resource: Protected resource identifier
        message: Error message
        **kwargs: Additional args for create_requirements

    Returns:
        PaymentRequiredError ready to raise

    Example:
        if not user.has_credits():
            raise require_payment(
                price="$2.00",
                pay_to=MERCHANT_ADDRESS,
                resource="/premium-api",
                message="Premium feature requires payment"
            )
    """
    requirements = create_requirements(price=price, pay_to=pay_to, resource=resource, **kwargs)
    logger.info("Payment required", extra={"resource": resource, "price": str(price)})
    return PaymentRequiredError(message, requirements)


def require_payment_choice(
    options: list[PaymentRequirements],
    message: str = "Multiple payment options available",
) -> PaymentRequiredError:
    """Create a payment required exception with multiple payment options.

    Args:
        options: List of PaymentRequirements to choose from
        message: Error message

    Returns:
        PaymentRequiredError with multiple payment options

    Example:
        basic = create_requirements(price="$1.00", pay_to=ADDR, resource="/basic")
        premium = create_requirements(price="$5.00", pay_to=ADDR, resource="/premium")
        raise require_payment_choice([basic, premium], "Choose your tier")
    """
    return PaymentRequiredError(message, options)


def create_tiered_options(
    base_price: Price,
    pay_to: str,
    resource: str,
    tiers: list[dict] | None = None,
    **kwargs,
) -> list[PaymentRequirements]:
    """Create multiple payment options with different tiers.

    Args:
        base_price: Base payment amount (e.g., "$1.00")
        pay_to: Recipient address
        resource: Base resource identifier
        tiers: List of tier configs with 'multiplier', 'suffix', 'description'
        **kwargs: Additional args for create_requirements

    Returns:
        List of PaymentRequirements for different service tiers

    Example:
        options = create_tiered_options(
            base_price="$1.00",
            pay_to="0x742d35...",
            resource="/generate",
            tiers=[
                {"multiplier": 1, "suffix": "basic", "description": "Basic quality"},
                {"multiplier": 3, "suffix": "premium", "description": "Premium quality"},
            ]
        )
        raise require_payment_choice(options)
    """
    if tiers is None:
        tiers = [
            {"multiplier": 1, "suffix": "basic", "description": "Basic service"},
            {"multiplier": 2, "suffix": "premium", "description": "Premium service"},
        ]

    options = []
    for tier in tiers:
        multiplier = tier.get("multiplier", 1)
        suffix = tier.get("suffix", "")
        description = tier.get("description", f"Tier: {suffix}")

        # Calculate tier price
        if isinstance(base_price, str) and base_price.startswith("$"):
            base_amount = float(base_price[1:])
            tier_price: Price = f"${base_amount * multiplier:.2f}"
        elif isinstance(base_price, (int, float)):
            tier_price = base_price * multiplier
        else:
            tier_price = base_price

        tier_resource = f"{resource}/{suffix}" if suffix else resource

        options.append(create_requirements(
            price=tier_price,
            pay_to=pay_to,
            resource=tier_resource,
            description=description,
            **kwargs,
        ))

    return options


__all__ = [
    "create_requirements",
    "require_payment",
    "require_payment_choice",
    "create_tiered_options",
]
