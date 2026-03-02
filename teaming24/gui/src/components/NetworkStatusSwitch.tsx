/**
 * Network Status Switch - Toggle local node's connection to AgentaNet.
 * 
 * Features:
 * - Visual online/offline status indicator
 * - Switch toggle with confirmation dialog
 * - Shows connection duration and peer count
 */

import { useState, Fragment } from 'react'
import { Dialog, Transition, Switch } from '@headlessui/react'
import {
  GlobeAltIcon,
  SignalIcon,
  SignalSlashIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useNetworkStore, NetworkStatus } from '../store/networkStore'
import { formatDurationSecs } from '../utils/format'

interface NetworkStatusSwitchProps {
  compact?: boolean
}

export default function NetworkStatusSwitch({ compact = false }: NetworkStatusSwitchProps) {
  const { status, goOnline, goOffline, peerCount, connectedSince } = useNetworkStore()
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)
  const [pendingAction, setPendingAction] = useState<'online' | 'offline' | null>(null)
  const [, setIsTransitioning] = useState(false)

  const isOnline = status === 'online'
  const isConnecting = status === 'connecting'
  const isDisconnecting = status === 'disconnecting'
  const isLoading = isConnecting || isDisconnecting

  const formattedDuration = connectedSince
    ? formatDurationSecs(Math.floor((Date.now() - connectedSince) / 1000))
    : null

  // Handle switch toggle
  const handleToggle = () => {
    if (isLoading) return
    
    const newAction = isOnline ? 'offline' : 'online'
    setPendingAction(newAction)
    setShowConfirmDialog(true)
  }

  // Confirm action
  const handleConfirm = async () => {
    setShowConfirmDialog(false)
    setIsTransitioning(true)

    try {
      if (pendingAction === 'online') {
        await goOnline()
      } else {
        await goOffline()
      }
    } finally {
      setIsTransitioning(false)
      setPendingAction(null)
    }
  }

  // Cancel action
  const handleCancel = () => {
    setShowConfirmDialog(false)
    setPendingAction(null)
  }

  // Get status display info
  const getStatusInfo = (s: NetworkStatus | string) => {
    switch (s) {
      case 'online':
        return {
          label: 'Online',
          color: 'text-green-400',
          bgColor: 'bg-green-400',
          icon: SignalIcon,
        }
      case 'offline':
        return {
          label: 'Offline',
          color: 'text-gray-400',
          bgColor: 'bg-gray-400',
          icon: SignalSlashIcon,
        }
      case 'connecting':
        return {
          label: 'Connecting...',
          color: 'text-yellow-400',
          bgColor: 'bg-yellow-400',
          icon: ArrowPathIcon,
        }
      case 'disconnecting':
        return {
          label: 'Disconnecting...',
          color: 'text-yellow-400',
          bgColor: 'bg-yellow-400',
          icon: ArrowPathIcon,
        }
      default:
        return {
          label: 'Offline',
          color: 'text-gray-400',
          bgColor: 'bg-gray-400',
          icon: SignalSlashIcon,
        }
    }
  }

  const statusInfo = getStatusInfo(status)
  const StatusIcon = statusInfo.icon

  if (compact) {
    // Compact version for sidebar
    return (
      <>
        <button
          onClick={handleToggle}
          disabled={isLoading}
          className={clsx(
            'flex items-center gap-2 px-2 py-1 rounded-full text-xs font-medium transition-all',
            isOnline
              ? 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
              : 'bg-gray-500/20 text-gray-400 hover:bg-gray-500/30',
            isLoading && 'opacity-50 cursor-not-allowed'
          )}
        >
          <span
            className={clsx(
              'w-2 h-2 rounded-full',
              statusInfo.bgColor,
              isLoading && 'animate-pulse'
            )}
          />
          <span>{statusInfo.label}</span>
        </button>

        {/* Confirmation Dialog */}
        <ConfirmDialog
          isOpen={showConfirmDialog}
          onClose={handleCancel}
          onConfirm={handleConfirm}
          action={pendingAction}
        />
      </>
    )
  }

  // Full version with more details
  return (
    <>
      <div className="flex items-center justify-between p-3 rounded-lg bg-dark-bg border border-dark-border">
        <div className="flex items-center gap-3">
          <div
            className={clsx(
              'w-10 h-10 rounded-lg flex items-center justify-center',
              isOnline ? 'bg-green-500/20' : 'bg-gray-500/20'
            )}
          >
            <StatusIcon
              className={clsx(
                'w-5 h-5',
                statusInfo.color,
                isLoading && 'animate-spin'
              )}
            />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className={clsx('text-sm font-medium', statusInfo.color)}>
                {statusInfo.label}
              </span>
              {isOnline && peerCount > 0 && (
                <span className="text-xs text-gray-500">
                  ({peerCount} peers)
                </span>
              )}
            </div>
            {isOnline && connectedSince && (
              <span className="text-xs text-gray-500">
                Connected for {formattedDuration}
              </span>
            )}
            {!isOnline && !isLoading && (
              <span className="text-xs text-gray-500">
                Remote connections disabled
              </span>
            )}
          </div>
        </div>

        <Switch
          checked={isOnline}
          onChange={handleToggle}
          disabled={isLoading}
          className={clsx(
            'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
            isOnline ? 'bg-green-500' : 'bg-gray-600',
            isLoading && 'opacity-50 cursor-not-allowed'
          )}
        >
          <span
            className={clsx(
              'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
              isOnline ? 'translate-x-6' : 'translate-x-1'
            )}
          />
        </Switch>
      </div>

      {/* Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showConfirmDialog}
        onClose={handleCancel}
        onConfirm={handleConfirm}
        action={pendingAction}
      />
    </>
  )
}

// Confirmation Dialog Component
function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  action,
}: {
  isOpen: boolean
  onClose: () => void
  onConfirm: () => void
  action: 'online' | 'offline' | null
}) {
  // Keep track of the last valid action to prevent flicker during close animation
  const [lastAction, setLastAction] = useState<'online' | 'offline'>('online')
  
  // Update lastAction when we get a new valid action
  if (action !== null && action !== lastAction) {
    setLastAction(action)
  }
  
  // Use lastAction for rendering to prevent flicker
  const isGoingOnline = (action ?? lastAction) === 'online'

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
              <Dialog.Panel className="w-full max-w-md transform overflow-hidden rounded-2xl bg-dark-surface border border-dark-border p-6 shadow-xl transition-all">
                <div className="flex items-start gap-4">
                  <div
                    className={clsx(
                      'w-12 h-12 rounded-full flex items-center justify-center shrink-0',
                      isGoingOnline ? 'bg-green-500/20' : 'bg-orange-500/20'
                    )}
                  >
                    {isGoingOnline ? (
                      <GlobeAltIcon className="w-6 h-6 text-green-400" />
                    ) : (
                      <ExclamationTriangleIcon className="w-6 h-6 text-orange-400" />
                    )}
                  </div>

                  <div className="flex-1">
                    <Dialog.Title className="text-lg font-semibold text-white">
                      {isGoingOnline ? 'Join AgentaNet?' : 'Leave AgentaNet?'}
                    </Dialog.Title>

                    <Dialog.Description className="mt-2 text-sm text-gray-400">
                      {isGoingOnline ? (
                        <>
                          Your local node will become part of the AgentaNet network.
                          <ul className="mt-2 space-y-1 text-xs">
                            <li className="flex items-center gap-2">
                              <CheckCircleIcon className="w-4 h-4 text-green-400" />
                              <span>Remote agents can discover and connect to you</span>
                            </li>
                            <li className="flex items-center gap-2">
                              <CheckCircleIcon className="w-4 h-4 text-green-400" />
                              <span>Participate in distributed task execution</span>
                            </li>
                            <li className="flex items-center gap-2">
                              <CheckCircleIcon className="w-4 h-4 text-green-400" />
                              <span>Earn rewards for hosting agents</span>
                            </li>
                          </ul>
                        </>
                      ) : (
                        <>
                          Your local node will disconnect from AgentaNet.
                          <ul className="mt-2 space-y-1 text-xs">
                            <li className="flex items-center gap-2">
                              <ExclamationTriangleIcon className="w-4 h-4 text-orange-400" />
                              <span>All remote connections will be terminated</span>
                            </li>
                            <li className="flex items-center gap-2">
                              <ExclamationTriangleIcon className="w-4 h-4 text-orange-400" />
                              <span>Remote agents cannot discover your node</span>
                            </li>
                            <li className="flex items-center gap-2">
                              <CheckCircleIcon className="w-4 h-4 text-gray-400" />
                              <span>Local operations continue normally</span>
                            </li>
                          </ul>
                        </>
                      )}
                    </Dialog.Description>
                  </div>
                </div>

                <div className="mt-6 flex gap-3 justify-end">
                  <button
                    onClick={onClose}
                    className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={onConfirm}
                    className={clsx(
                      'px-4 py-2 text-sm font-medium text-white rounded-lg transition-colors',
                      isGoingOnline
                        ? 'bg-green-600 hover:bg-green-700'
                        : 'bg-orange-600 hover:bg-orange-700'
                    )}
                  >
                    {isGoingOnline ? 'Go Online' : 'Go Offline'}
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
