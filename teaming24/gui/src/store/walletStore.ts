/**
 * Wallet Store — Manages local wallet for x402 payments.
 *
 * All data is fetched from the backend (no demo/fake data).
 * Mock mode: backend returns 100 ETH initial balance.
 * Testnet/Mainnet: backend queries on-chain balance.
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { getApiBase } from '../utils/api'
import { initPaymentToken } from '../config/payment'

export interface Transaction {
  id: string
  timestamp: number
  type: 'expense' | 'income' | 'topup'
  amount: number
  taskId?: string
  taskName?: string
  description: string
  txHash?: string
  mode?: string
  network?: string
  payer?: string
  payee?: string
}

export interface WalletConfig {
  address: string
  isConfigured: boolean
  network: 'base' | 'base-sepolia'
}

interface WalletSummary {
  totalIncome: number
  totalExpenses: number
  totalTopups: number
  netProfit: number
  lastTaskExpense: number
  lastTaskIncome: number
  lastTaskId: string | null
  lastTaskName: string | null
}

interface WalletState {
  // Wallet config
  config: WalletConfig

  // Balance and token
  balance: number
  tokenSymbol: string
  isMock: boolean
  paymentMode: string
  isLoadingBalance: boolean

  // Summary stats (from backend)
  summary: WalletSummary

  // Transaction history (from backend)
  transactions: Transaction[]

  // Actions
  setConfig: (config: Partial<WalletConfig>) => void
  setBalance: (amount: number) => void
  setLoadingBalance: (loading: boolean) => void

  // Record a single transaction (called from SSE event)
  addTransaction: (tx: Transaction, newBalance?: number) => void

  // Stats helpers (computed from store)
  getTotalExpenses: () => number
  getTotalIncome: () => number
  getNetProfit: () => number

  // API calls to backend
  fetchBalance: () => Promise<void>
  fetchConfig: () => Promise<void>
  fetchTransactions: () => Promise<void>
  fetchSummary: () => Promise<void>
  fetchAll: () => Promise<void>

  // Reset
  reset: () => void
}

const initialConfig: WalletConfig = {
  address: '',
  isConfigured: false,
  network: 'base-sepolia',
}

const initialSummary: WalletSummary = {
  totalIncome: 0,
  totalExpenses: 0,
  totalTopups: 0,
  netProfit: 0,
  lastTaskExpense: 0,
  lastTaskIncome: 0,
  lastTaskId: null,
  lastTaskName: null,
}

export const useWalletStore = create<WalletState>()(
  persist(
    (set, get) => ({
      config: initialConfig,
      balance: 0,
      tokenSymbol: 'ETH',
      isMock: true,
      paymentMode: 'mock',
      isLoadingBalance: false,
      summary: initialSummary,
      transactions: [],

      setConfig: (config) =>
        set((state) => ({
          config: { ...state.config, ...config },
        })),

      setBalance: (amount) => set({ balance: amount }),

      setLoadingBalance: (loading) => set({ isLoadingBalance: loading }),

      addTransaction: (tx, newBalance) =>
        set((state) => {
          const updated: Partial<WalletState> = {
            transactions: [tx, ...state.transactions].slice(0, 200),
          }
          if (typeof newBalance === 'number') {
            updated.balance = newBalance
          }
          // Update summary stats
          const allTxs = updated.transactions as Transaction[]
          updated.summary = {
            totalIncome: allTxs
              .filter((t) => t.type === 'income')
              .reduce((s, t) => s + t.amount, 0),
            totalExpenses: allTxs
              .filter((t) => t.type === 'expense')
              .reduce((s, t) => s + t.amount, 0),
            totalTopups: allTxs
              .filter((t) => t.type === 'topup')
              .reduce((s, t) => s + t.amount, 0),
            netProfit: 0,
            lastTaskExpense: state.summary.lastTaskExpense,
            lastTaskIncome: state.summary.lastTaskIncome,
            lastTaskId: tx.taskId || state.summary.lastTaskId,
            lastTaskName: tx.taskName || state.summary.lastTaskName,
          }
          if (tx.type === 'expense') {
            updated.summary.lastTaskExpense = tx.amount
          } else if (tx.type === 'income') {
            updated.summary.lastTaskIncome = tx.amount
          }
          updated.summary.netProfit =
            updated.summary.totalIncome - updated.summary.totalExpenses
          return updated
        }),

      getTotalExpenses: () => get().summary.totalExpenses,

      getTotalIncome: () => get().summary.totalIncome,

      getNetProfit: () => get().summary.netProfit,

      // ------------------------------------------------------------------
      // Backend API calls
      // ------------------------------------------------------------------

      fetchBalance: async () => {
        set({ isLoadingBalance: true })
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/wallet/balance`)
          if (res.ok) {
            const data = await res.json()
            const sym = data.currency as string | undefined
            if (sym) initPaymentToken(sym)
            set({
              balance: data.balance ?? 0,
              tokenSymbol: sym ?? 'ETH',
              isMock: data.is_mock ?? true,
              paymentMode: data.mode ?? 'mock',
            })
          } else {
            console.error(`[Wallet] fetchBalance failed: HTTP ${res.status}`)
          }
        } catch (err) {
          console.error('[Wallet] fetchBalance error:', err)
        } finally {
          set({ isLoadingBalance: false })
        }
      },

      fetchConfig: async () => {
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/wallet/config`)
          if (res.ok) {
            const data = await res.json()
            set({
              config: {
                address: data.address || '',
                isConfigured: data.is_configured || false,
                network: data.network || 'base-sepolia',
              },
            })
          } else {
            console.error(`[Wallet] fetchConfig failed: HTTP ${res.status}`)
          }
        } catch (err) {
          console.error('[Wallet] fetchConfig error:', err)
        }
      },

      fetchTransactions: async () => {
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/wallet/transactions?limit=100`)
          if (res.ok) {
            const data = await res.json()
            const txs: Transaction[] = (data.transactions || []).map(
              (t: any) => ({
                id: t.id,
                timestamp: t.timestamp,
                type: t.type,
                amount: t.amount,
                taskId: t.task_id,
                taskName: t.task_name,
                description: t.description,
                txHash: t.tx_hash,
                mode: t.mode,
                network: t.network,
                payer: t.payer,
                payee: t.payee,
              })
            )
            set({ transactions: txs })
          } else {
            console.error(`[Wallet] fetchTransactions failed: HTTP ${res.status}`)
          }
        } catch (err) {
          console.error('[Wallet] fetchTransactions error:', err)
        }
      },

      fetchSummary: async () => {
        try {
          const apiBase = getApiBase()
          const res = await fetch(`${apiBase}/api/wallet/summary`)
          if (res.ok) {
            const data = await res.json()
            set({
              balance: data.balance ?? get().balance,
              summary: {
                totalIncome: data.total_income ?? 0,
                totalExpenses: data.total_expenses ?? 0,
                totalTopups: data.total_topups ?? 0,
                netProfit: data.net_profit ?? 0,
                lastTaskExpense: data.last_task_expense ?? 0,
                lastTaskIncome: data.last_task_income ?? 0,
                lastTaskId: data.last_task_id ?? null,
                lastTaskName: data.last_task_name ?? null,
              },
            })
          } else {
            console.error(`[Wallet] fetchSummary failed: HTTP ${res.status}`)
          }
        } catch (err) {
          console.error('[Wallet] fetchSummary error:', err)
        }
      },

      fetchAll: async () => {
        const state = get()
        await Promise.all([
          state.fetchConfig(),
          state.fetchBalance(),
          state.fetchTransactions(),
          state.fetchSummary(),
        ])
      },

      reset: () =>
        set({
          config: initialConfig,
          balance: 0,
          tokenSymbol: 'ETH',
          isMock: true,
          paymentMode: 'mock',
          summary: initialSummary,
          transactions: [],
        }),
    }),
    {
      name: 'teaming24-wallet',
      // Only persist minimal data; real data comes from backend
      partialize: (state) => ({
        config: state.config,
      }),
    }
  )
)

// Fetch wallet data from backend on startup
if (typeof window !== 'undefined') {
  setTimeout(() => {
    useWalletStore.getState().fetchAll()
  }, 800)
}
