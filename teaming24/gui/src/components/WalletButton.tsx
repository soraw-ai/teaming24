/**
 * WalletButton - Global wallet status and actions button.
 * 
 * Shows wallet balance (ETH), setup status, and provides
 * access to wallet configuration and top-up dialogs.
 */

import { useState, useEffect, Fragment } from 'react'
import { Dialog, Transition } from '@headlessui/react'
import { getApiBase } from '../utils/api'
import {
  WalletIcon,
  Cog6ToothIcon,
  PlusIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  ClipboardDocumentIcon,
  ArrowTopRightOnSquareIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useWalletStore } from '../store/walletStore'
import { formatBalanceCompact } from '../utils/format'
import { truncateWalletAddress } from '../utils/strings'
import { getPaymentTokenAddresses } from '../config/payment'

export default function WalletButton() {
  const { config, balance, tokenSymbol, isLoadingBalance, fetchBalance } = useWalletStore()
  const [showMenu, setShowMenu] = useState(false)
  const [showSetupDialog, setShowSetupDialog] = useState(false)
  const [showTopupDialog, setShowTopupDialog] = useState(false)

  return (
    <>
      {/* Wallet Button */}
      <div className="relative z-[9999]">
        <button
          onClick={() => setShowMenu(!showMenu)}
          className={clsx(
            'flex items-center gap-2 px-3 py-2 rounded-lg transition-all',
            config.isConfigured
              ? 'bg-primary-500/20 hover:bg-primary-500/30 text-primary-400'
              : 'bg-orange-500/20 hover:bg-orange-500/30 text-orange-400'
          )}
        >
          <WalletIcon className="w-4 h-4" />
          {config.isConfigured ? (
            <>
              <span className="text-sm font-medium">
                {isLoadingBalance ? '...' : formatBalanceCompact(balance)} {tokenSymbol}
              </span>
            </>
          ) : (
            <span className="text-sm font-medium">Setup Wallet</span>
          )}
        </button>

        {/* Dropdown Menu */}
        {showMenu && (
          <>
            <div
              className="fixed inset-0 z-[9998]"
              onClick={() => setShowMenu(false)}
            />
            <div className="absolute right-0 top-full mt-2 w-64 bg-dark-surface border border-dark-border rounded-xl shadow-xl z-[10000] overflow-hidden">
              {/* Header */}
              <div className="p-4 border-b border-dark-border bg-gradient-to-r from-primary-500/10 to-purple-500/10">
                {config.isConfigured ? (
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <div className="w-2 h-2 rounded-full bg-green-400" />
                      <span className="text-xs text-gray-400">Connected</span>
                    </div>
                    <p className="text-lg font-bold text-white">
                      {formatBalanceCompact(balance)} {tokenSymbol}
                    </p>
                    <div className="flex items-center gap-2 mt-1">
                      <p className="text-xs text-gray-500 font-mono">
                        {truncateWalletAddress(config.address)}
                      </p>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          navigator.clipboard.writeText(config.address)
                        }}
                        className="p-1 hover:bg-dark-hover rounded transition-colors"
                        title="Copy address"
                      >
                        <ClipboardDocumentIcon className="w-3.5 h-3.5 text-gray-500 hover:text-gray-300" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-orange-500/20 flex items-center justify-center">
                      <ExclamationTriangleIcon className="w-5 h-5 text-orange-400" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-white">Not Configured</p>
                      <p className="text-xs text-gray-500">Set up your wallet</p>
                    </div>
                  </div>
                )}
              </div>

              {/* Actions */}
              <div className="p-2">
                {config.isConfigured && (
                  <>
                    <button
                      onClick={() => {
                        setShowMenu(false)
                        setShowTopupDialog(true)
                      }}
                      className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-dark-hover rounded-lg transition-colors"
                    >
                      <PlusIcon className="w-4 h-4 text-green-400" />
                      <span className="text-sm text-gray-200">Top Up Wallet</span>
                    </button>
                    <button
                      onClick={() => {
                        fetchBalance()
                        setShowMenu(false)
                      }}
                      className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-dark-hover rounded-lg transition-colors"
                    >
                      <ArrowTopRightOnSquareIcon className="w-4 h-4 text-primary-400" />
                      <span className="text-sm text-gray-200">Refresh Balance</span>
                    </button>
                  </>
                )}
                <button
                  onClick={() => {
                    setShowMenu(false)
                    setShowSetupDialog(true)
                  }}
                  className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-dark-hover rounded-lg transition-colors"
                >
                  <Cog6ToothIcon className="w-4 h-4 text-gray-400" />
                  <span className="text-sm text-gray-200">
                    {config.isConfigured ? 'Wallet Settings' : 'Configure Wallet'}
                  </span>
                </button>
              </div>

              {/* Status Info */}
              <div className="px-4 py-3 border-t border-dark-border bg-dark-bg/50 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Network</span>
                  <span className="text-gray-400 capitalize">
                    {config.network === 'base-sepolia' ? 'Base Sepolia (Testnet)' : 'Base Mainnet'}
                  </span>
                </div>
                <PaymentStatusLine />
              </div>
            </div>
          </>
        )}
      </div>

      {/* Setup Dialog */}
      <WalletSetupDialog
        isOpen={showSetupDialog}
        onClose={() => setShowSetupDialog(false)}
      />

      {/* Top Up Dialog */}
      <TopUpDialog
        isOpen={showTopupDialog}
        onClose={() => setShowTopupDialog(false)}
      />
    </>
  )
}

