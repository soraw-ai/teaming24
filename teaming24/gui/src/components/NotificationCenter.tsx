/**
 * Notification Center - Displays and manages system notifications.
 */

import { useState, useRef, useEffect } from 'react'
import {
  BellIcon,
  XMarkIcon,
  CheckIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XCircleIcon,
  TrashIcon,
} from '@heroicons/react/24/outline'
import { BellIcon as BellIconSolid } from '@heroicons/react/24/solid'
import clsx from 'clsx'
import { useNotificationStore, type Notification, type NotificationType } from '../store/notificationStore'

interface NotificationCenterProps {
  onNavigate?: (viewMode: string) => void
}

const typeConfig: Record<NotificationType, { 
  icon: React.ElementType
  iconColor: string
  bgColor: string
}> = {
  info: {
    icon: InformationCircleIcon,
    iconColor: 'text-blue-400',
    bgColor: 'bg-blue-500/10',
  },
  success: {
    icon: CheckCircleIcon,
    iconColor: 'text-green-400',
    bgColor: 'bg-green-500/10',
  },
  warning: {
    icon: ExclamationTriangleIcon,
    iconColor: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10',
  },
  error: {
    icon: XCircleIcon,
    iconColor: 'text-red-400',
    bgColor: 'bg-red-500/10',
  },
}

const defaultTypeConfig = typeConfig.info

function resolveNotificationTypeConfig(type: unknown) {
  if (typeof type === 'string' && type in typeConfig) {
    return typeConfig[type as NotificationType]
  }
  return defaultTypeConfig
}

function formatTime(timestamp: number): string {
  const now = Date.now()
  const diff = now - timestamp
  
  if (diff < 60000) return 'Just now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  
  const date = new Date(timestamp)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function NotificationItem({ 
  notification,
  onNavigate,
}: { 
  notification: Notification
  onNavigate?: (viewMode: string) => void
}) {
  const { markAsRead, removeNotification } = useNotificationStore()
  const config = resolveNotificationTypeConfig(notification.type)
  const Icon = config.icon

  const handleClick = () => {
    if (!notification.read) {
      markAsRead(notification.id)
    }
    if (notification.link?.action) {
      notification.link.action()
    } else if (notification.link?.viewMode && onNavigate) {
      onNavigate(notification.link.viewMode)
    }
  }

  return (
    <div
      className={clsx(
        'group relative p-3 rounded-lg border transition-colors cursor-pointer',
        notification.read
          ? 'bg-dark-bg border-dark-border hover:border-gray-600'
          : 'bg-dark-surface border-dark-border hover:border-primary-500'
      )}
      onClick={handleClick}
    >
      {/* Unread indicator */}
      {!notification.read && (
        <div className="absolute top-3 right-3 w-2 h-2 rounded-full bg-primary-500" />
      )}
      
      <div className="flex gap-3">
        <div className={clsx('p-2 rounded-lg shrink-0', config.bgColor)}>
          <Icon className={clsx('w-4 h-4', config.iconColor)} />
        </div>
        
        <div className="flex-1 min-w-0 pr-6">
          <p className={clsx(
            'text-sm font-medium',
            notification.read ? 'text-gray-400' : 'text-white'
          )}>
            {notification.title}
          </p>
          <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">
            {notification.message}
          </p>
          <div className="flex items-center gap-2 mt-2">
            <span className="text-xs text-gray-500">
              {formatTime(notification.timestamp)}
            </span>
            {notification.link && (
              <span className="text-xs text-primary-400 hover:text-primary-300">
                {notification.link.label} →
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Actions (visible on hover) */}
      <div className="absolute top-2 right-8 opacity-0 group-hover:opacity-100 flex items-center gap-1 transition-opacity">
        {!notification.read && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              markAsRead(notification.id)
            }}
            className="p-1 hover:bg-dark-hover rounded"
            title="Mark as read"
          >
            <CheckIcon className="w-3 h-3 text-gray-400" />
          </button>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation()
            removeNotification(notification.id)
          }}
          className="p-1 hover:bg-red-500/20 rounded"
          title="Remove"
        >
          <TrashIcon className="w-3 h-3 text-red-400" />
        </button>
      </div>
    </div>
  )
}

export default function NotificationCenter({ onNavigate }: NotificationCenterProps) {
  const [isOpen, setIsOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  
  const { notifications, unreadCount, markAllAsRead, clearAll } = useNotificationStore()

  // Close panel when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  return (
    <div className="relative z-[9999]" ref={panelRef}>
      {/* Bell Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={clsx(
          'relative p-2 rounded-lg transition-colors',
          isOpen
            ? 'bg-primary-500/20 text-primary-400'
            : 'hover:bg-dark-hover text-gray-400 hover:text-gray-200'
        )}
      >
        {unreadCount > 0 ? (
          <BellIconSolid className="w-5 h-5" />
        ) : (
          <BellIcon className="w-5 h-5" />
        )}
        
        {/* Badge */}
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[18px] h-[18px] px-1 text-xs font-bold text-white bg-red-500 rounded-full">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown Panel */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-96 bg-dark-surface border border-dark-border rounded-xl shadow-2xl z-[10000] overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-dark-border">
            <h3 className="text-sm font-semibold text-white">Notifications</h3>
            <div className="flex items-center gap-2">
              {unreadCount > 0 && (
                <button
                  onClick={() => { markAllAsRead() }}
                  className="text-xs text-primary-400 hover:text-primary-300 hover:underline"
                >
                  Mark all read
                </button>
              )}
              {notifications.length > 0 && (
                <button
                  onClick={clearAll}
                  className="text-xs text-gray-500 hover:text-gray-400"
                >
                  Clear all
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 hover:bg-dark-hover rounded"
              >
                <XMarkIcon className="w-4 h-4 text-gray-400" />
              </button>
            </div>
          </div>

          {/* Notifications List */}
          <div className="max-h-96 overflow-y-auto thin-scrollbar p-2 space-y-2">
            {notifications.length === 0 ? (
              <div className="py-8 text-center">
                <BellIcon className="w-10 h-10 text-gray-600 mx-auto mb-2" />
                <p className="text-sm text-gray-500">No notifications</p>
                <p className="text-xs text-gray-600 mt-1">
                  You're all caught up!
                </p>
              </div>
            ) : (
              notifications.map((notification) => (
                <NotificationItem
                  key={notification.id}
                  notification={notification}
                  onNavigate={(viewMode) => {
                    setIsOpen(false)
                    onNavigate?.(viewMode)
                  }}
                />
              ))
            )}
          </div>

          {/* Footer */}
          {notifications.length > 0 && (
            <div className="px-4 py-2 border-t border-dark-border text-center">
              <span className="text-xs text-gray-500">
                {notifications.length} notification{notifications.length !== 1 ? 's' : ''}
                {unreadCount > 0 && ` • ${unreadCount} unread`}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
