# x402 Payments

Teaming24 uses the x402 protocol for crypto payments between Agentic Nodes.

## Overview

x402 is an HTTP-based payment protocol using the 402 Payment Required status code. It enables AN-to-AN payments using EIP-3009 (transferWithAuthorization) for gasless USDC transfers.

## Payment Flow

```
┌────────────┐                              ┌────────────┐
│   Client   │                              │   Server   │
│    (AN)    │                              │    (AN)    │
└─────┬──────┘                              └─────┬──────┘
      │                                           │
      │  1. Request protected resource            │
      │──────────────────────────────────────────►│
      │                                           │
      │  2. 402 Payment Required                  │
      │  (PaymentRequirements)                    │
      │◄──────────────────────────────────────────│
      │                                           │
      │  3. Sign payment (EIP-3009)               │
      │                                           │
      │  4. Retry with X-PAYMENT header           │
      │──────────────────────────────────────────►│
      │                                           │
      │                    5. Verify & Settle     │
      │                                           │
      │  6. Return resource                       │
      │◄──────────────────────────────────────────│
      │                                           │
```

## Quick Start

### Server-side (Merchant)

Require payment for a resource:

```python
from teaming24.payment.crypto.x402 import require_payment

# Simple payment requirement
raise require_payment(
    price="$1.00",
    pay_to="0x742d35Cc6634C0532925a3b844Bc9e7595f0Ab13",
    resource="/api/premium",
    message="Premium feature requires payment"
)
```

### Client-side (Wallet)

Sign and submit payment:

```python
from teaming24.payment.crypto.x402 import sign_payment_from_402

# Sign the payment
payload = sign_payment_from_402(
    response_402,
    private_key=os.environ["TEAMING24_WALLET_PRIVATE_KEY"]
)

# Resubmit with payment header
headers = {"X-PAYMENT": payload.model_dump_json()}
response = await client.post("/api/premium", headers=headers)
```

## Configuration

### Environment Variables

All x402-related environment variables use the `TEAMING24_` prefix:

```bash
# Wallet configuration
TEAMING24_WALLET_ADDRESS=0x...
TEAMING24_WALLET_PRIVATE_KEY=0x...
TEAMING24_WALLET_NETWORK=base-sepolia  # or "base" for mainnet

# RPC endpoint for blockchain interaction
TEAMING24_RPC_URL=https://sepolia.base.org

# Merchant address for receiving payments (server)
TEAMING24_MERCHANT_ADDRESS=0x...

# Optional: Custom facilitator
TEAMING24_FACILITATOR_URL=https://x402.org/facilitator
```

### Config File

Optional x402 config file (auto-loaded when present):

`teaming24/payment/config/x402.yaml`

```yaml
network:
  name: "base-sepolia"  # or "base" for mainnet

payment:
  scheme: "exact"
  timeout_seconds: 600

facilitator:
  url: "https://x402.org/facilitator"
  timeout: 30

merchant:
  default_description: "Payment required"

wallet:
  valid_hours: 1.0
```

## API Reference

### Server-side Functions

```python
from teaming24.payment.crypto.x402 import (
    create_requirements,
    require_payment,
    require_payment_choice,
    create_tiered_options,
)

# Create payment requirements
requirements = create_requirements(
    price="$0.50",           # USD amount
    pay_to="0x...",          # Recipient address
    resource="/api/v1/task", # Resource identifier
    description="Task fee",
    network="base-sepolia",  # Optional, from config
)

# Multiple payment options
raise require_payment_choice([
    create_requirements(price="$1.00", pay_to=ADDR, resource="/basic"),
    create_requirements(price="$5.00", pay_to=ADDR, resource="/premium"),
])

# Tiered pricing helper
options = create_tiered_options(
    base_price="$1.00",
    pay_to="0x...",
    resource="/generate",
    tiers=[
        {"multiplier": 1, "suffix": "basic", "description": "Basic"},
        {"multiplier": 3, "suffix": "premium", "description": "Premium"},
    ]
)
```

### Client-side Functions

```python
from teaming24.payment.crypto.x402 import (
    sign_payment,
    sign_payment_from_402,
)

# Sign from requirements directly
payload = sign_payment(
    requirements,
    private_key="0x...",
    valid_hours=1.0,
)

# Sign from 402 response
payload = sign_payment_from_402(
    response_402,
    private_key="0x...",
    requirement_index=0,  # Which option to select
)
```

### Settlement Functions

```python
from teaming24.payment.crypto.x402 import (
    verify_payment,
    settle_payment,
    process_and_settle,
)

# Verify signature
result = await verify_payment(payload, requirements)
if result.is_valid:
    print(f"Valid payment from {result.payer}")

# Settle on blockchain
settle_result = await settle_payment(payload, requirements)
if settle_result.success:
    print(f"Settled: {settle_result.transaction}")

# Combined verify + settle
result = await process_and_settle(payload, requirements)
```

### Utilities

```python
from teaming24.payment.crypto.x402 import (
    format_amount,
    extract_payer_address,
    paid_service,
)

# Format for display
print(format_amount(requirements))  # "1.50 USDC"

# Extract payer from payload
payer = extract_payer_address(payload)  # "0x..."

# Decorator for paid endpoints
@paid_service(
    price="$0.10",
    pay_to="0x...",
    description="Premium AI generation"
)
async def generate_premium(prompt: str):
    return await ai.generate(prompt)
```

## Error Handling

```python
from teaming24.payment.crypto.x402 import (
    X402Error,
    PaymentRequiredError,
    PaymentValidationError,
    PaymentSettlementError,
)

try:
    result = await process_and_settle(payload, requirements)
except PaymentValidationError as e:
    # Invalid signature, expired, etc.
    print(f"Validation failed: {e}")
except PaymentSettlementError as e:
    # Blockchain settlement failed
    print(f"Settlement failed: {e}")
except X402Error as e:
    # Other x402 errors
    print(f"Payment error: {e}")
```

## Networks

| Network | Chain ID | USDC Address |
|---------|----------|--------------|
| Base Sepolia | 84532 | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| Base Mainnet | 8453 | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

## Testing

Use Base Sepolia testnet for development:

1. Get testnet ETH from [Base Sepolia Faucet](https://www.coinbase.com/faucets/base-ethereum-goerli-faucet)
2. Get testnet USDC from [Circle Faucet](https://faucet.circle.com/)
3. Configure `TEAMING24_RPC_URL=https://sepolia.base.org`