// ============================================================================
// Payment Status (inline in dropdown)
// ============================================================================

function PaymentStatusLine() {
  const [info, setInfo] = useState<{ enabled: boolean; mode: string } | null>(null)

  useEffect(() => {
    const apiBase = getApiBase()
    fetch(`${apiBase}/api/payment/config`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setInfo({ enabled: d.enabled, mode: d.mode }))
      .catch((e) => console.warn('Failed to fetch payment config:', e))
  }, [])

  if (!info) return null

  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-gray-500">x402 Payment</span>
      <span className={info.enabled ? 'text-green-400' : 'text-gray-500'}>
        {info.enabled ? `Enabled (${info.mode})` : 'Disabled'}
      </span>
    </div>
  )
}

// ============================================================================
// Setup Dialog
// ============================================================================

function WalletSetupDialog({
  isOpen,
  onClose,
}: {
  isOpen: boolean
  onClose: () => void
}) {
  const { config, tokenSymbol, fetchConfig } = useWalletStore()
  const [address, setAddress] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [network, setNetwork] = useState<'base' | 'base-sepolia'>('base-sepolia')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showPrivateKey, setShowPrivateKey] = useState(false)

  // Payment settings
  const [paymentEnabled, setPaymentEnabled] = useState(false)
  const [paymentMode, setPaymentMode] = useState<'mock' | 'testnet' | 'mainnet'>('mock')
  const [taskPrice, setTaskPrice] = useState('0.001')

  // Sync state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setAddress(config.address)
      setNetwork(config.network)
      setPrivateKey('')
      setError(null)
      // Fetch current payment config
      const apiBase = getApiBase()
      fetch(`${apiBase}/api/payment/config`)
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (data) {
            setPaymentEnabled(data.enabled ?? false)
            setPaymentMode(data.mode ?? 'mock')
            setTaskPrice(data.task_price ?? '0.001')
          }
        })
        .catch((e) => console.warn('Failed to fetch payment config:', e))
    }
  }, [isOpen, config.address, config.network])

  const handleSave = async () => {
    if (!address) {
      setError('Please enter a wallet address')
      return
    }

    if (!/^0x[a-fA-F0-9]{40}$/.test(address)) {
      setError('Invalid wallet address format')
      return
    }

    setSaving(true)
    setError(null)

    try {
      const apiBase = getApiBase()

      // 1) Save wallet config
      const walletRes = await fetch(`${apiBase}/api/wallet/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          address,
          private_key: privateKey || undefined,
          network,
        }),
      })
      if (!walletRes.ok) {
        const data = await walletRes.json().catch((e) => { console.warn('Failed to parse wallet response:', e); return {}; })
        throw new Error(data.detail || 'Failed to save wallet configuration')
      }

      // 2) Save payment config
      const payRes = await fetch(`${apiBase}/api/payment/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: paymentEnabled,
          mode: paymentMode,
          task_price: taskPrice,
        }),
      })
      if (!payRes.ok) {
        const data = await payRes.json().catch((e) => { console.warn('Failed to parse payment response:', e); return {}; })
        throw new Error(data.detail || 'Failed to save payment configuration')
      }

      // Refresh all wallet data after save
      await fetchConfig()
      useWalletStore.getState().fetchBalance()
      useWalletStore.getState().fetchSummary()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save configuration')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-md transform overflow-hidden rounded-2xl bg-dark-surface border border-dark-border shadow-xl transition-all max-h-[90vh] flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-dark-border shrink-0">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-primary-500/20 flex items-center justify-center">
                      <WalletIcon className="w-5 h-5 text-primary-400" />
                    </div>
                    <div>
                      <Dialog.Title className="text-lg font-semibold text-white">
                        Wallet & Payment
                      </Dialog.Title>
                      <p className="text-xs text-gray-500">Configure wallet and x402 payment</p>
                    </div>
                  </div>
                  <button
                    onClick={onClose}
                    className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
                  >
                    <XMarkIcon className="w-5 h-5 text-gray-400" />
                  </button>
                </div>

                {/* Content — scrollable */}
                <div className="p-6 space-y-5 overflow-y-auto flex-1">
                  {error && (
                    <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
                      <ExclamationTriangleIcon className="w-4 h-4 text-red-400 shrink-0" />
                      <p className="text-sm text-red-400">{error}</p>
                    </div>
                  )}

                  {/* ── Wallet Section ── */}
                  <div>
                    <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Wallet</h3>

                    {/* Network Selection */}
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-gray-300 mb-2">Network</label>
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          onClick={() => setNetwork('base-sepolia')}
                          className={clsx(
                            'p-3 rounded-lg border text-sm transition-colors',
                            network === 'base-sepolia'
                              ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                              : 'border-dark-border hover:border-gray-600 text-gray-400'
                          )}
                        >
                          <div className="font-medium">Base Sepolia</div>
                          <div className="text-xs opacity-70">Testnet</div>
                        </button>
                        <button
                          onClick={() => setNetwork('base')}
                          className={clsx(
                            'p-3 rounded-lg border text-sm transition-colors',
                            network === 'base'
                              ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                              : 'border-dark-border hover:border-gray-600 text-gray-400'
                          )}
                        >
                          <div className="font-medium">Base Mainnet</div>
                          <div className="text-xs opacity-70">Production</div>
                        </button>
                      </div>
                    </div>

                    {/* Wallet Address */}
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-gray-300 mb-2">Wallet Address</label>
                      <input
                        type="text"
                        value={address}
                        onChange={(e) => setAddress(e.target.value)}
                        placeholder="0x..."
                        className="w-full px-4 py-3 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500 font-mono text-sm focus:outline-none focus:border-primary-500"
                      />
                    </div>

                    {/* Private Key */}
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">
                        Private Key <span className="text-gray-500">(optional)</span>
                      </label>
                      <div className="relative">
                        <input
                          type={showPrivateKey ? 'text' : 'password'}
                          value={privateKey}
                          onChange={(e) => setPrivateKey(e.target.value)}
                          placeholder="Enter private key or set in .env"
                          className="w-full px-4 py-3 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500 font-mono text-sm focus:outline-none focus:border-primary-500 pr-12"
                        />
                        <button
                          type="button"
                          onClick={() => setShowPrivateKey(!showPrivateKey)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                        >
                          {showPrivateKey ? 'Hide' : 'Show'}
                        </button>
                      </div>
                      <p className="mt-1.5 text-xs text-gray-500">
                        Or set TEAMING24_WALLET_PRIVATE_KEY in .env
                      </p>
                    </div>
                  </div>

                  {/* ── Payment Settings Section ── */}
                  <div className="pt-4 border-t border-dark-border">
                    <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">x402 Payment</h3>

                    {/* Enable Payment */}
                    <div className="flex items-center justify-between mb-4">
                      <div>
                        <p className="text-sm text-gray-200">Enable Payment</p>
                        <p className="text-xs text-gray-500">Enforce x402 payment for task execution</p>
                      </div>
                      <button
                        onClick={() => setPaymentEnabled(!paymentEnabled)}
                        className={clsx(
                          'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                          paymentEnabled ? 'bg-primary-600' : 'bg-gray-700'
                        )}
                      >
                        <span
                          className={clsx(
                            'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
                            paymentEnabled ? 'translate-x-6' : 'translate-x-1'
                          )}
                        />
                      </button>
                    </div>

                    {/* Payment Mode */}
                    <div className="mb-4">
                      <label className="block text-sm font-medium text-gray-300 mb-2">Payment Mode</label>
                      <div className="grid grid-cols-3 gap-2">
                        {(['mock', 'testnet', 'mainnet'] as const).map((m) => (
                          <button
                            key={m}
                            onClick={() => setPaymentMode(m)}
                            disabled={!paymentEnabled}
                            className={clsx(
                              'px-3 py-2 rounded-lg border text-sm transition-colors',
                              !paymentEnabled && 'opacity-40 cursor-not-allowed',
                              paymentMode === m
                                ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                                : 'border-dark-border hover:border-gray-600 text-gray-400'
                            )}
                          >
                            <div className="font-medium capitalize">{m}</div>
                          </button>
                        ))}
                      </div>
                      <p className="mt-1.5 text-xs text-gray-500">
                        {paymentMode === 'mock' && 'Simulated payments — no real blockchain calls'}
                        {paymentMode === 'testnet' && 'Real EIP-3009 signatures on Base Sepolia'}
                        {paymentMode === 'mainnet' && 'Real payments on Base mainnet (production)'}
                      </p>
                    </div>

                    {/* Task Price */}
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-2">Task Price ({tokenSymbol})</label>
                      <input
                        type="text"
                        value={taskPrice}
                        onChange={(e) => setTaskPrice(e.target.value)}
                        disabled={!paymentEnabled}
                        placeholder="0.001"
                        className={clsx(
                          'w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500 text-sm focus:outline-none focus:border-primary-500',
                          !paymentEnabled && 'opacity-40 cursor-not-allowed'
                        )}
                      />
                      <p className="mt-1.5 text-xs text-gray-500">
                        Price charged per task execution
                      </p>
                    </div>
                  </div>

                  {/* Info Box */}
                  <div className="p-4 bg-dark-bg rounded-lg border border-dark-border">
                    <div className="flex items-start gap-3">
                      <CheckCircleIcon className="w-5 h-5 text-primary-400 shrink-0 mt-0.5" />
                      <div className="text-sm text-gray-400">
                        <p className="font-medium text-gray-300 mb-1">x402 Payment Protocol</p>
                        <p>This wallet is used to pay/receive for agent tasks via the x402 protocol. Top up with {tokenSymbol} before running tasks.</p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Footer */}
                <div className="flex gap-3 px-6 py-4 border-t border-dark-border bg-dark-bg/50 shrink-0">
                  <button
                    onClick={onClose}
                    className="flex-1 px-4 py-2.5 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="flex-1 px-4 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg transition-colors disabled:opacity-50"
                  >
                    {saving ? 'Saving...' : 'Save Configuration'}
                  </button>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  )
}

