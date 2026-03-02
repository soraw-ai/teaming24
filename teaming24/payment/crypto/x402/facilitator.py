"""x402 Facilitator Implementations.

Provides different facilitator implementations for various network modes:
- MockFacilitator: For development/testing without real blockchain calls
- LocalFacilitator: For testnet with local transaction signing
- Standard FacilitatorClient: For production via x402.org

Usage:
    from teaming24.payment.crypto.x402 import get_facilitator, NetworkMode

    # Get appropriate facilitator for current mode
    facilitator = get_facilitator()

    # Or specify mode explicitly
    facilitator = get_facilitator(mode=NetworkMode.MOCK)
"""

from __future__ import annotations

import os

from teaming24.utils.ids import random_hex
from teaming24.utils.logger import get_logger

from ._local_types import (
    ExactPaymentPayload,
    FacilitatorClient,
    PaymentPayload,
    PaymentRequirements,
    SettleResponse,
    VerifyResponse,
)
from .types import NetworkMode, X402Config, get_config

logger = get_logger(__name__)


# ============================================================================
# Mock Facilitator
# ============================================================================


class MockFacilitator(FacilitatorClient):
    """Mock facilitator for development and testing.

    Bypasses all real blockchain calls and returns configurable responses.
    Useful for:
    - Local development without testnet tokens
    - Unit testing payment flows
    - Demo applications

    Args:
        always_valid: Whether verify() always returns valid (default: True)
        always_settled: Whether settle() always succeeds (default: True)

    Example:
        facilitator = MockFacilitator()
        result = await facilitator.verify(payload, requirements)
        assert result.is_valid  # Always True in mock mode
    """

    def __init__(self, always_valid: bool = True, always_settled: bool = True):
        self._always_valid = always_valid
        self._always_settled = always_settled

    async def verify(
        self,
        payload: PaymentPayload,
        requirements: PaymentRequirements
    ) -> VerifyResponse:
        """Mock verification - extracts payer and returns configured result."""
        logger.debug("MOCK: Verifying payment", extra={"resource": requirements.resource})

        payer = None
        if isinstance(payload.payload, ExactPaymentPayload):
            payer = payload.payload.authorization.from_

        if self._always_valid:
            logger.info("MOCK: Payment verified", extra={"payer": payer})
            return VerifyResponse(is_valid=True, payer=payer)
        else:
            logger.warning("MOCK: Payment verification failed (configured to fail)")
            return VerifyResponse(is_valid=False, invalid_reason="mock_invalid_payload")

    async def settle(
        self,
        payload: PaymentPayload,
        requirements: PaymentRequirements
    ) -> SettleResponse:
        """Mock settlement - returns configured result with fake transaction."""
        logger.debug("MOCK: Settling payment", extra={"network": requirements.network})

        if self._always_settled:
            # Generate a fake transaction hash
            mock_tx = f"0x{random_hex(64)}"

            payer = None
            if isinstance(payload.payload, ExactPaymentPayload):
                payer = payload.payload.authorization.from_

            logger.info("MOCK: Payment settled", extra={"tx": mock_tx[:20] + "..."})
            return SettleResponse(
                success=True,
                network="mock",
                transaction=mock_tx,
                payer=payer,
            )
        else:
            logger.warning("MOCK: Settlement failed (configured to fail)")
            return SettleResponse(
                success=False,
                error_reason="mock_settlement_failed"
            )


# ============================================================================
# Local Facilitator (Testnet)
# ============================================================================


