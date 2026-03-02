import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { getApiBaseAbsolute } from '../utils/api'
import { prefixedId } from '../utils/ids'
import { debugLog } from '../utils/debug'

const API_BASE = getApiBaseAbsolute()
/**
 * Represents a step in the agent's reasoning process.
 * Tracks agent name/role, action type, thought content, and step progress.
 */
export interface AgentStep {
  id: string
  agent: string
  /** Agent role for color-coding badges in the UI. */
  agentRole?: 'organizer' | 'coordinator' | 'worker' | 'remote'
  action: string
  /** Semantic action type for icon selection. */
  actionType?: 'thinking' | 'tool_call' | 'delegation' | 'observation'
  /** Step execution status for progress display. */
  status?: 'pending' | 'running' | 'done' | 'error'
  content: string
  /** Separate output text (result from tool/delegation). */
  output?: string
  /** Current step index (1-based) within this agent's execution. */
  stepIndex?: number
  /** Total expected steps for progress indicator. */
  totalSteps?: number
  timestamp: number
}

/** Attachment for multimodal output (files, images). */
export interface MessageAttachment {
  type: 'file' | 'image'
  filename: string
  filepath?: string
  /** API-relative URL for direct display/download */
  url?: string
  language?: string
  runCommand?: string
}

/**
 * Chat message with optional agent execution metadata.
 * Supports both simple chat and agent task execution.
 * attachments: multimodal output (files, images) from task results.
 */
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  taskId?: string
  steps?: AgentStep[]
  cost?: {
    inputTokens?: number
    outputTokens?: number
    totalTokens?: number
    duration?: number
    costUsd?: number
    x402Payment?: number
  }
  isTask?: boolean
  /** Multimodal output: files saved, images generated */
  attachments?: MessageAttachment[]
}

export interface PendingApproval {
  id: string
  taskId: string
  title: string
  description: string
  options: Array<{ id: string; label: string; style?: string }>
  type?: string
  metadata?: Record<string, unknown>
}