// ============================================================================
// Top Up Dialog
// ============================================================================

function TopUpDialog({
  isOpen,
  onClose,
}: {
  isOpen: boolean
  onClose: () => void
}) {
  const { config, tokenSymbol, fetchAll } = useWalletStore()
  const [amount, setAmount] = useState('10')
  const [devMode, setDevMode] = useState(false)
  const [status, setStatus] = useState<'idle' | 'connecting' | 'pending' | 'success' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [txHash, setTxHash] = useState<string | null>(null)

  // Token contract addresses from config
  const tokenAddresses = getPaymentTokenAddresses()

  // Dev mode: add ETH to mock balance directly (no MetaMask needed)
  const handleDevTopUp = async () => {
    const amountNum = parseFloat(amount)
    if (isNaN(amountNum) || amountNum <= 0) {
      setError('Please enter a valid amount')
      return
    }
    setStatus('pending')
    setError(null)
    try {
      const apiBase = getApiBase()
      const res = await fetch(`${apiBase}/api/wallet/mock-topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: amountNum }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to add balance')
      }
      setStatus('success')
      setTimeout(() => fetchAll(), 300)
    } catch (err: any) {
      setStatus('error')
      setError(err.message || 'Failed to add dev balance')
    }
  }

  const handleTopUp = async () => {
    if (!window.ethereum) {
      setError('MetaMask not detected. Please install MetaMask extension.')
      return
    }

    const amountNum = parseFloat(amount)
    if (isNaN(amountNum) || amountNum <= 0) {
      setError('Please enter a valid amount')
      return
    }

    setStatus('connecting')
    setError(null)

    try {
      // Request account access
      const accounts = await window.ethereum.request({
        method: 'eth_requestAccounts',
      })

      if (!accounts || accounts.length === 0) {
        throw new Error('No accounts found')
      }

      // Check network
      const chainId = await window.ethereum.request({ method: 'eth_chainId' })
      const expectedChainId = config.network === 'base' ? '0x2105' : '0x14a34' // Base: 8453, Base Sepolia: 84532

      if (chainId !== expectedChainId) {
        // Try to switch network
        try {
          await window.ethereum.request({
            method: 'wallet_switchEthereumChain',
            params: [{ chainId: expectedChainId }],
          })
        } catch (switchError: any) {
          if (switchError.code === 4902) {
            // Add the network
            await window.ethereum.request({
              method: 'wallet_addEthereumChain',
              params: [{
                chainId: expectedChainId,
                chainName: config.network === 'base' ? 'Base' : 'Base Sepolia',
                nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
                rpcUrls: [config.network === 'base' 
                  ? 'https://mainnet.base.org' 
                  : 'https://sepolia.base.org'],
                blockExplorerUrls: [config.network === 'base'
                  ? 'https://basescan.org'
                  : 'https://sepolia.basescan.org'],
              }],
            })
          } else {
            throw switchError
          }
        }
      }

      setStatus('pending')

      // ETH token has 6 decimals
      const amountInWei = BigInt(Math.floor(amountNum * 1e6)).toString(16)
      const usdcContract = tokenAddresses[config.network]

      // ERC20 transfer data
      // transfer(address,uint256) = 0xa9059cbb
      const transferData = '0xa9059cbb' +
        config.address.slice(2).padStart(64, '0') +
        amountInWei.padStart(64, '0')

      // Send transaction
      const hash = await window.ethereum.request({
        method: 'eth_sendTransaction',
        params: [{
          from: accounts[0],
          to: usdcContract,
          data: transferData,
        }],
      })

      setTxHash(hash)
      setStatus('success')

      // Refresh balance from backend after on-chain confirmation delay
      setTimeout(() => {
        fetchAll()
      }, 5000)

    } catch (err: any) {
      console.error('Top-up error:', err)
      setStatus('error')
      if (err.code === 4001) {
        setError('Transaction rejected by user')
      } else {
        setError(err.message || 'Failed to send transaction')
      }
    }
  }

  const handleClose = () => {
    setStatus('idle')
    setError(null)
    setTxHash(null)
    setAmount('10')
    setDevMode(false)
    onClose()
  }

  const presetAmounts = ['5', '10', '25', '50', '100']

  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={handleClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-md transform overflow-hidden rounded-2xl bg-dark-surface border border-dark-border shadow-xl transition-all">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-dark-border">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-green-500/20 flex items-center justify-center">
                      <PlusIcon className="w-5 h-5 text-green-400" />
                    </div>
                    <div>
                      <Dialog.Title className="text-lg font-semibold text-white">
                        Top Up Wallet
                      </Dialog.Title>
                      <p className="text-xs text-gray-500">
                        {devMode ? 'Dev mode — instant mock balance' : `Send ${tokenSymbol} via MetaMask`}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={handleClose}
                    className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
                  >
                    <XMarkIcon className="w-5 h-5 text-gray-400" />
                  </button>
                </div>

                {/* Content */}
                <div className="p-6">
                  {status === 'success' ? (
                    <div className="text-center py-6">
                      <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center mx-auto mb-4">
                        <CheckCircleIcon className="w-8 h-8 text-green-400" />
                      </div>
                      <h3 className="text-lg font-semibold text-white mb-2">
                        {devMode ? 'Balance Updated!' : 'Transaction Submitted!'}
                      </h3>
                      <p className="text-sm text-gray-400 mb-4">
                        {devMode
                          ? `+${amount} ${tokenSymbol} added to your dev balance.`
                          : `Your top-up of ${amount} ${tokenSymbol} has been submitted.`}
                      </p>
                      {txHash && (
                        <a
                          href={`https://${config.network === 'base' ? '' : 'sepolia.'}basescan.org/tx/${txHash}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-2 text-primary-400 hover:text-primary-300 text-sm"
                        >
                          View on BaseScan
                          <ArrowTopRightOnSquareIcon className="w-4 h-4" />
                        </a>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {/* Dev Mode Switch */}
                      <div className="flex items-center justify-between px-3 py-2.5 bg-dark-bg rounded-lg border border-dark-border">
                        <div>
                          <p className="text-sm font-medium text-gray-300">Dev Mode</p>
                          <p className="text-xs text-gray-500">Skip MetaMask — add mock balance instantly</p>
                        </div>
                        <button
                          onClick={() => { setDevMode(!devMode); setError(null) }}
                          className={clsx(
                            'relative inline-flex h-6 w-11 items-center rounded-full transition-colors shrink-0',
                            devMode ? 'bg-amber-500' : 'bg-gray-700'
                          )}
                        >
                          <span
                            className={clsx(
                              'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
                              devMode ? 'translate-x-6' : 'translate-x-1'
                            )}
                          />
                        </button>
                      </div>

                      {error && (
                        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
                          <ExclamationTriangleIcon className="w-4 h-4 text-red-400 shrink-0" />
                          <p className="text-sm text-red-400">{error}</p>
                        </div>
                      )}

                      {/* Destination — only for real mode */}
                      {!devMode && (
                        <div>
                          <label className="block text-sm font-medium text-gray-300 mb-2">
                            To Wallet
                          </label>
                          <div className="flex items-center gap-2 px-4 py-3 bg-dark-bg border border-dark-border rounded-lg">
                            <span className="text-sm text-gray-400 font-mono">
                              {config.address.slice(0, 10)}...{config.address.slice(-8)}
                            </span>
                            <button
                              onClick={() => navigator.clipboard.writeText(config.address)}
                              className="ml-auto p-1 hover:bg-dark-hover rounded"
                              title="Copy address"
                            >
                              <ClipboardDocumentIcon className="w-4 h-4 text-gray-500" />
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Amount */}
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">
                          Amount ({tokenSymbol})
                        </label>
                        <input
                          type="number"
                          value={amount}
                          onChange={(e) => setAmount(e.target.value)}
                          min="1"
                          step="1"
                          className="w-full px-4 py-3 bg-dark-bg border border-dark-border rounded-lg text-white text-lg font-medium focus:outline-none focus:border-primary-500"
                        />
                        <div className="flex gap-2 mt-2">
                          {presetAmounts.map((preset) => (
                            <button
                              key={preset}
                              onClick={() => setAmount(preset)}
                              className={clsx(
                                'flex-1 py-1.5 rounded text-sm transition-colors',
                                amount === preset
                                  ? 'bg-primary-500/20 text-primary-400 border border-primary-500/50'
                                  : 'bg-dark-bg hover:bg-dark-hover text-gray-400 border border-dark-border'
                              )}
                            >
                              ${preset}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* Info row */}
                      <div className="p-3 bg-dark-bg rounded-lg border border-dark-border space-y-1.5">
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-gray-500">Token</span>
                          <span className="text-gray-300">{tokenSymbol}</span>
                        </div>
                        {devMode ? (
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-gray-500">Mode</span>
                            <span className="text-amber-400">Mock (local simulation)</span>
                          </div>
                        ) : (
                          <>
                            <div className="flex items-center justify-between text-sm">
                              <span className="text-gray-500">Network</span>
                              <span className="text-gray-300">
                                {config.network === 'base' ? 'Base Mainnet' : 'Base Sepolia (Testnet)'}
                              </span>
                            </div>
                            <div className="flex items-center justify-between text-sm">
                              <span className="text-gray-500">Contract</span>
                              <span className="text-gray-400 font-mono text-xs">
                                {tokenAddresses[config.network]?.slice(0, 8)}...{tokenAddresses[config.network]?.slice(-6)}
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Footer */}
                <div className="px-6 py-4 border-t border-dark-border bg-dark-bg/50">
                  {status === 'success' ? (
                    <button
                      onClick={handleClose}
                      className="w-full px-4 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg transition-colors"
                    >
                      Done
                    </button>
                  ) : devMode ? (
                    /* Dev mode: instant topup button */
                    <button
                      onClick={handleDevTopUp}
                      disabled={status === 'pending'}
                      className="w-full px-4 py-2.5 bg-amber-500 hover:bg-amber-600 text-white rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {status === 'pending' ? (
                        <>
                          <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                          Adding...
                        </>
                      ) : (
                        <>
                          <PlusIcon className="w-4 h-4" />
                          Add {amount} {tokenSymbol} (Dev Mode)
                        </>
                      )}
                    </button>
                  ) : (
                    /* Real mode: MetaMask button */
                    <button
                      onClick={handleTopUp}
                      disabled={status === 'connecting' || status === 'pending'}
                      className="w-full px-4 py-2.5 bg-green-500 hover:bg-green-600 text-white rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {status === 'connecting' && (
                        <>
                          <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                          Connecting MetaMask...
                        </>
                      )}
                      {status === 'pending' && (
                        <>
                          <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                          Confirm in MetaMask...
                        </>
                      )}
                      {(status === 'idle' || status === 'error') && (
                        <>
                          <WalletIcon className="w-4 h-4" />
                          Top Up with MetaMask
                        </>
                      )}
                    </button>
                  )}
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  )
}

// Add ethereum to window type
declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: any[] }) => Promise<any>
      on: (event: string, callback: (...args: any[]) => void) => void
      removeListener: (event: string, callback: (...args: any[]) => void) => void
    }
  }
}
