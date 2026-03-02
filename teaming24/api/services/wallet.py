"""Wallet ledger service.

Single source of truth for in-memory wallet ledger writes and conversion
between snake_case (storage/REST) and camelCase (frontend SSE payloads).
"""
from __future__ import annotations

import time
from typing import Any

import teaming24.api.state as _st
from teaming24.api.deps import config, logger
from teaming24.data.database import get_database
from teaming24.utils.ids import prefixed_id

_DEFAULT_TASK_PRICE_USDC = 0.001


def _wallet_ledger_capacity() -> int:
    """Get bounded wallet ledger capacity from config."""
    return max(1, int(config.api.wallet_ledger_capacity))


def wallet_tx_to_frontend(tx: dict[str, Any]) -> dict[str, Any]:
    """Convert snake_case wallet tx to frontend/SSE camelCase shape."""
    return {
        "id": tx.get("id", ""),
        "timestamp": tx.get("timestamp", 0),
        "type": tx.get("type", ""),
        "amount": tx.get("amount", 0),
        "taskId": tx.get("task_id") or tx.get("taskId", ""),
        "taskName": tx.get("task_name") or tx.get("taskName", ""),
        "description": tx.get("description", ""),
        "txHash": tx.get("tx_hash") or tx.get("txHash", ""),
        "payer": tx.get("payer", ""),
        "payee": tx.get("payee", ""),
        "mode": tx.get("mode", "mock"),
        "network": tx.get("network", "mock"),
    }


def record_wallet_transaction(
    tx_type: str,
    amount: float,
    task_id: str = "",
    task_name: str = "",
    description: str = "",
    tx_hash: str = "",
    payer: str = "",
    payee: str = "",
    mode: str = "mock",
    network: str = "mock",
) -> dict[str, Any]:
    """Record transaction into canonical state ledger and persist to DB.

    Returns the frontend/SSE transaction payload (camelCase).
    """
    amount = abs(amount)

    with _st.wallet_lock:
        capacity = _wallet_ledger_capacity()
        st_tx = {
            "id": prefixed_id("tx", 12),
            "timestamp": time.time() * 1000,  # milliseconds for frontend
            "type": tx_type,
            "amount": round(amount, 6),
            "task_id": task_id,
            "task_name": task_name,
            "description": description,
            "tx_hash": tx_hash,
            "payer": payer,
            "payee": payee,
            "mode": mode,
            "network": network,
        }
        _st.wallet_ledger.append(st_tx)
        if len(_st.wallet_ledger) > capacity:
            _st.wallet_ledger[:] = _st.wallet_ledger[-capacity:]

        if tx_type == "expense":
            _st.mock_balance = max(0, _st.mock_balance - amount)
        elif tx_type in ("income", "topup"):
            _st.mock_balance += amount

    try:
        get_database().save_wallet_transaction(st_tx)
    except Exception as e:
        logger.debug(f"Wallet DB persist error: {e}")

    logger.debug(
        "Wallet transaction recorded",
        extra={
            "type": tx_type,
            "amount": amount,
            "task_id": task_id,
            "balance": _st.mock_balance,
        },
    )
    return wallet_tx_to_frontend(st_tx)


def resolve_payment_defaults() -> tuple[float, str, str]:
    """Resolve (task_price, mode, network) from config with safe fallbacks."""
    mode = str(getattr(config.payment, "mode", "mock") or "mock").strip().lower()

    try:
        task_price = float(getattr(config.payment, "task_price", _DEFAULT_TASK_PRICE_USDC))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid payment.task_price in config (%r); using default %s",
            getattr(config.payment, "task_price", None),
            _DEFAULT_TASK_PRICE_USDC,
        )
        task_price = _DEFAULT_TASK_PRICE_USDC

    if mode == "mock":
        return task_price, "mock", "mock"

    wallet_network = str(_st.wallet_config.get("network") or "").strip().lower()
    config_network = str(getattr(config.payment.network, "name", "") or "").strip().lower()

    # Prefer explicitly configured wallet network, then payment.network.name.
    network = wallet_network or config_network
    if not network:
        network = "base" if mode == "mainnet" else "base-sepolia"

    # Normalize obvious mode/network mismatches.
    if mode == "mainnet" and network.endswith("sepolia"):
        network = "base"
    elif mode == "testnet" and network == "base":
        network = "base-sepolia"

    return task_price, mode, network


def restore_wallet_transactions(db_txs: list[dict[str, Any]]) -> float:
    """Replace in-memory ledger from DB and return recomputed mock balance."""
    with _st.wallet_lock:
        capacity = _wallet_ledger_capacity()
        _st.wallet_ledger.clear()
        _st.wallet_ledger.extend(db_txs[-capacity:])

        balance = config.payment.mock.initial_balance
        for tx in db_txs:
            amount = tx.get("amount") or 0
            if tx.get("type") == "expense":
                balance = max(0, balance - amount)
            elif tx.get("type") in ("income", "topup"):
                balance += amount

        _st.mock_balance = round(balance, 6)
        return _st.mock_balance
