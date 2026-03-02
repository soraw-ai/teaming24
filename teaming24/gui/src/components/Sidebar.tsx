import { useState, useEffect, Fragment } from 'react'
import { 
  PlusIcon, 
  ChatBubbleLeftIcon, 
  TrashIcon, 
  Bars3Icon,
  ChartBarIcon,
  UserGroupIcon,
  GlobeAltIcon,
  Cog6ToothIcon,
  WalletIcon,
  ArrowTrendingUpIcon,
  ArrowTrendingDownIcon,
  BookOpenIcon,
  ServerIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import { Dialog, Transition } from '@headlessui/react'
import clsx from 'clsx'
import type { ChatSession } from '../store/chatStore'
import { useChatStore } from '../store/chatStore'
import type { ViewMode } from '../types'
import { useAgentStore } from '../store/agentStore'
import { useWalletStore } from '../store/walletStore'
import { useNetworkStore } from '../store/networkStore'
import { isDemoId } from '../utils/ids'
import NetworkStatusSwitch from './NetworkStatusSwitch'
import SettingsDialog from './SettingsDialog'
import { formatUSDC } from '../utils/format'

interface SidebarProps {
  isOpen: boolean
  onToggle: () => void
  sessions: ChatSession[]
  activeSessionId: string | null
  onNewChat: () => void
  onSelectSession: (id: string) => void
  onDeleteSession: (id: string) => void
  viewMode: ViewMode
  onViewModeChange: (mode: ViewMode) => void
}

export default function Sidebar({
  isOpen,
  onToggle,
  sessions,
  activeSessionId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  viewMode,
  onViewModeChange,
}: SidebarProps) {
  const [showSettings, setShowSettings] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)
  const { agents, tasks, unreadTaskCount, unreadSandboxCount, markAllSandboxesRead } = useAgentStore()
  const { totalUnreadCount } = useChatStore()
  const { balance, tokenSymbol, summary } = useWalletStore()
  const { lastTaskExpense, lastTaskIncome } = summary
  const { wanNodes } = useNetworkStore()
  
  const onlineLocal = agents.filter(a => 
    a.status === 'online' || a.status === 'busy' || a.status === 'idle'
  ).length
  
  const onlineNodes = wanNodes.filter(n => n.status !== 'offline').length
  const realTasks = tasks.filter(t => !isDemoId(t.id))
  const runningTasks = realTasks.filter(t => t.status === 'running').length

  // Background sync every 15s — diff-aware stores skip no-op updates,
  // so this won't cause re-renders unless data actually changed.
  useEffect(() => {
    const interval = setInterval(() => {
      useAgentStore.getState().loadTasksFromDB()
      useAgentStore.getState().loadAgentsFromDB()
      useNetworkStore.getState().syncPeersFromBackend()
    }, 15_000)
    return () => clearInterval(interval)
  }, [])

  const formatDate = (timestamp: number) => {
    const date = new Date(timestamp)
    const now = new Date()
    const diffDays = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24))
    
    if (diffDays === 0) return 'Today'
    if (diffDays === 1) return 'Yesterday'
    if (diffDays < 7) return `${diffDays} days ago`
    return date.toLocaleDateString()
  }

  const navItems = [
    { 
      id: 'chat' as ViewMode, 
      label: 'Chat', 
      icon: ChatBubbleLeftIcon,
      badge: totalUnreadCount > 0 ? totalUnreadCount : undefined,
      badgeColor: 'bg-red-500',
    },
    { 
      id: 'dashboard' as ViewMode, 
      label: 'Dashboard', 
      icon: ChartBarIcon,
      badge: unreadTaskCount > 0 ? unreadTaskCount : undefined,
      badgeColor: 'bg-red-500',
    },
    { 
      id: 'sandbox' as ViewMode, 
      label: 'Sandbox', 
      icon: ServerIcon,
      badge: unreadSandboxCount > 0 ? unreadSandboxCount : undefined,
      badgeColor: 'bg-orange-500',
    },
    { 
      id: 'docs' as ViewMode, 
      label: 'Docs', 
      icon: BookOpenIcon,
    },
  ]

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={onToggle}
        />
      )}

      {/* Sidebar */}
      <aside
        className={clsx(
          'fixed lg:relative z-50 h-full bg-dark-surface border-r border-dark-border',
          'flex flex-col transition-all duration-300 ease-in-out',
          isOpen ? 'w-72' : 'w-0 lg:w-0',
          'overflow-hidden'
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <span className="text-white font-bold text-sm">T</span>
            </div>
            <span className="font-semibold text-white">Teaming24</span>
          </div>
          <button
            onClick={onToggle}
            className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
          >
            <Bars3Icon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Navigation */}
        <div className="p-3 space-y-1">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => {
                onViewModeChange(item.id)
                // Clear unread badges when navigating to the view
                if (item.id === 'sandbox') markAllSandboxesRead()
              }}
              className={clsx(
                'w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-colors',
                viewMode === item.id
                  ? 'bg-primary-500/20 text-primary-400'
                  : 'text-gray-400 hover:bg-dark-hover hover:text-gray-200'
              )}
            >
              <div className="flex items-center gap-3">
                <div className="relative">
                  <item.icon className="w-5 h-5" />
                  {item.badge !== undefined && item.badge > 0 && (
                    <span className={clsx(
                      'absolute -top-1.5 -right-1.5 flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[10px] font-bold text-white rounded-full',
                      item.badgeColor || 'bg-red-500'
                    )}>
                      {item.badge > 9 ? '9+' : item.badge}
                    </span>
                  )}
                </div>
                <span className="font-medium">{item.label}</span>
              </div>
            </button>
          ))}
        </div>

        {/* Wallet Card */}
        <div className="px-3 py-2">
          <div className="p-3 rounded-lg bg-gradient-to-br from-primary-500/10 to-purple-500/10 border border-primary-500/20">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <WalletIcon className="w-4 h-4 text-primary-400" />
                <span className="text-xs text-gray-400 font-medium">Wallet</span>
              </div>
              <span className="text-lg font-bold text-white">{formatUSDC(balance)} {tokenSymbol}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1 text-red-400">
                <ArrowTrendingDownIcon className="w-3 h-3" />
                <span>-{formatUSDC(lastTaskExpense)}</span>
              </div>
              <div className="flex items-center gap-1 text-green-400">
                <ArrowTrendingUpIcon className="w-3 h-3" />
                <span>+{formatUSDC(lastTaskIncome)}</span>
              </div>
            </div>
            <div className="mt-2 text-[10px] text-gray-500 text-center">
              Last task fees ({tokenSymbol})
            </div>
          </div>
        </div>

        {/* Status Cards */}
        <div className="px-3 py-2 space-y-2">
          {/* Local Agents */}
          <div className="p-3 rounded-lg bg-dark-bg border border-dark-border">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <UserGroupIcon className="w-4 h-4 text-gray-500" />
                <span className="text-xs text-gray-500 font-medium">Local Agents</span>
              </div>
              <div className="text-sm font-medium">
                <span className={onlineLocal > 0 ? 'text-green-400' : 'text-gray-500'}>{onlineLocal}</span>
                <span className="text-gray-600">/</span>
                <span className="text-gray-400">{agents.length}</span>
              </div>
            </div>
          </div>

          {/* AgentaNet */}
          <div className="p-3 rounded-lg bg-dark-bg border border-dark-border">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <GlobeAltIcon className="w-4 h-4 text-orange-400" />
                <span className="text-xs text-gray-500 font-medium">AgentaNet</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium">
                  <span className={onlineNodes > 0 ? 'text-orange-400' : 'text-gray-500'}>{onlineNodes}</span>
                  <span className="text-gray-600">/</span>
                  <span className="text-gray-400">{wanNodes.length}</span>
                </div>
                <NetworkStatusSwitch compact />
              </div>
            </div>
          </div>
        </div>

        {/* Divider */}
        <div className="px-3 py-2">
          <div className="h-px bg-dark-border" />
        </div>

        {/* Chat Section */}
        {viewMode === 'chat' && (
          <>
            <div className="px-3 pb-2">
              <button
                onClick={onNewChat}
                className="w-full flex items-center gap-3 px-4 py-3 bg-primary-600 hover:bg-primary-700 
                           text-white rounded-lg transition-colors font-medium"
              >
                <PlusIcon className="w-5 h-5" />
                New Chat
              </button>
            </div>

            <div className="flex-1 overflow-y-auto thin-scrollbar px-3 pb-4">
              <p className="text-xs text-gray-500 font-medium px-1 mb-2">Recent Chats</p>
              {sessions.length === 0 ? (
                <div className="text-center text-gray-500 mt-4">
                  <ChatBubbleLeftIcon className="w-8 h-8 mx-auto mb-2 opacity-50" />
                  <p className="text-xs">No conversations yet</p>
                </div>
              ) : (
                <div className="space-y-1">
                  {sessions.map((session) => (
                    <div
                      key={session.id}
                      className={clsx(
                        'group flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors',
                        session.id === activeSessionId
                          ? 'bg-dark-hover'
                          : 'hover:bg-dark-hover/50'
                      )}
                      onClick={() => onSelectSession(session.id)}
                    >
                      <ChatBubbleLeftIcon className="w-4 h-4 text-gray-400 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm text-gray-200 truncate flex-1">{session.title}</p>
                          {(session.unreadCount || 0) > 0 && (
                            <span className="flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-bold text-white bg-primary-500 rounded-full shrink-0">
                              {session.unreadCount > 9 ? '9+' : session.unreadCount}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-500">{formatDate(session.updatedAt)}</p>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          e.preventDefault()
                          setDeleteConfirmId(session.id)
                        }}
                        className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-500/20 
                                   rounded transition-all"
                      >
                        <TrashIcon className="w-4 h-4 text-red-400" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* Dashboard quick info */}
        {viewMode === 'dashboard' && (
          <div className="flex-1 overflow-y-auto thin-scrollbar px-3 pb-4">
            <p className="text-xs text-gray-500 font-medium px-1 mb-2">Task Status</p>
            <div className="space-y-2">
              <div className="p-3 rounded-lg bg-dark-bg">
                <p className="text-xs text-gray-500">Running</p>
                <p className="text-lg font-semibold text-blue-400">{runningTasks}</p>
              </div>
              <div className="p-3 rounded-lg bg-dark-bg">
                <p className="text-xs text-gray-500">Pending</p>
                <p className="text-lg font-semibold text-yellow-400">
                  {realTasks.filter(t => t.status === 'pending').length}
                </p>
              </div>
              <div className="p-3 rounded-lg bg-dark-bg">
                <p className="text-xs text-gray-500">Completed</p>
                <p className="text-lg font-semibold text-green-400">
                  {realTasks.filter(t => t.status === 'completed').length}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="p-4 border-t border-dark-border">
          <button 
            onClick={() => setShowSettings(true)}
            className="w-full flex items-center gap-3 px-3 py-2 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors"
          >
            <Cog6ToothIcon className="w-5 h-5" />
            <span className="text-sm">Settings</span>
          </button>
          <div className="text-xs text-gray-500 text-center mt-2">
            Teaming24 v0.1.0
          </div>
        </div>
      </aside>
      
      {/* Settings Dialog */}
      <SettingsDialog 
        isOpen={showSettings} 
        onClose={() => setShowSettings(false)} 
      />

      {/* Delete Chat Confirmation Dialog */}
      <Transition appear show={deleteConfirmId !== null} as={Fragment}>
        <Dialog as="div" className="relative z-50" onClose={() => setDeleteConfirmId(null)}>
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
                    <div className="p-2 rounded-lg bg-red-500/20">
                      <ExclamationTriangleIcon className="w-5 h-5 text-red-400" />
                    </div>
                    <div className="flex-1">
                      <Dialog.Title className="text-sm font-semibold text-white">
                        Delete Chat
                      </Dialog.Title>
                      <p className="mt-1 text-xs text-gray-400">
                        Are you sure you want to delete this chat? This action cannot be undone.
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 flex justify-end gap-2">
                    <button
                      onClick={() => setDeleteConfirmId(null)}
                      className="px-3 py-1.5 text-xs font-medium text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => {
                        if (deleteConfirmId) {
                          onDeleteSession(deleteConfirmId)
                        }
                        setDeleteConfirmId(null)
                      }}
                      className="px-3 py-1.5 text-xs font-medium rounded-lg transition-colors bg-red-600 hover:bg-red-700 text-white"
                    >
                      Delete
                    </button>
                  </div>
                </Dialog.Panel>
              </Transition.Child>
            </div>
          </div>
        </Dialog>
      </Transition>
    </>
  )
}
