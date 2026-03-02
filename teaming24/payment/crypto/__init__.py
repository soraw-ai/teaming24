"""Crypto Payment Module for Agentic Node (AN) Payments.

This module provides blockchain-based payment protocols for AN-to-AN transactions.

Supported Protocols:
    - x402: HTTP 402 Payment Required protocol using EIP-3009

Usage:
    # Import specific protocol
    from teaming24.payment.crypto.x402 import require_payment, sign_payment

    # Or import protocol module
    from teaming24.payment.crypto import x402
    x402.require_payment(...)
"""

# Import x402 protocol module for convenient access
from . import x402

# Re-export commonly used x402 items for convenience
# Users can also use: from teaming24.payment.crypto.x402 import ...
from .x402 import (
    FacilitatorClient,
    PaymentPayload,
    PaymentRequiredError,
    # Types
    PaymentRequirements,
    PaymentSettlementError,
    PaymentValidationError,
    SettleResponse,
    VerifyResponse,
    # Configuration
    X402Config,
    # Errors
    X402Error,
    configure,
    # Server-side (merchant)
    create_requirements,
    create_tiered_options,
    extract_payer_address,
    # Utilities
    format_amount,
    get_config,
    paid_service,
    process_and_settle,
    require_payment,
    require_payment_choice,
    settle_payment,
    # Client-side (wallet)
    sign_payment,
    sign_payment_from_402,
    # Protocol operations
    verify_payment,
    x402PaymentRequiredResponse,
)

__all__ = [
    # Protocol modules
    "x402",
    # Re-exported from x402 for convenience
    "X402Error",
    "PaymentValidationError",
    "PaymentSettlementError",
    "PaymentRequiredError",
    "X402Config",
    "get_config",
    "configure",
    "create_requirements",
    "require_payment",
    "require_payment_choice",
    "create_tiered_options",
    "sign_payment",
    "sign_payment_from_402",
    "verify_payment",
    "settle_payment",
    "process_and_settle",
    "format_amount",
    "extract_payer_address",
    "paid_service",
    "PaymentRequirements",
    "PaymentPayload",
    "x402PaymentRequiredResponse",
    "VerifyResponse",
    "SettleResponse",
    "FacilitatorClient",
]
