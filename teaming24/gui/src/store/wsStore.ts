/**
 * WebSocket client store for Teaming24.
 *
 * Provides a Zustand store that manages the WebSocket connection to the
 * backend /ws endpoint.  Supports the typed req/res/event protocol and
 * auto-reconnection with exponential backoff.
 *
 * Usage:
 *   const { connect, send, subscribe, status } = useWSStore()
 *   connect()
 *   const unsub = subscribe('task_step', (payload) => { ... })
 */

import { create } from 'zustand'
import { getApiBaseAbsolute } from '../utils/api'
import { debugLog, debugWarn } from '../utils/debug'
import { generateTempId } from '../utils/ids'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WSStatus = 'disconnected' | 'connecting' | 'connected'

interface WSRequest {
  type: 'req'
  id: string
  method: string
  params: Record<string, unknown>
}

interface WSResponse {
  type: 'res'
  id: string
  ok: boolean
  payload: Record<string, unknown>
}

interface WSEvent {
  type: 'event'
  event: string
  payload: Record<string, unknown>
  seq: number
}

type WSFrame = WSResponse | WSEvent

type EventHandler = (payload: Record<string, unknown>) => void

interface PendingRequest {
  resolve: (payload: Record<string, unknown>) => void
  reject: (err: Error) => void
  timer: ReturnType<typeof setTimeout>
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface WSStore {
  status: WSStatus
  clientId: string
  lastSeq: number

  connect: () => void
  disconnect: () => void
  send: (method: string, params?: Record<string, unknown>) => Promise<Record<string, unknown>>
  subscribe: (event: string, handler: EventHandler) => () => void
}

let _ws: WebSocket | null = null
let _pending: Map<string, PendingRequest> = new Map()
let _listeners: Map<string, Set<EventHandler>> = new Map()
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null
let _reconnectAttempt = 0
const MAX_RECONNECT_DELAY = 30_000
const REQUEST_TIMEOUT = 15_000

function _wsUrl(): string {
  const httpBase = getApiBaseAbsolute()
  return httpBase.replace(/^http/, 'ws') + '/ws'
}

export const useWSStore = create<WSStore>((set, get) => ({
  status: 'disconnected',
  clientId: '',
  lastSeq: 0,

  connect() {
    if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    set({ status: 'connecting' })
    const url = _wsUrl()
    debugLog('[WS] connecting to', url)

    const ws = new WebSocket(url)
    _ws = ws

    ws.onopen = () => {
      debugLog('[WS] connected, sending handshake')
      _reconnectAttempt = 0
      const id = generateTempId('ws')
      ws.send(JSON.stringify({ type: 'req', id, method: 'connect', params: {} }))
    }

    ws.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data) as WSFrame
        if (frame.type === 'res') {
          _handleResponse(frame as WSResponse, set)
        } else if (frame.type === 'event') {
          _handleEvent(frame as WSEvent, set)
        }
      } catch (err) {
        debugWarn('[WS] parse error', err)
      }
    }

    ws.onclose = () => {
      debugLog('[WS] disconnected')
      _ws = null
      set({ status: 'disconnected' })
      _rejectAllPending()
      _scheduleReconnect(get)
    }

    ws.onerror = (err) => {
      debugWarn('[WS] error', err)
    }
  },

  disconnect() {
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer)
      _reconnectTimer = null
    }
    _reconnectAttempt = 999 // prevent auto-reconnect
    if (_ws) {
      _ws.close()
      _ws = null
    }
    _rejectAllPending()
    set({ status: 'disconnected', clientId: '' })
  },

  send(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      if (!_ws || _ws.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'))
        return
      }
      const id = generateTempId('ws')
      const timer = setTimeout(() => {
        _pending.delete(id)
        reject(new Error(`WS request timeout: ${method}`))
      }, REQUEST_TIMEOUT)

      _pending.set(id, { resolve, reject, timer })

      const req: WSRequest = { type: 'req', id, method, params }
      _ws.send(JSON.stringify(req))
    })
  },

  subscribe(event: string, handler: EventHandler): () => void {
    if (!_listeners.has(event)) {
      _listeners.set(event, new Set())
    }
    _listeners.get(event)!.add(handler)
    return () => {
      _listeners.get(event)?.delete(handler)
    }
  },
}))

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _handleResponse(frame: WSResponse, set: (s: Partial<WSStore>) => void) {
  const pending = _pending.get(frame.id)
  if (pending) {
    clearTimeout(pending.timer)
    _pending.delete(frame.id)
    if (frame.ok) {
      // Handshake response
      if (frame.payload?.client_id) {
        set({ status: 'connected', clientId: frame.payload.client_id as string })
        debugLog('[WS] handshake ok, clientId=', frame.payload.client_id)
      }
      pending.resolve(frame.payload)
    } else {
      pending.reject(new Error((frame.payload?.error as string) || 'Request failed'))
    }
  }
}

function _handleEvent(frame: WSEvent, set: (s: Partial<WSStore>) => void) {
  set({ lastSeq: frame.seq })
  const handlers = _listeners.get(frame.event)
  if (handlers) {
    for (const h of handlers) {
      try {
        h(frame.payload)
      } catch (err) {
        debugWarn('[WS] event handler error', frame.event, err)
      }
    }
  }
  // Also dispatch to wildcard listeners
  const wildcardHandlers = _listeners.get('*')
  if (wildcardHandlers) {
    for (const h of wildcardHandlers) {
      try {
        h({ event: frame.event, ...frame.payload })
      } catch (err) {
        debugWarn('[WS] wildcard handler error', err)
      }
    }
  }
}

function _rejectAllPending() {
  for (const [, p] of _pending) {
    clearTimeout(p.timer)
    p.reject(new Error('WebSocket disconnected'))
  }
  _pending.clear()
}

function _scheduleReconnect(get: () => WSStore) {
  if (_reconnectAttempt > 50) return
  const delay = Math.min(1000 * Math.pow(1.5, _reconnectAttempt), MAX_RECONNECT_DELAY)
  _reconnectAttempt++
  debugLog(`[WS] reconnecting in ${Math.round(delay)}ms (attempt ${_reconnectAttempt})`)
  _reconnectTimer = setTimeout(() => {
    get().connect()
  }, delay)
}
