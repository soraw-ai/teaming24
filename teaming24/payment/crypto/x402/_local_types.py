"""Local x402 protocol types — self-contained, no external x402 package.

Defines all types needed for the payment flow. Replaces imports from the
external x402 package (a2a-x402 / google-agentic-commerce) which has a
different API than the Coinbase x402 PyPI package.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Protocol version
x402_VERSION = "1.0"

# Price: USD string ("$1.00"), float, or atomic units (int)
Price = str | float | int

# Supported networks (Base Sepolia, Base mainnet, mock)
SupportedNetworks = str


# ============================================================================
# EIP-3009 Authorization
# ============================================================================


@dataclass
class EIP3009Authorization:
    """EIP-3009 transferWithAuthorization parameters."""

    from_: str
    to: str
    value: str
    valid_after: str
    valid_before: str
    nonce: str


# ============================================================================
# Payment Payloads
# ============================================================================


@dataclass
class ExactPaymentPayload:
    """EIP-3009 exact payment payload with signature."""

    signature: str
    authorization: EIP3009Authorization


@dataclass
class PaymentPayload:
    """Top-level payment payload (wraps ExactPaymentPayload)."""

    x402_version: str
    scheme: str
    network: str
    payload: ExactPaymentPayload


# ============================================================================
# Payment Requirements
# ============================================================================


@dataclass
class PaymentRequirements:
    """Payment requirements for a protected resource (402 response)."""

    scheme: str
    network: SupportedNetworks
    asset: str
    pay_to: str
    max_amount_required: str
    resource: str
    description: str
    mime_type: str = "application/json"
    max_timeout_seconds: int = 600
    extra: dict[str, Any] | None = None


# ============================================================================
# Responses
# ============================================================================


@dataclass
class VerifyResponse:
    """Result of payment verification."""

    is_valid: bool
    payer: str | None = None
    invalid_reason: str | None = None


@dataclass
class SettleResponse:
    """Result of payment settlement on-chain."""

    success: bool
    network: str | None = None
    transaction: str | None = None
    payer: str | None = None
    error_reason: str | None = None


@dataclass
class x402PaymentRequiredResponse:
    """Standard 402 response format."""

    x402_version: str
    accepts: list[PaymentRequirements]
    error: str | None = None


# ============================================================================
# Facilitator (abstract interface)
# ============================================================================


@dataclass
class FacilitatorConfig:
    """Configuration for facilitator client."""

    url: str | None = None
    timeout: int = 30
    max_retries: int = 3


class FacilitatorClient(ABC):
    """Abstract facilitator for payment verification and settlement."""

    @abstractmethod
    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        """Verify payment signature and requirements."""
        ...

    @abstractmethod
    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        """Settle payment on blockchain."""
        ...
