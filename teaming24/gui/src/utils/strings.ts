/**
 * Shared string utilities.
 */

/**
 * Truncate wallet/eth address to "0x1234...5678" (6 + 4 chars).
 * @param addr - Full address
 * @param emptyFallback - Returned when addr is falsy
 */
export function truncateWalletAddress(addr: string, emptyFallback: string = ''): string {
  if (!addr) return emptyFallback
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`
}

/**
 * Truncate long ID (AN id, agent id) to "12345678...123456" (8 + 6 chars).
 * Returns as-is if length <= maxLen.
 * @param id - Full ID
 * @param maxLen - Max length before truncation (default 16)
 */
export function truncateId(id: string | undefined, maxLen: number = 16): string | null {
  if (!id) return null
  if (id.length <= maxLen) return id
  return `${id.slice(0, 8)}...${id.slice(-6)}`
}
