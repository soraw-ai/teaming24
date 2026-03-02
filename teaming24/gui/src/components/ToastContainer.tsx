/**
 * Toast Container - Displays toast notifications in the top-right corner.
 * 
 * Features:
 * - Auto-dismiss after duration (default 5s)
 * - Manual dismiss by clicking X
 * - Stacks multiple toasts vertically
 * - Animated enter/exit
 */

import { useState, useEffect } from 'react'
import {
  XMarkIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useNotificationStore, type Toast, type NotificationType } from '../store/notificationStore'

const typeConfig: Record<NotificationType, {
  icon: React.ElementType
  iconColor: string
  bgColor: string
  borderColor: string
  progressColor: string
}> = {
  info: {
    icon: InformationCircleIcon,
    iconColor: 'text-blue-400',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/30',
    progressColor: 'bg-blue-500',
  },
  success: {
    icon: CheckCircleIcon,
    iconColor: 'text-green-400',
    bgColor: 'bg-green-500/10',
    borderColor: 'border-green-500/30',
    progressColor: 'bg-green-500',
  },
  warning: {
    icon: ExclamationTriangleIcon,
    iconColor: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    progressColor: 'bg-yellow-500',
  },
  error: {
    icon: XCircleIcon,
    iconColor: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    progressColor: 'bg-red-500',
  },
}

const defaultTypeConfig = typeConfig.info

function resolveToastTypeConfig(type: unknown) {
  if (typeof type === 'string' && type in typeConfig) {
    return typeConfig[type as NotificationType]
  }
  return defaultTypeConfig
}

function ToastItem({ toast }: { toast: Toast }) {
  const { removeToast } = useNotificationStore()
  const [isVisible, setIsVisible] = useState(false)
  const [progress, setProgress] = useState(100)
  
  const config = resolveToastTypeConfig(toast.type)
  const Icon = config.icon

  // Animate in
  useEffect(() => {
    const timer = setTimeout(() => setIsVisible(true), 10)
    return () => clearTimeout(timer)
  }, [])

  // Progress bar countdown
  useEffect(() => {
    if (toast.duration <= 0) return
    
    const startTime = Date.now()
    const endTime = startTime + toast.duration
    
    const updateProgress = () => {
      const now = Date.now()
      const remaining = Math.max(0, endTime - now)
      const percent = (remaining / toast.duration) * 100
      setProgress(percent)
      
      if (percent > 0) {
        requestAnimationFrame(updateProgress)
      }
    }
    
    const frame = requestAnimationFrame(updateProgress)
    return () => cancelAnimationFrame(frame)
  }, [toast.duration])

  const handleClose = () => {
    setIsVisible(false)
    // Wait for animation to complete before removing
    setTimeout(() => removeToast(toast.id), 200)
  }

  const handleAction = () => {
    try {
      toast.onAction?.()
    } catch (error) {
      console.error('[ToastContainer] Toast action failed:', error)
    } finally {
      handleClose()
    }
  }

  return (
    <div
      className={clsx(
        'relative w-80 rounded-lg border shadow-lg overflow-hidden',
        'bg-dark-surface/95 backdrop-blur-sm transform-gpu',
        config.borderColor,
        isVisible
          ? 'animate-slide-in'
          : 'opacity-0 translate-x-full transition-all duration-200'
      )}
    >
      <div className="flex items-start gap-3 p-3">
        {/* Icon */}
        <div className={clsx('p-1.5 rounded-lg shrink-0', config.bgColor)}>
          <Icon className={clsx('w-4 h-4', config.iconColor)} />
        </div>
        
        {/* Content */}
        <div className="flex-1 min-w-0 pt-0.5">
          <p className="text-sm font-medium text-white truncate">
            {toast.title}
          </p>
          <p className="text-xs text-gray-400 mt-0.5 line-clamp-2">
            {toast.message}
          </p>
          {toast.actionLabel && toast.onAction && (
            <button
              onClick={handleAction}
              className="mt-1.5 text-[11px] font-medium text-primary-300 hover:text-primary-200 transition-colors"
            >
              {toast.actionLabel}
            </button>
          )}
        </div>
        
        {/* Close Button */}
        <button
          onClick={handleClose}
          className="p-1 hover:bg-dark-hover rounded transition-colors shrink-0"
        >
          <XMarkIcon className="w-4 h-4 text-gray-400 hover:text-white" />
        </button>
      </div>
      
      {/* Progress Bar - shrinks from right to left as time runs out */}
      {toast.duration > 0 && (
        <div className="h-0.5 bg-dark-border overflow-hidden">
          <div
            className={clsx('h-full', config.progressColor)}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </div>
  )
}

export default function ToastContainer() {
  const { toasts } = useNotificationStore()

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-[999999] flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <ToastItem toast={toast} />
        </div>
      ))}
    </div>
  )
}
