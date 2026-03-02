"""x402 Payment Protocol for Agentic Node (AN) HTTP 402 Payments.

This module provides a simplified x402 implementation focused on agentic node payments
via the HTTP 402 Payment Required protocol. It handles payment requirement creation,
wallet signing, verification, and settlement for AN-to-AN transactions.

Network Modes:
    - MOCK (default): No real blockchain calls, for development/testing
    - TESTNET: Base Sepolia testnet with real transactions
    - MAINNET: Base mainnet for production

Quick Start:
    # Configure mode (default is MOCK - no real transactions)
    from teaming24.payment.crypto.x402 import configure, NetworkMode

    configure(mode=NetworkMode.MOCK)      # Development (default)
    configure(mode=NetworkMode.TESTNET)   # Testing
    configure(mode=NetworkMode.MAINNET)   # Production

    # Server-side: Require payment
    from teaming24.payment.crypto.x402 import require_payment

    raise require_payment(
        price="$1.00",
        pay_to="0x742d35Cc6634C0532925a3b844Bc9e7595f0Ab13",
        resource="/api/premium"
    )

    # Client-side: Sign and submit payment
    from teaming24.payment.crypto.x402 import sign_payment_from_402

    payload = sign_payment_from_402(response_402, private_key)
    headers = {"X-PAYMENT": payload.model_dump_json()}
"""

# Types and configuration
# Facilitators
from .facilitator import (
    LocalFacilitator,
    MockFacilitator,
    get_facilitator,
)

# Task payment gate
from .gate import (
    PaymentReceipt,
    TaskPaymentGate,
    get_payment_gate,
    reset_payment_gate,
)

# Server-side (merchant) functions
from .merchant import (
    create_requirements,
    create_tiered_options,
    require_payment,
    require_payment_choice,
)

# Protocol operations and utilities
from .protocol import (
    extract_payer_address,
    format_amount,
    paid_service,
    process_and_settle,
    settle_payment,
    verify_payment,
)
from .types import (
    NETWORK_PRESETS,
    EIP3009Authorization,
    ExactPaymentPayload,
    FacilitatorClient,
    FacilitatorConfig,
    # Network Mode
    NetworkMode,
    PaymentPayload,
    PaymentRequiredError,
    # Re-exports from x402 package
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
    x402PaymentRequiredResponse,
)

# Client-side (wallet) functions
from .wallet import (
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
    # Facilitators
    "MockFacilitator",
    "LocalFacilitator",
    "get_facilitator",
    # Server-side (merchant)
    "create_requirements",
    "require_payment",
    "require_payment_choice",
    "create_tiered_options",
    # Client-side (wallet)
    "sign_payment",
    "sign_payment_from_402",
    # Protocol operations
    "verify_payment",
    "settle_payment",
    "process_and_settle",
    # Utilities
    "format_amount",
    "extract_payer_address",
    # Decorator
    "paid_service",
    # Task payment gate
    "PaymentReceipt",
    "TaskPaymentGate",
    "get_payment_gate",
    "reset_payment_gate",
    # Re-exports from x402 package
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
