"""Task Payment Gate — x402 Payment Enforcement for Task Execution.

Intercepts task execution and enforces payment based on the configured mode:
- MOCK: Auto-approves with a simulated payment receipt (default)
- TESTNET: Verifies real EIP-3009 signatures on Base Sepolia
- MAINNET: Verifies real EIP-3009 signatures on Base mainnet

Usage:
    from teaming24.payment.crypto.x402.gate import get_payment_gate

    gate = get_payment_gate()

    # Before task execution:
    receipt = await gate.process_task_payment(
        task_id="task-abc123",
        requester_id="0x1234...-a1b2c3",
        payment_data=request.payment,  # from HTTP request
    )

    if not receipt.approved:
        # Return 402 with payment requirements
        return gate.build_402_response(receipt)

    # Proceed with task execution...
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from teaming24.utils.ids import random_hex
from teaming24.utils.logger import get_logger

from .facilitator import get_facilitator
from .merchant import create_requirements
from .types import (
    NetworkMode,
    PaymentPayload,
    PaymentRequirements,
    X402Config,
    get_config,
)

logger = get_logger(__name__)


# ============================================================================
# Payment Receipt (result of payment gate check)
# ============================================================================


@dataclass
class PaymentReceipt:
    """Result of a payment gate check.

    Attributes:
        approved: Whether the payment was accepted (or auto-approved in mock).
        mode: The payment mode used ("mock", "testnet", "mainnet").
        task_id: The task this receipt is for.
        amount: Human-readable amount charged (e.g. "0.001 ETH").
        amount_atomic: Atomic units of the token charged.
        currency: Token symbol (e.g. "ETH").
        network: Blockchain network (e.g. "mock", "base-sepolia", "base").
        payer: Payer address (or "mock-payer" in mock mode).
        payee: Payee/merchant address.
        tx_hash: Transaction hash (mock hash in mock mode).
        timestamp: Unix timestamp of the payment.
        error: Error message if payment was rejected.
        requirements: Payment requirements (for 402 response if not approved).
    """

    approved: bool = False
    mode: str = "mock"
    task_id: str = ""
    amount: str = "0"
    amount_atomic: str = "0"
    currency: str = "ETH"
    network: str = "mock"
    payer: str = ""
    payee: str = ""
    tx_hash: str | None = None
    timestamp: float = field(default_factory=time.time)
    error: str | None = None
    requirements: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization (excludes None values)."""
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


# ============================================================================
# Task Payment Gate
# ============================================================================


