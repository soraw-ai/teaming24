/**
 * WalletCard — Dashboard widget showing wallet balance, income/expense,
 * and recent transactions. All data comes from the backend via walletStore.
 */

import { useEffect } from 'react'
import {
  WalletIcon,
  ArrowTrendingUpIcon,
  ArrowTrendingDownIcon,
  ArrowPathIcon,
  PlusCircleIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useWalletStore, type Transaction } from '../../store/walletStore'
import { formatCurrencyUSDC } from '../../utils/format'
import { truncateWalletAddress } from '../../utils/strings'

interface WalletCardProps {
  compact?: boolean
}

export default function WalletCard({ compact = false }: WalletCardProps) {
  const {
    config,
    balance,
    isMock,
    paymentMode,
    isLoadingBalance,
    summary,
    transactions,
    fetchAll,
  } = useWalletStore()

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  const recentTransactions = transactions.slice(0, compact ? 3 : 5)

  if (compact) {
    return (
      <div className="p-3 rounded-lg bg-dark-bg border border-dark-border">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <WalletIcon className="w-4 h-4 text-primary-400" />
            <span className="text-xs text-gray-500 font-medium">Wallet</span>
            {isMock && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400 font-medium">
                MOCK
              </span>
            )}
          </div>
          <span className="text-lg font-bold text-white">
            {isLoadingBalance ? '...' : formatCurrencyUSDC(balance)}
          </span>
        </div>
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-1 text-red-400">
            <ArrowTrendingDownIcon className="w-3 h-3" />
            <span>-{formatCurrencyUSDC(summary.lastTaskExpense)}</span>
          </div>
          <div className="flex items-center gap-1 text-green-400">
            <ArrowTrendingUpIcon className="w-3 h-3" />
            <span>+{formatCurrencyUSDC(summary.lastTaskIncome)}</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-dark-border bg-gradient-to-r from-primary-500/10 to-purple-500/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-primary-500/20 flex items-center justify-center">
              <WalletIcon className="w-5 h-5 text-primary-400" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <p className="text-xs text-gray-500">x402 Wallet Balance</p>
                {isMock && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400 font-medium">
                    MOCK
                  </span>
                )}
              </div>
              <p className="text-2xl font-bold text-white">
                {isLoadingBalance ? (
                  <span className="flex items-center gap-2">
                    <ArrowPathIcon className="w-5 h-5 animate-spin" />
                  </span>
                ) : (
                  formatCurrencyUSDC(balance)
                )}
              </p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs text-gray-500">Net Profit</p>
            <p
              className={clsx(
                'text-lg font-semibold',
                summary.netProfit >= 0 ? 'text-green-400' : 'text-red-400'
              )}
            >
              {summary.netProfit >= 0 ? '+' : ''}
              {formatCurrencyUSDC(summary.netProfit)}
            </p>
          </div>
        </div>

        {/* Wallet Address + Refresh */}
        <div className="mt-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Address:</span>
            <span
              className={clsx(
                'text-xs font-mono',
                config.isConfigured ? 'text-gray-400' : 'text-orange-400'
              )}
            >
              {truncateWalletAddress(config.address, 'Not configured')}
            </span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-dark-bg text-gray-500">
              {paymentMode === 'mock'
                ? 'Mock'
                : config.network === 'base'
                  ? 'Base'
                  : 'Base Sepolia'}
            </span>
          </div>
          <button
            onClick={() => fetchAll()}
            className="p-1.5 hover:bg-dark-hover rounded-lg transition-colors"
            title="Refresh wallet data"
          >
            <ArrowPathIcon
              className={clsx(
                'w-4 h-4 text-gray-500 hover:text-gray-300',
                isLoadingBalance && 'animate-spin'
              )}
            />
          </button>
        </div>
      </div>

      {/* Last Task Stats */}
      <div className="p-4 border-b border-dark-border">
        <p className="text-xs text-gray-500 mb-3">
          Last Task: {summary.lastTaskName || 'N/A'}
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
            <div className="flex items-center gap-2 mb-1">
              <ArrowTrendingDownIcon className="w-4 h-4 text-red-400" />
              <span className="text-xs text-red-400">Spent</span>
            </div>
            <p className="text-xl font-bold text-red-400">
              {formatCurrencyUSDC(summary.lastTaskExpense)}
            </p>
          </div>
          <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/20">
            <div className="flex items-center gap-2 mb-1">
              <ArrowTrendingUpIcon className="w-4 h-4 text-green-400" />
              <span className="text-xs text-green-400">Earned</span>
            </div>
            <p className="text-xl font-bold text-green-400">
              {formatCurrencyUSDC(summary.lastTaskIncome)}
            </p>
          </div>
        </div>
      </div>

      {/* Summary */}
      <div className="p-4 border-b border-dark-border">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-gray-500">Total Expenses</p>
            <p className="text-red-400 font-medium">
              {formatCurrencyUSDC(summary.totalExpenses)}
            </p>
          </div>
          <div>
            <p className="text-gray-500">Total Income</p>
            <p className="text-green-400 font-medium">
              {formatCurrencyUSDC(summary.totalIncome)}
            </p>
          </div>
        </div>
      </div>

      {/* Recent Transactions */}
      <div className="p-4">
        <p className="text-xs text-gray-500 font-medium mb-3">
          Recent Transactions
        </p>
        <div className="space-y-2">
          {recentTransactions.length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-4">
              No transactions yet
            </p>
          ) : (
            recentTransactions.map((tx) => (
              <TransactionRow key={tx.id} transaction={tx} />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function TransactionRow({ transaction }: { transaction: Transaction }) {
  const isExpense = transaction.type === 'expense'
  const isTopup = transaction.type === 'topup'

  const formatTime = (timestamp: number) => {
    const diff = Date.now() - timestamp
    if (diff < 60000) return 'just now'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
    return `${Math.floor(diff / 86400000)}d ago`
  }

  return (
    <div className="flex items-center justify-between p-2 rounded-lg hover:bg-dark-hover/50 transition-colors">
      <div className="flex items-center gap-3">
        <div
          className={clsx(
            'w-8 h-8 rounded-full flex items-center justify-center',
            isTopup
              ? 'bg-primary-500/20'
              : isExpense
                ? 'bg-red-500/20'
                : 'bg-green-500/20'
          )}
        >
          {isTopup ? (
            <PlusCircleIcon className="w-4 h-4 text-primary-400" />
          ) : isExpense ? (
            <ArrowTrendingDownIcon className="w-4 h-4 text-red-400" />
          ) : (
            <ArrowTrendingUpIcon className="w-4 h-4 text-green-400" />
          )}
        </div>
        <div>
          <p className="text-sm text-gray-200 truncate max-w-[180px]">
            {transaction.taskName || transaction.description}
          </p>
          <p className="text-xs text-gray-500">
            {formatTime(transaction.timestamp)}
            {transaction.mode && transaction.mode !== 'disabled' && (
              <span className="ml-1 opacity-60">· {transaction.mode}</span>
            )}
          </p>
        </div>
      </div>
      <p
        className={clsx(
          'font-medium text-sm',
          isTopup
            ? 'text-primary-400'
            : isExpense
              ? 'text-red-400'
              : 'text-green-400'
        )}
      >
        {isExpense ? '-' : '+'}
        {formatCurrencyUSDC(transaction.amount)}
      </p>
    </div>
  )
}
