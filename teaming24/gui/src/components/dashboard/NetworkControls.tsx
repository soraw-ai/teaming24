import { useState } from 'react'
import { Dialog, Transition } from '@headlessui/react'
import { Fragment } from 'react'
import { useNetworkStore } from '../../store/networkStore'
import { useSettingsStore } from '../../store/settingsStore'
import {
  EyeIcon,
  EyeSlashIcon,
  ShoppingBagIcon,
  MagnifyingGlassIcon,
  ExclamationTriangleIcon
} from '@heroicons/react/24/outline'
import { notify } from '../../store/notificationStore'
import MarketplaceListingDialog from './MarketplaceListingDialog'

// Tooltip component for hover explanations
function Tooltip({ children, text }: { children: React.ReactNode; text: string }) {
  return (
    <div className="relative group">
      {children}
      <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 px-2 py-1
                      bg-dark-bg border border-dark-border rounded text-xs text-gray-300
                      opacity-0 group-hover:opacity-100 transition-opacity duration-200
                      pointer-events-none whitespace-nowrap z-[99999] shadow-lg">
        {text}
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 -mb-1
                        border-4 border-transparent border-b-dark-border" />
      </div>
    </div>
  )
}

// Confirmation Dialog component
function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  type = 'warning'
}: {
  isOpen: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  type?: 'warning' | 'info'
}) {
  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-150"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/60" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-200"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-150"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-sm transform overflow-hidden rounded-xl bg-dark-surface border border-dark-border p-5 shadow-xl transition-all">
                <div className="flex items-start gap-3">
                  <div className={`p-2 rounded-lg ${type === 'warning' ? 'bg-yellow-500/20' : 'bg-primary-500/20'}`}>
                    <ExclamationTriangleIcon className={`w-5 h-5 ${type === 'warning' ? 'text-yellow-400' : 'text-primary-400'}`} />
                  </div>
                  <div className="flex-1">
                    <Dialog.Title className="text-sm font-semibold text-white">
                      {title}
                    </Dialog.Title>
                    <p className="mt-1 text-xs text-gray-400">
                      {message}
                    </p>
                  </div>
                </div>

                <div className="mt-4 flex justify-end gap-2">
                  <button
                    onClick={onClose}
                    className="px-3 py-1.5 text-xs font-medium text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
                  >
                    {cancelText}
                  </button>
                  <button
                    onClick={() => { onConfirm(); onClose(); }}
                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                      type === 'warning'
                        ? 'bg-yellow-600 hover:bg-yellow-700 text-white'
                        : 'bg-primary-600 hover:bg-primary-700 text-white'
                    }`}
                  >
                    {confirmText}
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

export default function NetworkControls() {
  const {
    isDiscovering,
    isDiscoverable,
    isListedOnMarketplace,
    status,
    startDiscovery,
    stopDiscovery,
    setDiscoverable
  } = useNetworkStore()
  const { agentanetCentralUrl, agentanetToken } = useSettingsStore()

  const [showListingDialog, setShowListingDialog] = useState(false)
  const [scanAnimating, setScanAnimating] = useState(false)
  const [visibleAnimating, setVisibleAnimating] = useState(false)

  // Confirmation dialogs
  const [showVisibleConfirm, setShowVisibleConfirm] = useState(false)
  const [showHideConfirm, setShowHideConfirm] = useState(false)
  const [showListedConfirm, setShowListedConfirm] = useState(false)

  const isOnline = status === 'online'
  const centralLinked = Boolean((agentanetCentralUrl || '').trim() && (agentanetToken || '').trim())
  // Note: LAN Visible auto-apply is handled centrally in networkStore.goOnline

  // Handle LAN Scan toggle
  const handleScanToggle = async () => {
    if (!isOnline) {
      setScanAnimating(true)
      setTimeout(() => {
        setScanAnimating(false)
        notify.warning('Offline', 'Go online first to scan LAN')
      }, 300)
      return
    }

    if (isDiscovering) {
      await stopDiscovery()
    } else {
      await startDiscovery()
    }
  }

  // Handle LAN visibility toggle with confirmation
  const handleVisibilityClick = () => {
    if (!isOnline) {
      notify.warning('Offline', 'Go online first to change LAN visibility')
      return
    }

    if (isDiscoverable) {
      setShowHideConfirm(true)
    } else {
      setShowVisibleConfirm(true)
    }
  }

  const handleEnableVisible = async () => {
    setVisibleAnimating(true)
    setTimeout(async () => {
      await setDiscoverable(true)
      setVisibleAnimating(false)
    }, 300)
  }

  const handleDisableVisible = async () => {
    await setDiscoverable(false)
  }

  // Handle marketplace click with confirmation
  const handleMarketplaceClick = () => {
    if (!isOnline) {
      notify.warning('Offline', 'Go online first to join the Agentic Node Marketplace')
      return
    }

    if (!centralLinked && !isListedOnMarketplace) {
      notify.warning(
        'AgentaNet Central',
        'Please configure Central Service URL and Token in Settings before joining marketplace',
      )
      return
    }

    if (isListedOnMarketplace) {
      // Already listed - open dialog to edit/leave
      setShowListingDialog(true)
    } else {
      // Not listed - show confirmation first
      setShowListedConfirm(true)
    }
  }

  const handleConfirmJoinMarketplace = () => {
    setShowListingDialog(true)
  }

  return (
    <>
      <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
        {/* Marketplace Listing */}
        <Tooltip text={
          !isOnline
            ? "Go online to join the Agentic Node Marketplace"
            : isListedOnMarketplace
              ? "Your node is listed on the global Agentic Node Marketplace"
              : "List your node on the Agentic Node Marketplace"
        }>
          <div className="flex items-center gap-2 bg-dark-surface rounded-lg p-1 border border-dark-border h-8">
            <button
              onClick={handleMarketplaceClick}
              className={`
                flex items-center gap-1.5 px-2 py-0.5 rounded transition-colors text-xs font-medium min-w-0
                ${!isOnline
                  ? 'text-gray-600 hover:text-gray-500 hover:bg-dark-hover/50'
                  : isListedOnMarketplace
                    ? 'text-primary-400 hover:bg-primary-500/10'
                    : 'text-gray-400 hover:bg-dark-hover'}
              `}
            >
              <ShoppingBagIcon className="w-3.5 h-3.5" />
              <span className="sm:hidden">{isListedOnMarketplace ? 'Listed' : 'Join'}</span>
              <span className="hidden sm:inline truncate" title={isListedOnMarketplace ? 'Listed' : 'Join Agentic Node Marketplace'}>
                {isListedOnMarketplace ? 'Listed' : 'Join Agentic Node Marketplace'}
              </span>
            </button>
          </div>
        </Tooltip>

        {/* LAN Visibility Toggle */}
        <Tooltip text={
          !isOnline
            ? "Go online to change LAN visibility"
            : isDiscoverable
              ? "Others can find you via LAN scan (click to hide)"
              : "You are hidden from LAN scans (click to be visible)"
        }>
          <div className={`flex items-center gap-2 bg-dark-surface rounded-lg p-1 border h-8 transition-all duration-300 ${
            visibleAnimating ? 'border-green-500 shadow-[0_0_10px_rgba(34,197,94,0.3)]' : 'border-dark-border'
          }`}>
            <button
              onClick={handleVisibilityClick}
              disabled={visibleAnimating}
              className={`
                flex items-center gap-1.5 px-2 py-0.5 rounded transition-colors text-xs font-medium
                ${visibleAnimating
                  ? 'text-green-400 animate-pulse'
                  : !isOnline
                    ? 'text-gray-600 hover:text-gray-500 hover:bg-dark-hover/50'
                    : isDiscoverable
                      ? 'text-green-400 hover:bg-green-500/10'
                      : 'text-gray-500 hover:bg-dark-hover'}
              `}
            >
              {isDiscoverable || visibleAnimating ? (
                <>
                  <EyeIcon className="w-3.5 h-3.5" />
                  <span>LAN Visible</span>
                </>
              ) : (
                <>
                  <EyeSlashIcon className="w-3.5 h-3.5" />
                  <span>LAN Hidden</span>
                </>
              )}
            </button>
          </div>
        </Tooltip>

        {/* LAN Scan Toggle */}
        <Tooltip text={
          !isOnline
            ? "Go online to scan for LAN nodes"
            : isDiscovering
              ? "Scanning local network (click to stop)"
              : "Scan local network to find other nodes"
        }>
          <div className="flex items-center gap-2 bg-dark-surface rounded-lg p-1 border border-dark-border h-8">
            <MagnifyingGlassIcon className={`w-3.5 h-3.5 ml-1.5 ${isDiscovering ? 'text-green-400' : 'text-gray-500'}`} />
            <span className="text-[10px] text-gray-400 font-medium">Scan</span>
            <button
              onClick={handleScanToggle}
              className={`
                relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent
                transition-colors duration-200 ease-in-out focus:outline-none mr-1
                ${scanAnimating
                  ? 'bg-yellow-600 animate-pulse'
                  : !isOnline
                    ? 'bg-gray-800 hover:bg-gray-700'
                    : isDiscovering
                      ? 'bg-green-600'
                      : 'bg-gray-700 hover:bg-gray-600'}
              `}
            >
              <span
                className={`
                  pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0
                  transition duration-200 ease-in-out
                  ${scanAnimating ? 'translate-x-2' : isDiscovering ? 'translate-x-4' : 'translate-x-0'}
                `}
              />
            </button>
          </div>
        </Tooltip>
      </div>

      {/* Confirmation Dialogs */}
      <ConfirmDialog
        isOpen={showVisibleConfirm}
        onClose={() => setShowVisibleConfirm(false)}
        onConfirm={handleEnableVisible}
        title="Enable LAN Visibility?"
        message="This will start a UDP listener so other nodes on your local network can discover you. Your IP address will be visible to LAN scanners."
        confirmText="Enable"
        type="info"
      />

      <ConfirmDialog
        isOpen={showHideConfirm}
        onClose={() => setShowHideConfirm(false)}
        onConfirm={handleDisableVisible}
        title="Disable LAN Visibility?"
        message="Other nodes will no longer be able to discover you via LAN scan. Existing connections will not be affected."
        confirmText="Disable"
        type="warning"
      />

      <ConfirmDialog
        isOpen={showListedConfirm}
        onClose={() => setShowListedConfirm(false)}
        onConfirm={handleConfirmJoinMarketplace}
        title="Join Agentic Node Marketplace?"
        message="Your node information (name, capabilities, pricing) will be publicly listed in the Agentic Node Marketplace. Other users can discover and connect to you."
        confirmText="Continue"
        type="info"
      />

      {/* Marketplace Listing Dialog */}
      <MarketplaceListingDialog
        isOpen={showListingDialog}
        onClose={() => setShowListingDialog(false)}
      />
    </>
  )
}
