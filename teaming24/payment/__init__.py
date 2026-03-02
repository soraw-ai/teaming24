"""Teaming24 Payment Module - x402 Protocol Integration.

This module provides x402 payment capabilities for agent-to-agent transactions.

Example:
    from teaming24.payment import sign_payment, get_config, NetworkMode

    # Configure for testnet
    from teaming24.payment.crypto.x402 import configure
    configure(mode=NetworkMode.TESTNET)

    # Sign a payment
    payload = sign_payment(requirements, private_key)
"""

from teaming24.payment.crypto.x402.gate import (
    PaymentReceipt,
    TaskPaymentGate,
    get_payment_gate,
    reset_payment_gate,
)
from teaming24.payment.crypto.x402.types import (
    NETWORK_PRESETS,
    EIP3009Authorization,
    ExactPaymentPayload,
    # Network Mode
    NetworkMode,
    PaymentPayload,
    PaymentRequiredError,
    PaymentRequirements,
    PaymentSettlementError,
    PaymentValidationError,
    Price,
    SettleResponse,
    SupportedNetworks,
    VerifyResponse,
    # Configuration
    X402Config,
    # Errors
    X402Error,
    configure,
    get_config,
    # Re-exports from x402
    x402_VERSION,
    x402PaymentRequiredResponse,
)
from teaming24.payment.crypto.x402.wallet import (
    sign_payment,
    sign_payment_from_402,
)

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
    # Wallet
    "sign_payment",
    "sign_payment_from_402",
    # Payment Gate
    "PaymentReceipt",
    "TaskPaymentGate",
    "get_payment_gate",
    "reset_payment_gate",
    # Types
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
]
