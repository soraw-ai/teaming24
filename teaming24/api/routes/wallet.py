"""
Wallet configuration, balance, transactions, and x402 payment endpoints.

This module handles wallet setup (address, private key, network), balance
queries (mock or on-chain ETH), transaction ledger, summary stats, and
x402 payment gate configuration.

Endpoints
---------
Wallet:
  - GET /api/wallet/config — Current wallet config (address, network, is_configured)
  - POST /api/wallet/config — Set wallet (address, private_key, network)
  - GET /api/wallet/balance — Balance (mock or on-chain ETH)
  - GET /api/wallet/transactions — Transaction ledger (limit, offset)
  - GET /api/wallet/summary — Aggregated stats (income, expenses, net profit)

Payment (x402):
  - GET /api/payment/config — Payment gate info (enabled, mode, task_price)
  - POST /api/payment/config — Update enabled, mode, task_price
  - GET /api/payment/status — Status summary (active/disabled, merchant address)

Dependencies
------------
Uses ``teaming24.api.deps``: ``config``, ``logger``, ``BASE_DIR``.
Uses ``teaming24.api.state``: ``wallet_config``, ``wallet_ledger``,
``mock_balance`` for in-memory wallet state and ledger.

Extending
---------
Add endpoints with ``@router.get(...)`` or ``@router.post(...)``.
Use ``_st.wallet_config`` and ``_st.wallet_ledger`` for state.
Call ``record_wallet_transaction()`` to append to the ledger.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import teaming24.api.state as _st
from teaming24.api.deps import BASE_DIR, config, logger
from teaming24.api.services import wallet as _wallet_service

router = APIRouter(tags=["wallet"])

def _require_local(request: Request) -> JSONResponse | None:
    """Restrict sensitive wallet/payment writes to loopback unless explicitly allowed."""
    allow_remote = os.getenv("TEAMING24_ALLOW_REMOTE_ADMIN", "").lower() in ("1", "true", "yes")
    if allow_remote:
        return None
    host = request.client.host if request and request.client else ""
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError as exc:
        logger.debug("Non-IP request host in wallet _require_local: %s (%s)", host, exc)
        if host in ("localhost",):
            return None
    return JSONResponse(status_code=403, content={"error": "local access only"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_env_file(env_path: Path, updates: dict):
    lines = []
    existing_keys = set()
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                updated = False
                for key, value in updates.items():
                    if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                        lines.append(f"{key}={value}\n")
                        existing_keys.add(key)
                        updated = True
                        break
                if not updated:
                    lines.append(line)
    for key, value in updates.items():
        if key not in existing_keys:
            if lines and not lines[-1].strip() == "":
                lines.append("\n")
            lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def load_wallet_from_env():
    """Load wallet configuration from environment variables (called at startup)."""
    import os
    address = os.getenv("TEAMING24_WALLET_ADDRESS", "")
    private_key = os.getenv("TEAMING24_WALLET_PRIVATE_KEY", "")
    network = os.getenv("TEAMING24_WALLET_NETWORK", "base-sepolia")

    if address:
        _st.wallet_config["address"] = address
        _st.wallet_config["is_configured"] = True
        _st.wallet_config["network"] = network
        if private_key:
            _st.wallet_config["_private_key"] = private_key
        try:
            config.local_node.wallet_address = address
        except Exception as e:
            logger.debug("Wallet config load: %s", e)
        if not config.payment.enabled:
            config.payment.enabled = True
            logger.info("[Wallet] Payment auto-enabled (wallet found in env)")
        logger.info("Wallet loaded from environment", extra={
            "address": address[:10] + "...", "network": network,
        })


def record_wallet_transaction(
    tx_type: str, amount: float, task_id: str = "", task_name: str = "",
    description: str = "", tx_hash: str = "", payer: str = "", payee: str = "",
    mode: str = "mock", network: str = "mock",
) -> dict:
    return _wallet_service.record_wallet_transaction(
        tx_type=tx_type,
        amount=amount,
        task_id=task_id,
        task_name=task_name,
        description=description,
        tx_hash=tx_hash,
        payer=payer,
        payee=payee,
        mode=mode,
        network=network,
    )


# ---------------------------------------------------------------------------
# Wallet config endpoints
# ---------------------------------------------------------------------------

class WalletConfigRequest(BaseModel):
    address: str
    private_key: str | None = None
    network: str = "base-sepolia"


@router.get("/api/wallet/config")
async def get_wallet_config():
    return {
        "address": _st.wallet_config["address"],
        "is_configured": _st.wallet_config["is_configured"],
        "network": _st.wallet_config["network"],
    }


@router.post("/api/wallet/config")
async def set_wallet_config(request: WalletConfigRequest, http_request: Request):
    guard = _require_local(http_request)
    if guard:
        return guard
    if not request.address.startswith("0x") or len(request.address) != 42:
        raise HTTPException(status_code=400, detail="Invalid wallet address format")
    if request.network not in ("base", "base-sepolia"):
        raise HTTPException(status_code=400, detail="Invalid network")

    pk = None
    if request.private_key:
        pk = request.private_key.strip()
        if not pk.startswith("0x"):
            pk = "0x" + pk
        if len(pk) != 66 or not all(c in '0123456789abcdefABCDEF' for c in pk[2:]):
            raise HTTPException(status_code=400, detail="Invalid private key format")

    _st.wallet_config["address"] = request.address
    _st.wallet_config["is_configured"] = True
    _st.wallet_config["network"] = request.network
    if pk:
        _st.wallet_config["_private_key"] = pk

    try:
        config.local_node.wallet_address = request.address
    except Exception as e:
        logger.warning(f"Could not sync wallet address: {e}")

    if not config.payment.enabled:
        config.payment.enabled = True

    try:
        from teaming24.payment.crypto.x402.gate import reset_payment_gate
        reset_payment_gate()
    except Exception as e:
        logger.debug("Wallet env load: %s", e)

    env_path = BASE_DIR / ".env"
    _update_env_file(env_path, {
        "TEAMING24_WALLET_ADDRESS": request.address,
        "TEAMING24_WALLET_NETWORK": request.network,
        **({"TEAMING24_WALLET_PRIVATE_KEY": pk} if pk else {}),
    })
    return {
        "status": "ok",
        "address": request.address,
        "network": request.network,
        "payment_enabled": config.payment.enabled,
        "saved_to": str(env_path),
    }


# ---------------------------------------------------------------------------
# Balance / transactions / summary
# ---------------------------------------------------------------------------

@router.get("/api/wallet/balance")
async def get_wallet_balance():
    payment_mode = config.payment.mode if hasattr(config, "payment") else "mock"
    payment_enabled = config.payment.enabled if hasattr(config, "payment") else False

    token_symbol = config.payment.token_symbol
    token_contracts = {
        "base": config.payment.settings.mainnet_asset,
        "base-sepolia": config.payment.settings.default_asset,
    }

    if not payment_enabled or payment_mode == "mock":
        return {
            "address": _st.wallet_config.get("address", ""),
            "balance": round(_st.mock_balance, 6),
            "network": "mock", "currency": token_symbol,
            "mode": "mock", "is_mock": True,
        }

    import httpx
    if not _st.wallet_config["is_configured"]:
        return {"address": "", "balance": 0, "network": payment_mode,
                "currency": token_symbol, "mode": payment_mode, "is_mock": False,
                "error": "Wallet not configured"}

    address = _st.wallet_config["address"]
    network = _st.wallet_config["network"]
    rpc_urls = {"base": "https://mainnet.base.org", "base-sepolia": "https://sepolia.base.org"}
    token_contract = token_contracts.get(network)
    rpc_url = rpc_urls.get(network)
    if not token_contract or not rpc_url:
        raise HTTPException(status_code=400, detail="Invalid network configuration")

    try:
        data = "0x70a08231" + address[2:].lower().zfill(64)
        async with httpx.AsyncClient(timeout=config.api.http_client_timeout) as client:
            response = await client.post(rpc_url, json={
                "jsonrpc": "2.0", "method": "eth_call",
                "params": [{"to": token_contract, "data": data}, "latest"], "id": 1,
            })
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to query blockchain")
            result = response.json()
            if "error" in result:
                raise HTTPException(status_code=502, detail="Blockchain query failed")
            balance_hex = result.get("result", "0x0")
            balance = int(balance_hex, 16) / 1e6
            return {"address": address, "balance": balance, "network": network,
                    "currency": token_symbol, "mode": payment_mode, "is_mock": False}
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to query blockchain: {e}") from e


@router.get("/api/wallet/transactions")
async def get_wallet_transactions(limit: int = 50, offset: int = 0):
    total = len(_st.wallet_ledger)
    txs = list(reversed(_st.wallet_ledger))
    return {"transactions": txs[offset:offset + limit], "total": total, "limit": limit, "offset": offset}


class MockTopupRequest(BaseModel):
    amount: float = 100.0


@router.post("/api/wallet/mock-topup")
async def mock_topup(body: MockTopupRequest, http_request: Request):
    """Add ETH to mock balance for dev/testing (mock payment mode only)."""
    guard = _require_local(http_request)
    if guard:
        return guard

    payment_enabled = config.payment.enabled if hasattr(config, "payment") else False
    payment_mode = config.payment.mode if hasattr(config, "payment") else "mock"
    if payment_enabled and payment_mode != "mock":
        raise HTTPException(status_code=400, detail="Mock topup only available in mock payment mode")

    if body.amount <= 0 or body.amount > 10000:
        raise HTTPException(status_code=400, detail="Amount must be between 1 and 10000 ETH")

    sym = config.payment.token_symbol
    tx = record_wallet_transaction(
        tx_type="topup",
        amount=body.amount,
        description=f"Dev topup +{body.amount} {sym} (mock)",
        mode="mock",
        network="mock",
    )
    return {
        "balance": round(_st.mock_balance, 6),
        "amount": body.amount,
        "transaction": tx,
    }


@router.get("/api/wallet/summary")
async def get_wallet_summary():
    total_income = sum(t["amount"] for t in _st.wallet_ledger if t["type"] == "income")
    total_expenses = sum(t["amount"] for t in _st.wallet_ledger if t["type"] == "expense")
    total_topups = sum(t["amount"] for t in _st.wallet_ledger if t["type"] == "topup")
    task_txs = [t for t in _st.wallet_ledger if t["type"] in ("expense", "income")]
    last_task_tx = task_txs[-1] if task_txs else None
    last_expense_txs = [t for t in _st.wallet_ledger if t["type"] == "expense"]
    last_income_txs = [t for t in _st.wallet_ledger if t["type"] == "income"]

    return {
        "balance": round(_st.mock_balance, 6),
        "address": _st.wallet_config.get("address", ""),
        "is_configured": _st.wallet_config.get("is_configured", False),
        "payment_enabled": config.payment.enabled if hasattr(config, "payment") else False,
        "payment_mode": config.payment.mode if hasattr(config, "payment") else "mock",
        "total_income": round(total_income, 6),
        "total_expenses": round(total_expenses, 6),
        "total_topups": round(total_topups, 6),
        "net_profit": round(total_income - total_expenses, 6),
        "transaction_count": len(_st.wallet_ledger),
        "last_task_expense": round(last_expense_txs[-1]["amount"], 6) if last_expense_txs else 0,
        "last_task_income": round(last_income_txs[-1]["amount"], 6) if last_income_txs else 0,
        "last_task_id": last_task_tx["task_id"] if last_task_tx else None,
        "last_task_name": last_task_tx["task_name"] if last_task_tx else None,
    }


# ---------------------------------------------------------------------------
# Payment config (x402)
# ---------------------------------------------------------------------------

class PaymentConfigUpdateRequest(BaseModel):
    enabled: bool | None = None
    mode: str | None = None
    task_price: str | None = None


@router.get("/api/payment/config")
async def get_payment_config():
    from teaming24.payment.crypto.x402.gate import get_payment_gate
    return get_payment_gate().get_payment_info()


@router.post("/api/payment/config")
async def update_payment_config(request: PaymentConfigUpdateRequest, http_request: Request):
    guard = _require_local(http_request)
    if guard:
        return guard
    from teaming24.payment.crypto.x402.gate import get_payment_gate, reset_payment_gate
    from teaming24.payment.crypto.x402.types import NetworkMode
    from teaming24.payment.crypto.x402.types import configure as x402_configure

    if request.mode is not None and request.mode not in {"mock", "testnet", "mainnet"}:
        raise HTTPException(status_code=400, detail=f"Invalid payment mode: '{request.mode}'")
    if request.task_price is not None:
        try:
            if float(request.task_price) < 0:
                raise ValueError
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid task_price: '{request.task_price}'") from e

    if request.enabled is not None:
        config.payment.enabled = request.enabled
    if request.mode is not None:
        config.payment.mode = request.mode
        x402_configure(mode=NetworkMode(request.mode))
    if request.task_price is not None:
        config.payment.task_price = request.task_price

    reset_payment_gate()
    return get_payment_gate().get_payment_info()


@router.get("/api/payment/status")
async def get_payment_status():
    from teaming24.payment.crypto.x402.gate import get_payment_gate
    info = get_payment_gate().get_payment_info()
    return {
        "status": "active" if info["enabled"] else "disabled",
        "mode": info["mode"],
        "task_price": f"{info['task_price']} {info['currency']}",
        "network": info["network"],
        "merchant_address": info["merchant_address"],
    }