class LocalFacilitator(FacilitatorClient):
    """Local facilitator for testnet transactions.

    Performs real EIP-3009 transferWithAuthorization transactions
    on Base Sepolia testnet. Requires:
    - FACILITATOR_PRIVATE_KEY env var
    - RPC_URL env var (or uses Base Sepolia default)

    Warning:
        This facilitator signs and broadcasts real transactions.
        Only use on testnet with test tokens.
    """

    # Base Sepolia ETH token contract
    USDC_ADDRESS = "0x4182528b6660B9c0875c6e94260A2E425F00797f"
    USDC_ABI = [
        {
            "inputs": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "signature", "type": "bytes"}
            ],
            "name": "transferWithAuthorization",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        }
    ]

    def __init__(self):
        try:
            from web3 import Web3
        except ImportError as e:
            raise ImportError("web3 package required for LocalFacilitator: pip install web3") from e

        rpc_url = os.getenv("RPC_URL", "https://sepolia.base.org")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 120}))

        self._private_key = os.getenv("FACILITATOR_PRIVATE_KEY")
        if not self._private_key:
            logger.warning("FACILITATOR_PRIVATE_KEY not set, settlement will fail")

    async def verify(
        self,
        payload: PaymentPayload,
        requirements: PaymentRequirements
    ) -> VerifyResponse:
        """Verify payment signature off-chain."""
        logger.debug("LOCAL: Verifying payment")

        payer = None
        if isinstance(payload.payload, ExactPaymentPayload):
            payer = payload.payload.authorization.from_

            # NOTE: Off-chain signature verification is not yet implemented.
            # The payload is trusted as-is; a future implementation should verify
            # the EIP-3009 transferWithAuthorization signature before settling.
            return VerifyResponse(is_valid=True, payer=payer)

        return VerifyResponse(is_valid=False, invalid_reason="unsupported_payload_type")

    async def settle(
        self,
        payload: PaymentPayload,
        requirements: PaymentRequirements
    ) -> SettleResponse:
        """Execute transferWithAuthorization on-chain."""
        logger.info("LOCAL: Settling payment on-chain")

        if not self._private_key:
            return SettleResponse(success=False, error_reason="FACILITATOR_PRIVATE_KEY not set")

        try:
            if not isinstance(payload.payload, ExactPaymentPayload):
                return SettleResponse(success=False, error_reason="unsupported_payload_type")

            auth = payload.payload.authorization
            account = self.w3.eth.account.from_key(self._private_key)

            contract = self.w3.eth.contract(
                address=self.USDC_ADDRESS,
                abi=self.USDC_ABI
            )

            # Build transaction
            tx = contract.functions.transferWithAuthorization(
                self.w3.to_checksum_address(auth.from_),
                self.w3.to_checksum_address(auth.to),
                int(auth.value),
                int(auth.valid_after),
                int(auth.valid_before),
                bytes.fromhex(auth.nonce.removeprefix("0x")),
                bytes.fromhex(payload.payload.signature.removeprefix("0x")),
            ).build_transaction({
                "from": account.address,
                "nonce": self.w3.eth.get_transaction_count(account.address),
                "gas": 200000,
                "chainId": self.w3.eth.chain_id,
            })

            # Sign and send
            signed = self.w3.eth.account.sign_transaction(tx, self._private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt.status == 1:
                logger.info("LOCAL: Settlement successful", extra={"tx": tx_hash.hex()})
                return SettleResponse(
                    success=True,
                    network="base-sepolia",
                    transaction=tx_hash.hex(),
                    payer=auth.from_,
                )
            else:
                logger.error("LOCAL: Transaction failed")
                return SettleResponse(success=False, error_reason="transaction_failed")

        except Exception as e:
            logger.error("LOCAL: Settlement error", extra={"error": str(e)})
            return SettleResponse(success=False, error_reason=str(e))


# ============================================================================
# Remote Facilitator Stub (when no local key)
# ============================================================================


class RemoteFacilitatorStub(FacilitatorClient):
    """Placeholder when remote facilitator (x402.org) is not configured.

    Returns failure on verify/settle. To use testnet/mainnet without
    FACILITATOR_PRIVATE_KEY, integrate with x402.org facilitator API.
    """

    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        return VerifyResponse(
            is_valid=False,
            invalid_reason="Remote facilitator not configured. Set FACILITATOR_PRIVATE_KEY for local settlement.",
        )

    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        return SettleResponse(
            success=False,
            error_reason="Remote facilitator not configured. Set FACILITATOR_PRIVATE_KEY for local settlement.",
        )


# ============================================================================
# Facilitator Factory
# ============================================================================


def get_facilitator(
    mode: NetworkMode | None = None,
    config: X402Config | None = None,
) -> FacilitatorClient:
    """Get appropriate facilitator for the given mode.

    Args:
        mode: Network mode (default: from config)
        config: X402Config (default: global config)

    Returns:
        FacilitatorClient implementation:
        - MOCK: MockFacilitator
        - TESTNET: LocalFacilitator or FacilitatorClient
        - MAINNET: FacilitatorClient

    Example:
        # Use global config mode
        facilitator = get_facilitator()

        # Force mock mode
        facilitator = get_facilitator(mode=NetworkMode.MOCK)
    """
    cfg = config or get_config()
    effective_mode = mode or cfg.mode

    if effective_mode == NetworkMode.MOCK:
        logger.debug("Using MockFacilitator")
        return MockFacilitator(
            always_valid=cfg.mock_always_valid,
            always_settled=cfg.mock_always_settled,
        )

    elif effective_mode == NetworkMode.TESTNET:
        if os.getenv("FACILITATOR_PRIVATE_KEY"):
            logger.debug("Using LocalFacilitator for testnet")
            return LocalFacilitator()
        logger.debug("Using RemoteFacilitatorStub for testnet (no FACILITATOR_PRIVATE_KEY)")
        return RemoteFacilitatorStub()

    else:  # MAINNET
        if os.getenv("FACILITATOR_PRIVATE_KEY"):
            logger.debug("Using LocalFacilitator for mainnet")
            return LocalFacilitator()
        logger.debug("Using RemoteFacilitatorStub for mainnet (no FACILITATOR_PRIVATE_KEY)")
        return RemoteFacilitatorStub()


__all__ = [
    "MockFacilitator",
    "LocalFacilitator",
    "get_facilitator",
]
