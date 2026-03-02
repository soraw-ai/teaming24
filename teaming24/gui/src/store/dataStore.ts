/**
 * Unified Data Store — Centralized data initialization for Teaming24 GUI.
 *
 * All data comes from the backend via REST + SSE. No demo/fake data.
 */

import { useEffect } from 'react'
import { create } from 'zustand'
import { useWalletStore } from './walletStore'

interface DataState {
  initialized: boolean
  loading: boolean
  error: string | null
  lastSync: number | null

  initialize: () => Promise<void>
  refresh: () => Promise<void>
  reset: () => void
}

export const useDataStore = create<DataState>()((set, get) => ({
  initialized: false,
  loading: false,
  error: null,
  lastSync: null,

  initialize: async () => {
    if (get().initialized) return

    set({ loading: true, error: null })

    try {
      // Fetch wallet data from backend
      await useWalletStore.getState().fetchAll()

      set({
        initialized: true,
        loading: false,
        lastSync: Date.now(),
      })
    } catch (error) {
      set({
        loading: false,
        error:
          error instanceof Error
            ? error.message
            : 'Failed to initialize data',
      })
    }
  },

  refresh: async () => {
    set({ loading: true, error: null })

    try {
      await useWalletStore.getState().fetchAll()

      set({
        loading: false,
        lastSync: Date.now(),
      })
    } catch (error) {
      set({
        loading: false,
        error:
          error instanceof Error ? error.message : 'Failed to refresh data',
      })
    }
  },

  reset: () => {
    set({
      initialized: false,
      loading: false,
      error: null,
      lastSync: null,
    })
  },
}))

/**
 * Hook to initialize data on component mount.
 */
export function useDataInitialization() {
  const { initialized, loading, error } = useDataStore()

  useEffect(() => {
    if (!initialized && !loading) {
      useDataStore.getState().initialize()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return { initialized, loading, error }
}