class TaskPaymentGate:
    """Enforces x402 payment before task execution.

    The gate sits in the task execution path and decides whether to
    approve, reject, or request payment based on the configured mode.

    Modes:
        MOCK: Auto-approves all tasks with a simulated receipt.
              No blockchain interaction. For development and testing.
        TESTNET: Requires a real EIP-3009 payment signature.
                 Verifies and settles on Base Sepolia.
        MAINNET: Requires a real EIP-3009 payment signature.
                 Verifies and settles on Base mainnet.

    Args:
        config: X402Config (default: global config).
        task_price: Price per task in ETH (default: from app config).
        merchant_address: Address to receive payments (default: from config/env).
    """

    def __init__(
        self,
        config: X402Config | None = None,
        task_price: str | None = None,
        merchant_address: str | None = None,
    ):
        self._config = config or get_config()
        self._task_price = task_price  # Resolved lazily from app config
        self._merchant_address = merchant_address
        self._facilitator = get_facilitator(config=self._config)

        logger.info(
            "TaskPaymentGate initialized",
            extra={
                "mode": self._config.mode.value,
                "network": self._config.network,
            },
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> NetworkMode:
        """Current payment mode."""
        return self._config.mode

    @property
    def is_enabled(self) -> bool:
        """Whether payment enforcement is active.

        Returns True when the app-level payment.enabled flag is set.
        When False, the gate auto-approves everything (free execution).
        """
        try:
            from teaming24.config import get_config as get_app_config
            return get_app_config().payment.enabled
        except Exception as e:
            logger.warning(f"[x402] Could not read payment.enabled from config: {e}")
            return False

    @property
    def token_symbol(self) -> str:
        """Payment token symbol, loaded from app config (e.g. 'ETH', 'USDC')."""
        try:
            from teaming24.config import get_config as get_app_config
            return str(get_app_config().payment.token_symbol)
        except Exception:
            return "ETH"

    @property
    def task_price(self) -> str:
        """Price per task (human-readable, e.g. '0.001')."""
        if self._task_price:
            return self._task_price
        try:
            from teaming24.config import get_config as get_app_config
            return get_app_config().payment.task_price
        except Exception as e:
            logger.warning(f"[x402] Could not read payment.task_price from config: {e} — using default 0.001")
            return "0.001"

    @property
    def merchant_address(self) -> str:
        """Address that receives task payments."""
        if self._merchant_address:
            return self._merchant_address
        addr = self._config.get_merchant_address()
        if addr:
            return addr
        # Fallback: use the node's own wallet address
        try:
            from teaming24.config import get_config as get_app_config
            return get_app_config().local_node.wallet_address or ""
        except Exception as e:
            logger.warning(f"[x402] Could not read wallet address from config: {e}")
            return ""

    # ------------------------------------------------------------------
    # Core: process task payment
    # ------------------------------------------------------------------

    async def process_task_payment(
        self,
        task_id: str,
        requester_id: str = "local",
        payment_data: dict[str, Any] | None = None,
        is_remote: bool = False,
    ) -> PaymentReceipt:
        """Process payment for a task execution.

        This is the main entry point. Call before executing a task.

        Args:
            task_id: Unique task identifier.
            requester_id: ID of the requesting node/user.
            payment_data: Payment data from the HTTP request (if any).
            is_remote: Whether this is a remote task from another AN.

        Returns:
            PaymentReceipt with approval status and payment details.
        """
        # If payment is disabled, auto-approve (free execution)
        if not self.is_enabled:
            return PaymentReceipt(
                approved=True,
                mode="disabled",
                task_id=task_id,
                amount="0",
                currency=self.token_symbol,
                network="none",
                payer=requester_id,
                payee=self.merchant_address,
            )

        # Validate wallet configuration — block transaction if not configured
        merchant = self.merchant_address
        if not merchant:
            logger.error(
                "[x402] PAYMENT BLOCKED: Merchant/wallet address is not configured. "
                "Set TEAMING24_WALLET_ADDRESS or configure via /api/wallet/config.",
                extra={"task_id": task_id, "requester": requester_id},
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                amount=self.task_price,
                currency=self.token_symbol,
                network=self._config.network or "unknown",
                payer=requester_id,
                payee="",
                error="Wallet address not configured. Please configure your wallet before executing tasks.",
            )

        # Route to the appropriate handler based on mode
        if self._config.is_mock:
            return await self._process_mock_payment(
                task_id, requester_id, payment_data
            )
        else:
            return await self._process_real_payment(
                task_id, requester_id, payment_data, is_remote
            )

    # ------------------------------------------------------------------
    # Mock Payment
    # ------------------------------------------------------------------

    async def _process_mock_payment(
        self, task_id: str, requester_id: str,
        payment_data: dict[str, Any] | None = None,
    ) -> PaymentReceipt:
        """Auto-approve with a simulated payment receipt.

        In mock mode, every task is automatically approved without any
        blockchain interaction. When payment_data has amount=0 (retry/same
        main task), no charge is applied — approved with amount="0".
        """
        # Retry/same-main-task: client sent amount=0 → no charge
        amt = 0.0
        if payment_data and "amount" in payment_data:
            try:
                amt = float(payment_data["amount"])
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid x402 payment amount payload: %r",
                    payment_data.get("amount"),
                    exc_info=True,
                )
                pass
        if amt == 0:
            price = "0"
            amount_atomic = "0"
            mock_tx = ""
            logger.info(
                "MOCK payment auto-approved (retry, no charge)",
                extra={"task_id": task_id, "payer": requester_id},
            )
        else:
            price = self.task_price
            amount_atomic = str(int(float(price) * 1_000_000))
            mock_tx = f"0xmock_{random_hex(48)}"
            logger.info(
                "MOCK payment auto-approved",
                extra={
                    "task_id": task_id,
                    "amount": f"{price} {self.token_symbol}",
                    "payer": requester_id,
                    "tx": mock_tx[:24] + "...",
                },
            )

        return PaymentReceipt(
            approved=True,
            mode="mock",
            task_id=task_id,
            amount=f"{price} {self.token_symbol}",
            amount_atomic=amount_atomic,
            currency=self.token_symbol,
            network="mock",
            payer=requester_id,
            payee=self.merchant_address,
            tx_hash=mock_tx or None,
        )

    # ------------------------------------------------------------------
    # Real Payment (Testnet / Mainnet)
    # ------------------------------------------------------------------

    async def _process_real_payment(
        self,
        task_id: str,
        requester_id: str,
        payment_data: dict[str, Any] | None,
        is_remote: bool,
    ) -> PaymentReceipt:
        """Verify and settle a real x402 payment.

        For testnet/mainnet modes, the client must include a signed
        EIP-3009 payment payload in the request. This method:
        1. Checks if payment data is present
        2. Parses the PaymentPayload
        3. Verifies the signature via the facilitator
        4. Settles the payment on-chain
        5. Returns the receipt

        If no payment data is provided, returns a receipt with
        requirements (for the client to sign and resubmit).
        """
        price = self.task_price
        network = self._config.network or "base-sepolia"
        merchant = self.merchant_address

        # No payment data → return 402 requirements
        if not payment_data or payment_data.get("protocol") != "x402":
            requirements = self._create_task_requirements(task_id)
            logger.info(
                "Payment required (no payment data)",
                extra={"task_id": task_id, "amount": f"{price} {self.token_symbol}"},
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                amount=f"{price} {self.token_symbol}",
                currency=self.token_symbol,
                network=network,
                payer=requester_id,
                payee=merchant,
                error="Payment required. Submit x402 payment to proceed.",
                requirements=[_requirements_to_dict(requirements)],
            )

        # Parse payment payload
        try:
            payload_data = payment_data.get("payload")
            if not payload_data:
                return PaymentReceipt(
                    approved=False,
                    mode=self._config.mode.value,
                    task_id=task_id,
                    error="Missing payment payload in request.",
                )

            payload = PaymentPayload.model_validate(payload_data)
        except Exception as e:
            logger.warning(
                "Invalid payment payload",
                extra={"task_id": task_id, "error": str(e)},
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                error=f"Invalid payment payload: {e}",
            )

        # Create requirements for verification
        requirements = self._create_task_requirements(task_id)

        # Step 1: Verify
        try:
            verify_result = await self._facilitator.verify(payload, requirements)
        except Exception as e:
            logger.error(
                "Payment verification error",
                extra={"task_id": task_id, "error": str(e)},
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                error=f"Payment verification failed: {e}",
            )

        if not verify_result.is_valid:
            logger.warning(
                "Payment verification rejected",
                extra={
                    "task_id": task_id,
                    "reason": verify_result.invalid_reason,
                },
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                error=f"Payment rejected: {verify_result.invalid_reason}",
            )

        # Step 2: Settle
        try:
            settle_result = await self._facilitator.settle(payload, requirements)
        except Exception as e:
            logger.error(
                "Payment settlement error",
                extra={"task_id": task_id, "error": str(e)},
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                error=f"Payment settlement failed: {e}",
            )

        if not settle_result.success:
            logger.error(
                "Payment settlement rejected",
                extra={
                    "task_id": task_id,
                    "reason": settle_result.error_reason,
                },
            )
            return PaymentReceipt(
                approved=False,
                mode=self._config.mode.value,
                task_id=task_id,
                error=f"Settlement failed: {settle_result.error_reason}",
            )

        # Success
        payer = verify_result.payer or requester_id
        tx_hash = settle_result.transaction or ""

        logger.info(
            "Payment verified and settled",
            extra={
                "task_id": task_id,
                "payer": payer,
                "tx": tx_hash[:24] + "..." if tx_hash else "N/A",
                "network": settle_result.network or network,
            },
        )

        return PaymentReceipt(
            approved=True,
            mode=self._config.mode.value,
            task_id=task_id,
            amount=f"{price} {self.token_symbol}",
            amount_atomic=str(int(float(price) * 1_000_000)),
            currency=self.token_symbol,
            network=settle_result.network or network,
            payer=payer,
            payee=merchant,
            tx_hash=tx_hash,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_task_requirements(self, task_id: str) -> PaymentRequirements:
        """Create PaymentRequirements for a task execution fee."""
        price = self.task_price
        merchant = self.merchant_address

        return create_requirements(
            price=float(price),
            pay_to=merchant,
            resource=f"/api/agent/execute#{task_id}",
            description=f"Task execution fee: {price} {self.token_symbol}",
        )

    def get_payment_info(self) -> dict[str, Any]:
        """Return current payment gate configuration as a dict.

        Used by the /api/payment/config endpoint.
        """
        try:
            from teaming24.config import get_config as get_app_config
            _cfg = get_app_config().payment.settings
            token_addresses = {
                "base-sepolia": _cfg.default_asset,
                "base": _cfg.mainnet_asset,
            }
        except Exception:
            token_addresses = {}
        return {
            "enabled": self.is_enabled,
            "mode": self._config.mode.value,
            "task_price": self.task_price,
            "currency": self.token_symbol,
            "token_addresses": token_addresses,
            "network": self._config.network or "mock",
            "merchant_address": self.merchant_address,
            "mock_always_valid": self._config.mock_always_valid,
            "mock_always_settled": self._config.mock_always_settled,
        }

    def build_402_response(self, receipt: PaymentReceipt) -> dict[str, Any]:
        """Build an HTTP 402 Payment Required response body.

        Args:
            receipt: A rejected PaymentReceipt with requirements.

        Returns:
            Dict suitable for a JSON response with 402 status.
        """
        return {
            "error": receipt.error or "Payment required",
            "payment": {
                "protocol": "x402",
                "amount": receipt.amount,
                "currency": receipt.currency,
                "network": receipt.network,
                "merchant": receipt.payee,
                "requirements": receipt.requirements,
            },
        }


# ============================================================================
# Module-level Singleton
# ============================================================================


_gate_instance: TaskPaymentGate | None = None


def get_payment_gate() -> TaskPaymentGate:
    """Get or create the global TaskPaymentGate singleton."""
    global _gate_instance
    if _gate_instance is None:
        _gate_instance = TaskPaymentGate()
    return _gate_instance


def reset_payment_gate() -> None:
    """Reset the global gate (for testing or reconfiguration)."""
    global _gate_instance
    _gate_instance = None


# ============================================================================
# Internal Helpers
# ============================================================================


def _requirements_to_dict(req: PaymentRequirements) -> dict[str, Any]:
    """Convert PaymentRequirements to a JSON-serializable dict."""
    try:
        return req.model_dump(by_alias=True)
    except Exception as e:
        logger.warning(f"[x402] model_dump failed for PaymentRequirements, using fallback: {e}")
        return {
            "scheme": getattr(req, "scheme", "exact"),
            "network": getattr(req, "network", ""),
            "asset": getattr(req, "asset", ""),
            "pay_to": getattr(req, "pay_to", ""),
            "max_amount_required": getattr(req, "max_amount_required", "0"),
            "resource": getattr(req, "resource", ""),
            "description": getattr(req, "description", ""),
        }


__all__ = [
    "PaymentReceipt",
    "TaskPaymentGate",
    "get_payment_gate",
    "reset_payment_gate",
]