function normalizeMemoryText(value: unknown): string {
  return String(value ?? '')
    .replace(/\bundefined\b/gi, '')
    .replace(/\s+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function getMessageMemoryText(message: Message): string {
  const direct = normalizeMemoryText(message.content)
  if (direct) return direct
  const stepFallback = [...(message.steps || [])]
    .reverse()
    .map((step) => normalizeMemoryText(step.output || step.content || ''))
    .find(Boolean)
  return stepFallback || ''
}

export function buildSessionContextMessages(
  session: ChatSession | undefined,
  options?: { excludeMessageId?: string },
): Array<{ role: Message['role']; content: string }> {
  if (!session) return []
  return (session.messages || [])
    .filter((message) => message.id !== options?.excludeMessageId)
    .map((message) => ({
      role: message.role,
      content: getMessageMemoryText(message),
    }))
    .filter((message) => message.content)
}

export interface ChatSession {
  id: string
  title: string
  messages: Message[]
  createdAt: number
  updatedAt: number
  unreadCount: number
  /** Task ID for in-progress tasks — enables reconnect on page return */
  activeTaskId?: string | null
  /** The assistant message ID receiving streaming updates */
  activeMessageId?: string | null
  /** Pending human-in-the-loop approval (survives page reload via localStorage) */
  pendingApproval?: PendingApproval | null
}

interface ChatState {
  sessions: ChatSession[]
  activeSessionId: string | null
  isStreaming: boolean
  currentTaskId: string | null
  isLoadingSessions: boolean
  totalUnreadCount: number
  
  // Actions
  createSession: () => void
  deleteSession: (id: string) => void
  setActiveSession: (id: string) => void
  addMessage: (sessionId: string, message: Omit<Message, 'id' | 'timestamp'>) => string
  updateMessage: (sessionId: string, messageId: string, content: string) => void
  updateMessageMeta: (sessionId: string, messageId: string, meta: Partial<Message>) => void
  addStepToMessage: (sessionId: string, messageId: string, step: Omit<AgentStep, 'id' | 'timestamp'>) => void
  setStreaming: (isStreaming: boolean) => void
  setCurrentTaskId: (taskId: string | null) => void
  setSessionTask: (sessionId: string, taskId: string | null, messageId?: string | null) => void
  setSessionApproval: (sessionId: string, approval: PendingApproval | null) => void
  _applyApprovalFromSync: (sessionId: string, approval: PendingApproval | null) => void
  updateSessionTitle: (sessionId: string, title: string) => void
  markSessionRead: (sessionId: string) => void
  
  truncateMessagesFrom: (sessionId: string, messageId: string) => void

  // Cross-store sync
  onTaskCompleted: (taskId: string) => void

  // Database sync actions
  loadSessionsFromDB: () => Promise<void>
  syncSessionToDB: (sessionId: string) => Promise<void>
  syncMessageToDB: (sessionId: string, message: Message) => Promise<void>
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      sessions: [],
      activeSessionId: null,
      isStreaming: false,
      currentTaskId: null,
      isLoadingSessions: false,
      totalUnreadCount: 0,

      createSession: () => {
        const newSession: ChatSession = {
          id: prefixedId('sess', 12),
          title: 'New Chat',
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
          unreadCount: 0,
        }
        set((state) => ({
          sessions: [newSession, ...state.sessions],
          activeSessionId: newSession.id,
        }))
      },

      deleteSession: (id: string) => {
        set((state) => {
          const newSessions = state.sessions.filter((s) => s.id !== id)
          let newActiveId = state.activeSessionId
          if (state.activeSessionId === id) {
            newActiveId = newSessions.length > 0 ? newSessions[0].id : null
          }
          // Recalculate totalUnreadCount after deletion
          const totalUnread = newSessions.reduce((sum, s) => sum + (s.unreadCount || 0), 0)
          return {
            sessions: newSessions,
            activeSessionId: newActiveId,
            totalUnreadCount: totalUnread,
          }
        })
        // Also delete from backend database
        fetch(`${API_BASE}/api/db/chat/sessions/${id}`, { method: 'DELETE' }).catch((err) =>
          console.error('[ChatStore] Failed to delete session from DB:', err)
        )
      },

      setActiveSession: (id: string) => {
        set({ activeSessionId: id })
        // Auto mark session as read when switching to it
        get().markSessionRead(id)
      },

      addMessage: (sessionId: string, message: Omit<Message, 'id' | 'timestamp'>) => {
        const newMessage: Message = {
          ...message,
          id: prefixedId('msg', 12),
          timestamp: Date.now(),
        }
        const isActive = get().activeSessionId === sessionId
        set((state) => {
          const updatedSessions = state.sessions.map((session) =>
            session.id === sessionId
              ? {
                  ...session,
                  messages: [...session.messages, newMessage],
                  updatedAt: Date.now(),
                  // Increment unread if not the active session and it's an assistant message
                  unreadCount: !isActive && message.role === 'assistant'
                    ? (session.unreadCount || 0) + 1
                    : (session.unreadCount || 0),
                  // Auto-update title from first user message
                  title: session.messages.length === 0 && message.role === 'user'
                    ? (() => {
                        const c = typeof message.content === 'string' ? message.content : String(message.content ?? '')
                        return c.slice(0, 30) + (c.length > 30 ? '...' : '')
                      })()
                    : session.title,
                }
              : session
          )
          // Recalculate total unread
          const totalUnread = updatedSessions.reduce((sum, s) => sum + (s.unreadCount || 0), 0)
          return { sessions: updatedSessions, totalUnreadCount: totalUnread }
        })
        return newMessage.id
      },

      updateMessage: (sessionId: string, messageId: string, content: string) => {
        set((state) => ({
          sessions: state.sessions.map((session) =>
            session.id === sessionId
              ? {
                  ...session,
                  messages: session.messages.map((msg) =>
                    msg.id === messageId ? { ...msg, content } : msg
                  ),
                  updatedAt: Date.now(),
                }
              : session
          ),
        }))
      },

      updateMessageMeta: (sessionId: string, messageId: string, meta: Partial<Message>) => {
        set((state) => ({
          sessions: state.sessions.map((session) =>
            session.id === sessionId
              ? {
                  ...session,
                  messages: session.messages.map((msg) =>
                    msg.id === messageId ? { ...msg, ...meta } : msg
                  ),
                  updatedAt: Date.now(),
                }
              : session
          ),
        }))
      },

      addStepToMessage: (sessionId: string, messageId: string, step: Omit<AgentStep, 'id' | 'timestamp'>) => {
        const newStep: AgentStep = {
          ...step,
          id: prefixedId('step', 12),
          timestamp: Date.now(),
        }
        set((state) => {
          const session = state.sessions.find((s) => s.id === sessionId)
          const msg = session?.messages.find((m) => m.id === messageId)
          const steps = msg?.steps ?? []
          // Dedupe: avoid adding duplicate approval_request steps (prevents repeated thinking output)
          if (step.action === 'approval_request' && steps.some((s) => s.action === 'approval_request')) {
            return state
          }
          return {
            sessions: state.sessions.map((s) =>
              s.id === sessionId
                ? {
                    ...s,
                    messages: s.messages.map((m) =>
                      m.id === messageId
                        ? { ...m, steps: [...(m.steps || []), newStep] }
                        : m
                    ),
                    updatedAt: Date.now(),
                  }
                : s
            ),
          }
        })
      },

      setStreaming: (isStreaming: boolean) => {
        set({ isStreaming })
      },

      setCurrentTaskId: (taskId: string | null) => {
        set({ currentTaskId: taskId })
      },

      setSessionTask: (sessionId: string, taskId: string | null, messageId?: string | null) => {
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === sessionId
              ? { ...s, activeTaskId: taskId, activeMessageId: messageId ?? null, updatedAt: Date.now() }
              : s
          ),
        }))
      },

      setSessionApproval: (sessionId: string, approval: PendingApproval | null) => {
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === sessionId ? { ...s, pendingApproval: approval } : s
          ),
        }))
        // Sync approval across tabs so other windows show the popup
        try {
          const bc = new BroadcastChannel('teaming24-approval-sync')
          bc.postMessage({ type: 'approval', sessionId, approval })
          bc.close()
        } catch { /* BroadcastChannel not supported */ }
      },

      /** Internal: apply approval from another tab (no broadcast to avoid loop) */
      _applyApprovalFromSync: (sessionId: string, approval: PendingApproval | null) => {
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === sessionId ? { ...s, pendingApproval: approval } : s
          ),
        }))
      },

      markSessionRead: (sessionId: string) => {
        set((state) => {
          const session = state.sessions.find(s => s.id === sessionId)
          if (!session || (session.unreadCount || 0) === 0) return state
          const updatedSessions = state.sessions.map(s =>
            s.id === sessionId ? { ...s, unreadCount: 0 } : s
          )
          const totalUnread = updatedSessions.reduce((sum, s) => sum + (s.unreadCount || 0), 0)
          return { sessions: updatedSessions, totalUnreadCount: totalUnread }
        })
      },

      truncateMessagesFrom: (sessionId: string, messageId: string) => {
        set(state => ({
          sessions: state.sessions.map(s => {
            if (s.id !== sessionId) return s
            const idx = s.messages.findIndex(m => m.id === messageId)
            if (idx === -1) return s
            return {
              ...s,
              messages: s.messages.slice(0, idx),
              updatedAt: Date.now(),
            }
          })
        }))
      },

      onTaskCompleted: (taskId: string) => {
        // Collect affected session IDs BEFORE the state update
        const affectedIds = get().sessions
          .filter((s) => s.activeTaskId === taskId)
          .map((s) => s.id)

        if (affectedIds.length === 0) return

        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.activeTaskId === taskId
              ? { ...s, activeTaskId: undefined, activeMessageId: undefined }
              : s
          ),
        }))

        // Persist the cleared activeTaskId to the backend DB so a fresh-browser
        // reload (bypassing localStorage) doesn't restore the stale task ref.
        affectedIds.forEach((sid) => get().syncSessionToDB(sid))
      },

      updateSessionTitle: (sessionId: string, title: string) => {
        set((state) => ({
          sessions: state.sessions.map((session) =>
            session.id === sessionId
              ? { ...session, title }
              : session
          ),
        }))
      },

      // Database sync actions
      loadSessionsFromDB: async () => {
        const isFirstLoad = get().sessions.length === 0
        if (isFirstLoad) set({ isLoadingSessions: true })
        try {
          const response = await fetch(`${API_BASE}/api/db/chat/sessions`)
          if (!response.ok) throw new Error('Failed to fetch sessions')
          const data = await response.json()
          
          const dbSessions: ChatSession[] = await Promise.all(
            (data.sessions || []).map(async (s: Record<string, unknown>) => {
              const msgResponse = await fetch(`${API_BASE}/api/db/chat/sessions/${s.id}/messages`)
              const msgData = msgResponse.ok ? await msgResponse.json() : { messages: [] }
              const messages: Message[] = (msgData.messages || []).map((m: Record<string, unknown>) => ({
                id: m.id as string,
                role: m.role as 'user' | 'assistant' | 'system',
                content: m.content as string,
                timestamp: (() => { const ts = typeof m.timestamp === 'number' && m.timestamp > 0 ? m.timestamp : 0; return ts > 0 ? (ts > 1e12 ? ts : ts * 1000) : Date.now() })(),
                taskId: m.task_id as string | undefined,
                steps: m.steps as AgentStep[] | undefined,
                cost: m.cost as Message['cost'] | undefined,
                isTask: m.is_task as boolean | undefined,
              }))
              
              return {
                id: s.id as string,
                title: s.title as string || 'New Chat',
                messages,
                createdAt: typeof s.created_at === 'number' && s.created_at > 0 ? (s.created_at > 1e12 ? s.created_at : s.created_at * 1000) : Date.now(),
                updatedAt: typeof s.updated_at === 'number' && s.updated_at > 0 ? (s.updated_at > 1e12 ? s.updated_at : s.updated_at * 1000) : Date.now(),
                unreadCount: 0,
                activeTaskId: (s.active_task_id as string) || undefined,
                activeMessageId: (s.active_message_id as string) || undefined,
              } as ChatSession
            })
          )
          
          // Merge with local sessions (prefer local if newer)
          const localSessions = get().sessions
          const mergedSessions: ChatSession[] = []
          const seenIds = new Set<string>()
          
          for (const dbSession of dbSessions) {
            const localSession = localSessions.find(s => s.id === dbSession.id)
            if (localSession && localSession.updatedAt > dbSession.updatedAt) {
              mergedSessions.push(localSession)
            } else {
              // Preserve activeTaskId/activeMessageId from local if DB doesn't have them
              const merged = { ...dbSession }
              if (localSession) {
                if (!merged.activeTaskId && localSession.activeTaskId)
                  merged.activeTaskId = localSession.activeTaskId
                if (!merged.activeMessageId && localSession.activeMessageId)
                  merged.activeMessageId = localSession.activeMessageId
              }
              mergedSessions.push(merged)
            }
            seenIds.add(dbSession.id)
          }
          
          for (const localSession of localSessions) {
            if (!seenIds.has(localSession.id)) {
              mergedSessions.push(localSession)
            }
          }
          
          mergedSessions.sort((a, b) => b.updatedAt - a.updatedAt)

          // Skip update if session list is identical (avoids unnecessary re-renders)
          const prev = localSessions
          const changed =
            prev.length !== mergedSessions.length ||
            mergedSessions.some((s, i) => {
              const p = prev[i]
              return !p || p.id !== s.id || p.updatedAt !== s.updatedAt ||
                p.messages.length !== s.messages.length || p.title !== s.title
            })

          if (changed) {
            const totalUnread = mergedSessions.reduce((sum, s) => sum + (s.unreadCount || 0), 0)
            set({ sessions: mergedSessions, totalUnreadCount: totalUnread })
            debugLog(`[ChatStore] Synced ${dbSessions.length} sessions from database`)
          }
          if (isFirstLoad) set({ isLoadingSessions: false })
        } catch (error) {
          console.error('[ChatStore] Failed to load sessions from DB:', error)
          if (isFirstLoad) set({ isLoadingSessions: false })
        }
      },

      syncSessionToDB: async (sessionId: string) => {
        const session = get().sessions.find(s => s.id === sessionId)
        if (!session) return
        
        try {
          await fetch(`${API_BASE}/api/db/chat/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id: session.id,
              title: session.title,
              mode: 'task',
              created_at: session.createdAt / 1000,
              updated_at: session.updatedAt / 1000,
              active_task_id: session.activeTaskId || null,
              active_message_id: session.activeMessageId || null,
            }),
          })
          debugLog(`[ChatStore] Session ${sessionId} synced to database`)
        } catch (error) {
          console.error('[ChatStore] Failed to sync session to DB:', error)
        }
      },

      syncMessageToDB: async (sessionId: string, message: Message) => {
        try {
          await fetch(`${API_BASE}/api/db/chat/sessions/${sessionId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id: message.id,
              role: message.role,
              content: message.content,
              timestamp: message.timestamp / 1000,
              task_id: message.taskId,
              steps: message.steps,
              cost: message.cost,
              is_task: message.isTask,
            }),
          })
          debugLog(`[ChatStore] Message ${message.id} synced to database`)
        } catch (error) {
          console.error('[ChatStore] Failed to sync message to DB:', error)
        }
      },
    }),
    {
      name: 'teaming24-chat-storage',
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
      }),
    }
  )
)
