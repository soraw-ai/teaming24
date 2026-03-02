/**
 * Notification Store - Manages system notifications and toasts.
 */

import { create } from 'zustand'
import { prefixedId } from '../utils/ids'

export type NotificationType = 'info' | 'success' | 'warning' | 'error'

export interface Notification {
  id: string
  type: NotificationType
  title: string
  message: string
  timestamp: number
  read: boolean
  // Optional link to navigate to
  link?: {
    label: string
    viewMode?: string
    action?: () => void
  }
}

export interface Toast {
  id: string
  type: NotificationType
  title: string
  message: string
  timestamp: number
  duration: number // milliseconds, 0 = no auto-dismiss
  actionLabel?: string
  onAction?: () => void
}

interface NotificationState {
  notifications: Notification[]
  unreadCount: number
  toasts: Toast[]
  
  // Actions
  addNotification: (notification: Omit<Notification, 'id' | 'timestamp' | 'read'>) => void
  markAsRead: (id: string) => void
  markAllAsRead: () => void
  removeNotification: (id: string) => void
  clearAll: () => void
  
  // Toast Actions
  addToast: (toast: Omit<Toast, 'id' | 'timestamp'>) => string
  removeToast: (id: string) => void
  clearToasts: () => void
}

export const useNotificationStore = create<NotificationState>()((set, _get) => ({
  notifications: [],
  unreadCount: 0,
  toasts: [],

  addNotification: (notification) => {
    const id = prefixedId('notif', 12)
    const newNotification: Notification = {
      ...notification,
      id,
      timestamp: Date.now(),
      read: false,
    }
    
    set((state) => ({
      notifications: [newNotification, ...state.notifications].slice(0, 50), // Keep max 50
      unreadCount: state.unreadCount + 1,
    }))
  },

  markAsRead: (id) => {
    set((state) => {
      const notification = state.notifications.find(n => n.id === id)
      if (notification && !notification.read) {
        return {
          notifications: state.notifications.map(n =>
            n.id === id ? { ...n, read: true } : n
          ),
          unreadCount: Math.max(0, state.unreadCount - 1),
        }
      }
      return state
    })
  },

  markAllAsRead: () => {
    set((state) => ({
      notifications: state.notifications.map(n => ({ ...n, read: true })),
      unreadCount: 0,
    }))
  },

  removeNotification: (id) => {
    set((state) => {
      const notification = state.notifications.find(n => n.id === id)
      const wasUnread = notification && !notification.read
      return {
        notifications: state.notifications.filter(n => n.id !== id),
        unreadCount: wasUnread ? Math.max(0, state.unreadCount - 1) : state.unreadCount,
      }
    })
  },

  clearAll: () => {
    set({ notifications: [], unreadCount: 0 })
  },
  
  // Toast Actions
  addToast: (toast) => {
    const id = prefixedId('toast', 12)
    const newToast: Toast = {
      ...toast,
      id,
      timestamp: Date.now(),
    }
    
    set((state) => ({
      toasts: [...state.toasts, newToast].slice(-10), // Keep max 10 toasts
    }))
    
    // Auto-remove after duration (if duration > 0)
    if (toast.duration > 0) {
      setTimeout(() => {
        set((state) => ({
          toasts: state.toasts.filter(t => t.id !== id),
        }))
      }, toast.duration)
    }
    
    return id
  },
  
  removeToast: (id) => {
    set((state) => ({
      toasts: state.toasts.filter(t => t.id !== id),
    }))
  },
  
  clearToasts: () => {
    set({ toasts: [] })
  },
}))

// Default toast duration (5 seconds)
const DEFAULT_TOAST_DURATION = 5000

// Helper to create typed notifications (adds both notification and toast)
export const notify = {
  info: (title: string, message: string, link?: Notification['link']) => {
    useNotificationStore.getState().addNotification({ type: 'info', title, message, link })
    useNotificationStore.getState().addToast({
      type: 'info',
      title,
      message,
      duration: DEFAULT_TOAST_DURATION,
      actionLabel: link?.action ? link.label : undefined,
      onAction: link?.action,
    })
  },
  
  success: (title: string, message: string, link?: Notification['link']) => {
    useNotificationStore.getState().addNotification({ type: 'success', title, message, link })
    useNotificationStore.getState().addToast({
      type: 'success',
      title,
      message,
      duration: DEFAULT_TOAST_DURATION,
      actionLabel: link?.action ? link.label : undefined,
      onAction: link?.action,
    })
  },
  
  warning: (title: string, message: string, link?: Notification['link']) => {
    useNotificationStore.getState().addNotification({ type: 'warning', title, message, link })
    useNotificationStore.getState().addToast({
      type: 'warning',
      title,
      message,
      duration: DEFAULT_TOAST_DURATION,
      actionLabel: link?.action ? link.label : undefined,
      onAction: link?.action,
    })
  },
  
  error: (title: string, message: string, link?: Notification['link']) => {
    useNotificationStore.getState().addNotification({ type: 'error', title, message, link })
    // Errors stay a bit longer (8 seconds)
    useNotificationStore.getState().addToast({
      type: 'error',
      title,
      message,
      duration: 8000,
      actionLabel: link?.action ? link.label : undefined,
      onAction: link?.action,
    })
  },
}

// Helper for toast-only notifications (no persistent notification)
export const toast = {
  info: (
    title: string,
    message: string,
    duration = DEFAULT_TOAST_DURATION,
    action?: { label: string; onClick: () => void }
  ) =>
    useNotificationStore.getState().addToast({
      type: 'info',
      title,
      message,
      duration,
      actionLabel: action?.label,
      onAction: action?.onClick,
    }),
  
  success: (
    title: string,
    message: string,
    duration = DEFAULT_TOAST_DURATION,
    action?: { label: string; onClick: () => void }
  ) =>
    useNotificationStore.getState().addToast({
      type: 'success',
      title,
      message,
      duration,
      actionLabel: action?.label,
      onAction: action?.onClick,
    }),
  
  warning: (
    title: string,
    message: string,
    duration = DEFAULT_TOAST_DURATION,
    action?: { label: string; onClick: () => void }
  ) =>
    useNotificationStore.getState().addToast({
      type: 'warning',
      title,
      message,
      duration,
      actionLabel: action?.label,
      onAction: action?.onClick,
    }),
  
  error: (
    title: string,
    message: string,
    duration = 8000,
    action?: { label: string; onClick: () => void }
  ) =>
    useNotificationStore.getState().addToast({
      type: 'error',
      title,
      message,
      duration,
      actionLabel: action?.label,
      onAction: action?.onClick,
    }),
}
