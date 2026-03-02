"""Client-Side Wallet Operations for x402 Protocol.

Functions for clients/agents to sign payment authorizations using EIP-3009.
"""

from __future__ import annotations

import datetime
import json

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_account.signers.local import LocalAccount
from web3 import Web3

from teaming24.utils.logger import get_logger

from .types import (
    EIP3009Authorization,
    ExactPaymentPayload,
    PaymentPayload,
    PaymentRequirements,
    PaymentValidationError,
    get_config,
    x402_VERSION,
    x402PaymentRequiredResponse,
)

logger = get_logger(__name__)


# ERC-20 ABI for nonce and token info (EIP-3009 compatible tokens)
_ERC20_ABI = json.loads("""[
    {"inputs": [], "name": "name", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "version", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}], "name": "nonces", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}
]""")


def _build_eip712_typed_data(
    from_addr: str,
    to_addr: str,
    value: int,
    valid_after: int,
    valid_before: int,
    nonce: bytes,
    chain_id: int,
    contract: str,
    token_name: str,
    token_version: str,
) -> dict:
    """Build EIP-712 typed data for EIP-3009 TransferWithAuthorization."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": token_name,
            "version": token_version,
            "chainId": chain_id,
            "verifyingContract": contract,
        },
        "message": {
            "from": from_addr,
            "to": to_addr,
            "value": value,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": nonce,
        },
    }


def sign_payment(
    requirements: PaymentRequirements,
    private_key: str,
    valid_hours: float = 1.0,
) -> PaymentPayload:
    """Sign a payment authorization using EIP-3009.

    Args:
        requirements: PaymentRequirements from 402 response
        private_key: Hex-encoded private key (with or without 0x prefix)
        valid_hours: Payment validity duration in hours

    Returns:
        Signed PaymentPayload ready for X-PAYMENT header

    Example:
        payload = sign_payment(
            requirements=response_402.accepts[0],
            private_key=os.environ["WALLET_PRIVATE_KEY"]
        )
        headers = {"X-PAYMENT": payload.model_dump_json()}
    """
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    account: LocalAccount = Account.from_key(private_key)
    logger.debug("Signing payment", extra={"from": account.address, "to": requirements.pay_to})

    # Connect to chain and get token contract
    config = get_config()
    rpc_url = config.get_rpc_url()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    token = w3.eth.contract(
        address=Web3.to_checksum_address(requirements.asset),
        abi=_ERC20_ABI
    )

    # Get nonce and token info from chain
    nonce_int = token.functions.nonces(account.address).call()
    nonce_bytes = nonce_int.to_bytes(32, "big")
    chain_id = w3.eth.chain_id
    token_name = token.functions.name().call()
    token_version = token.functions.version().call()

    # Build authorization parameters
    now = datetime.datetime.now(datetime.UTC)
    valid_after = 0
    valid_before = int((now + datetime.timedelta(hours=valid_hours)).timestamp())
    value = int(requirements.max_amount_required)

    # Build and sign EIP-712 typed data
    typed_data = _build_eip712_typed_data(
        from_addr=account.address,
        to_addr=requirements.pay_to,
        value=value,
        valid_after=valid_after,
        valid_before=valid_before,
        nonce=nonce_bytes,
        chain_id=chain_id,
        contract=requirements.asset,
        token_name=token_name,
        token_version=token_version,
    )

    signable = encode_typed_data(full_message=typed_data)
    signed = account.sign_message(signable)

    # Build signature hex (r + s + v concatenated)
    signature = (
        f"0x{signed.r.to_bytes(32, 'big').hex()}"
        f"{signed.s.to_bytes(32, 'big').hex()}"
        f"{signed.v:02x}"
    )

    authorization = EIP3009Authorization(
        from_=account.address,
        to=requirements.pay_to,
        value=str(value),
        valid_after=str(valid_after),
        valid_before=str(valid_before),
        nonce=f"0x{nonce_bytes.hex()}",
    )

    logger.info("Payment signed", extra={
        "from": account.address,
        "to": requirements.pay_to,
        "amount": value,
        "network": requirements.network,
    })

    return PaymentPayload(
        x402_version=x402_VERSION,
        scheme=requirements.scheme,
        network=requirements.network,
        payload=ExactPaymentPayload(signature=signature, authorization=authorization),
    )


def sign_payment_from_402(
    response_402: x402PaymentRequiredResponse,
    private_key: str,
    requirement_index: int = 0,
) -> PaymentPayload:
    """Sign payment from a 402 response, selecting from available options.

    Args:
        response_402: The x402PaymentRequiredResponse from server
        private_key: Wallet private key
        requirement_index: Which payment option to select (default: first)

    Returns:
        Signed PaymentPayload

    Raises:
        PaymentValidationError: If no requirements or invalid index
    """
    if not response_402.accepts:
        raise PaymentValidationError("No payment requirements in 402 response")

    if requirement_index >= len(response_402.accepts):
        raise PaymentValidationError(f"Invalid requirement index: {requirement_index}")

    return sign_payment(response_402.accepts[requirement_index], private_key)


__all__ = [
    "sign_payment",
    "sign_payment_from_402",
]
