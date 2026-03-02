/**
 * Payment token configuration — single source of truth for the frontend.
 *
 * Defaults are set here. At app init, `initPaymentToken()` is called with
 * values fetched from the backend `/api/wallet/balance` response, so the
 * symbol and addresses automatically reflect whatever is set in teaming24.yaml
 * (`payment.token_symbol` / `payment.settings.default_asset`).
 *
 * Usage:
 *   import { getPaymentTokenSymbol, getPaymentTokenAddresses } from '../config/payment'
 */

let _symbol = 'ETH'
let _addresses: Record<string, string> = {
  'base-sepolia': '0x4182528b6660B9c0875c6e94260A2E425F00797f',
  'base': '0x4182528b6660B9c0875c6e94260A2E425F00797f',
}

/** Current payment token symbol (e.g. "ETH", "USDC"). */
export function getPaymentTokenSymbol(): string {
  return _symbol
}

/** Contract addresses per network. */
export function getPaymentTokenAddresses(): Record<string, string> {
  return _addresses
}

/**
 * Initialize payment token config from API response.
 * Called once at app startup by walletStore.fetchBalance().
 */
export function initPaymentToken(
  symbol: string,
  addresses?: Record<string, string>,
): void {
  if (symbol) _symbol = symbol
  if (addresses && Object.keys(addresses).length > 0) _addresses = addresses
}
