"""x402 Protocol Operations: Verification, Settlement, and Utilities.

Core protocol functions for payment verification, settlement, and helper utilities.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import Any, TypeVar, cast

from teaming24.utils.logger import get_logger

from ._chains import get_chain_id, get_token_decimals, get_token_name
from .facilitator import get_facilitator
from .merchant import require_payment
from .types import (
    ExactPaymentPayload,
    FacilitatorClient,
    PaymentPayload,
    PaymentRequirements,
    PaymentSettlementError,
    PaymentValidationError,
    Price,
    SettleResponse,
    VerifyResponse,
    get_config,
)

logger = get_logger(__name__)


# ============================================================================
# Verification & Settlement
# ============================================================================


async def verify_payment(
    payload: PaymentPayload,
    requirements: PaymentRequirements,
    facilitator: FacilitatorClient | None = None,
) -> VerifyResponse:
    """Verify payment signature and requirements.

    Uses the appropriate facilitator based on current mode:
    - MOCK: Always returns valid (configurable)
    - TESTNET: Real verification
    - MAINNET: Real verification

    Args:
        payload: Signed payment payload from client
        requirements: Original payment requirements
        facilitator: Optional custom facilitator client

    Returns:
        VerifyResponse with validation result
    """
    cfg = get_config()
    logger.debug("Verifying payment", extra={
        "resource": requirements.resource,
        "mode": cfg.mode.value,
    })

    client = facilitator or get_facilitator()
    result = await client.verify(payload, requirements)

    if result.is_valid:
        logger.info("Payment verified", extra={"payer": result.payer})
    else:
        logger.warning("Payment verification failed", extra={"reason": result.invalid_reason})

    return result


async def settle_payment(
    payload: PaymentPayload,
    requirements: PaymentRequirements,
    facilitator: FacilitatorClient | None = None,
) -> SettleResponse:
    """Settle payment on blockchain.

    Uses the appropriate facilitator based on current mode:
    - MOCK: Returns fake transaction hash
    - TESTNET: Real transaction on Base Sepolia
    - MAINNET: Real transaction on Base

    Args:
        payload: Verified payment payload
        requirements: Payment requirements
        facilitator: Optional custom facilitator client

    Returns:
        SettleResponse with transaction details
    """
    cfg = get_config()
    logger.debug("Settling payment", extra={
        "network": requirements.network,
        "mode": cfg.mode.value,
    })

    client = facilitator or get_facilitator()
    response = await client.settle(payload, requirements)

    result = SettleResponse(
        success=response.success,
        transaction=response.transaction,
        network=response.network or requirements.network,
        payer=response.payer,
        error_reason=response.error_reason,
    )

    if result.success:
        logger.info("Payment settled", extra={
            "tx": result.transaction,
            "network": result.network,
        })
    else:
        logger.error("Settlement failed", extra={"reason": result.error_reason})

    return result


async def process_and_settle(
    payload: PaymentPayload,
    requirements: PaymentRequirements,
    facilitator: FacilitatorClient | None = None,
) -> SettleResponse:
    """Verify and settle payment in one operation.

    Args:
        payload: Signed payment payload
        requirements: Payment requirements
        facilitator: Optional custom facilitator

    Returns:
        SettleResponse with transaction details

    Raises:
        PaymentValidationError: If verification fails
        PaymentSettlementError: If settlement fails
    """
    client = facilitator or get_facilitator()

    # Verify first
    verify_result = await client.verify(payload, requirements)
    if not verify_result.is_valid:
        logger.error("Payment verification failed", extra={"reason": verify_result.invalid_reason})
        raise PaymentValidationError(f"Payment verification failed: {verify_result.invalid_reason}")

    # Then settle
    settle_result = await settle_payment(payload, requirements, client)
    if not settle_result.success:
        logger.error("Settlement failed", extra={"reason": settle_result.error_reason})
        raise PaymentSettlementError(f"Settlement failed: {settle_result.error_reason}")

    return settle_result


# ============================================================================
# Utilities
# ============================================================================


def format_amount(requirements: PaymentRequirements) -> str:
    """Format payment amount for human-readable display.

    Args:
        requirements: PaymentRequirements to format

    Returns:
        Formatted string like "1.50 ETH"
    """
    atomic = requirements.max_amount_required or "0"
    try:
        amount = Decimal(atomic)
        chain_id = get_chain_id(requirements.network)
        decimals = get_token_decimals(chain_id, requirements.asset)
        human = amount / (Decimal(10) ** decimals)
        token = get_token_name(chain_id, requirements.asset)
        formatted = format(human, "f").rstrip("0").rstrip(".")
        return f"{formatted} {token}"
    except Exception as e:
        logger.warning(f"[x402] format_payment_amount failed for '{atomic}': {e}")
        return f"{atomic} (atomic units)"


def extract_payer_address(payload: PaymentPayload) -> str | None:
    """Extract payer address from a payment payload.

    Args:
        payload: PaymentPayload to extract from

    Returns:
        Payer's Ethereum address or None
    """
    if isinstance(payload.payload, ExactPaymentPayload):
        return payload.payload.authorization.from_
    return None


# ============================================================================
# Decorator for Paid Services
# ============================================================================


F = TypeVar("F", bound=Callable[..., Any])


def paid_service(
    price: Price,
    pay_to: str,
    resource: str | None = None,
    description: str = "Payment required",
    **kwargs,
) -> Callable[[F], F]:
    """Decorator to require payment before function execution.

    The decorated function will raise PaymentRequiredError on first call.
    Callers must catch this, process payment, and retry with payment header.

    Args:
        price: Payment amount
        pay_to: Recipient address
        resource: Resource identifier (default: function name)
        description: Human-readable description
        **kwargs: Additional args for create_requirements

    Example:
        @paid_service(
            price="$0.10",
            pay_to="0x742d35Cc6634C0532925a3b844Bc9e7595f0Ab13",
            description="Premium AI generation"
        )
        async def generate_premium_content(prompt: str) -> str:
            return await ai_service.generate(prompt, quality="high")
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kw):
            effective_resource = resource or f"/{func.__name__}"
            raise require_payment(
                price=price,
                pay_to=pay_to,
                resource=effective_resource,
                message=description,
                **kwargs,
            )
        return cast(F, wrapper)
    return decorator


__all__ = [
    # Verification & Settlement
    "verify_payment",
    "settle_payment",
    "process_and_settle",
    # Utilities
    "format_amount",
    "extract_payer_address",
    # Decorator
    "paid_service",
]
