import { ORGANIZER_ID } from '../utils/ids'

/**
 * Frontend-normalized memory status contract.
 *
 * The backend exposes snake_case fields. This module owns the translation to
 * camelCase so the UI can swap transport details without touching components.
 */
export interface AgentMemoryStatus {
  agentId: string
  entryCount: number
  sessionEntryCount: number
  totalChars: number
  maxChars: number
  totalTokens: number
  maxTokens: number
  remainingChars: number
  usageRatio: number
  isCompacting: boolean
  recentlyCompacted: boolean
  lastCompactedAt: number
  lastSavedAt: number
}

interface RawMemoryStatusResponse {
  agent_id?: unknown
  entry_count?: unknown
  session_entry_count?: unknown
  total_chars?: unknown
  max_chars?: unknown
  total_tokens?: unknown
  max_tokens?: unknown
  remaining_chars?: unknown
  usage_ratio?: unknown
  is_compacting?: unknown
  recently_compacted?: unknown
  last_compacted_at?: unknown
  last_saved_at?: unknown
}

function toNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export function normalizeAgentMemoryStatus(raw: RawMemoryStatusResponse): AgentMemoryStatus {
  return {
    agentId: String(raw.agent_id || ORGANIZER_ID),
    entryCount: toNumber(raw.entry_count),
    sessionEntryCount: toNumber(raw.session_entry_count),
    totalChars: toNumber(raw.total_chars),
    maxChars: toNumber(raw.max_chars),
    totalTokens: toNumber(raw.total_tokens),
    maxTokens: toNumber(raw.max_tokens),
    remainingChars: toNumber(raw.remaining_chars),
    usageRatio: Math.max(0, Math.min(1, toNumber(raw.usage_ratio))),
    isCompacting: Boolean(raw.is_compacting),
    recentlyCompacted: Boolean(raw.recently_compacted),
    lastCompactedAt: toNumber(raw.last_compacted_at) * 1000,
    lastSavedAt: toNumber(raw.last_saved_at) * 1000,
  }
}

export async function fetchAgentMemoryStatus(
  getApiUrl: (path: string) => string,
  options: { agentId?: string; sessionId?: string | null } = {},
): Promise<AgentMemoryStatus> {
  const agentId = options.agentId || ORGANIZER_ID
  const params = new URLSearchParams({ agent_id: agentId })
  if (options.sessionId) params.set('session_id', options.sessionId)
  const response = await fetch(getApiUrl(`/memory/status?${params.toString()}`))
  if (!response.ok) {
    throw new Error(`memory status HTTP ${response.status}`)
  }
  return normalizeAgentMemoryStatus((await response.json()) as RawMemoryStatusResponse)
}
