import { create } from 'zustand'

export interface UiErrorPayload {
  id: string
  title: string
  message: string
  source?: string
  details?: string
  timestamp: number
}

interface UiStoreState {
  activeError: UiErrorPayload | null
  showError: (payload: Omit<UiErrorPayload, 'id' | 'timestamp'>) => UiErrorPayload
  clearError: () => void
}

function randomId(): string {
  return `uierr-${Math.random().toString(16).slice(2, 10)}`
}

export const useUiStore = create<UiStoreState>()((set) => ({
  activeError: null,
  showError: (payload) => {
    const fullPayload: UiErrorPayload = {
      ...payload,
      id: randomId(),
      timestamp: Date.now(),
    }
    set({ activeError: fullPayload })
    return fullPayload
  },
  clearError: () => set({ activeError: null }),
}))
