import { useEffect, useRef } from 'react'
import { useNetworkStore } from '../../store/networkStore'
import { getApiBase } from '../../utils/api'

export default function NetworkEventBridge() {
  const handleSSEMessage = useNetworkStore(s => s.handleSSEMessage)
  const syncPeersFromBackend = useNetworkStore(s => s.syncPeersFromBackend)
  const eventSourceRef = useRef<EventSource | null>(null)

  // On mount (page load / new tab), sync live peer state from backend
  // so previously connected nodes appear even after a browser refresh.
  useEffect(() => {
    syncPeersFromBackend()
  }, [syncPeersFromBackend])

  useEffect(() => {
    const apiBase = getApiBase()
    const url = `${apiBase}/api/network/events`

    const es = new EventSource(url)
    eventSourceRef.current = es

    es.onmessage = (evt) => {
      try {
        const parsed = JSON.parse(evt.data)
        if (parsed && parsed.type) {
          handleSSEMessage(parsed)
        }
      } catch (e) {
        console.debug('Invalid SSE frame in NetworkEventBridge:', e);
      }
    }

    es.onerror = () => {
      // EventSource will auto-reconnect; keep the latest instance alive.
    }

    return () => {
      es.close()
      eventSourceRef.current = null
    }
  }, [handleSSEMessage])

  return null
}

