import { useState, useEffect, lazy, Suspense } from 'react'
import { Bars3Icon } from '@heroicons/react/24/outline'
import Sidebar from './components/Sidebar'
import ChatView, { ApprovalCard } from './components/ChatView'
import WalletButton from './components/WalletButton'
import NotificationCenter from './components/NotificationCenter'
import ToastContainer from './components/ToastContainer'
import ErrorDetailModal from './components/ErrorDetailModal'
import { useChatStore } from './store/chatStore'
import { useAgentStore } from './store/agentStore'
import { useNetworkStore } from './store/networkStore'
import { useConfigStore } from './store/configStore'
import { useSettingsStore } from './store/settingsStore'
import { notify } from './store/notificationStore'
import { useDataInitialization } from './store/dataStore'
import type { ViewMode } from './types'

const Dashboard = lazy(() => import('./components/dashboard/Dashboard'))
const SandboxMonitor = lazy(() => import('./components/SandboxMonitor'))
const DocsView = lazy(() => import('./components/DocsView'))

// Page titles for each view
const VIEW_TITLES: Record<ViewMode, { title: string; subtitle?: string }> = {
  chat: { title: 'Chat', subtitle: 'AI Assistant' },
  dashboard: { title: 'Dashboard', subtitle: 'Overview' },
  sandbox: { title: 'Sandbox', subtitle: 'Runtime Monitor' },
  docs: { title: 'Documentation', subtitle: 'Guides & API' },
}

function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [viewMode, setViewMode] = useState<ViewMode>('chat')
  const { sessions, activeSessionId, createSession, setActiveSession, deleteSession, setSessionApproval } = useChatStore()
  const { getApiUrl } = useConfigStore()
  const dashboardApproval = useAgentStore(s => s.dashboardApproval)
  const setDashboardApproval = useAgentStore(s => s.setDashboardApproval)

  // Find any session with pending approval (for global overlay when on Dashboard etc.)
  const sessionWithApproval = sessions.find((s) => s.pendingApproval)
  const globalApproval = sessionWithApproval?.pendingApproval ?? dashboardApproval
  const approvalSessionId = sessionWithApproval?.id
  
  // Initialize all data stores at app startup (synchronous)
  useDataInitialization()

  // Cross-tab approval sync: when another tab sets approval, update our store
  useEffect(() => {
    try {
      const bc = new BroadcastChannel('teaming24-approval-sync')
      bc.onmessage = (e) => {
        const d = e?.data
        if (d?.type === 'approval' && d.sessionId != null) {
          useChatStore.getState()._applyApprovalFromSync(d.sessionId, d.approval ?? null)
        }
      }
      return () => bc.close()
    } catch {
      return () => {}
    }
  }, [])

  // Auto-connect to AgentaNet on startup (controlled by config + settings)
  useEffect(() => {
    // Wait a tick for config & settings stores to hydrate from backend/localStorage
    const timer = setTimeout(() => {
      const yamlDefault = useConfigStore.getState().autoOnline
      const userOverride = useSettingsStore.getState().autoConnectOnStartup
      // User setting takes priority; fall back to YAML config
      const shouldAutoConnect = userOverride ?? yamlDefault
      if (shouldAutoConnect) {
        const { status, goOnline } = useNetworkStore.getState()
        if (status === 'offline') {
          goOnline().catch((e) => console.warn('Auto-connect failed:', e))
        }
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [])

  // Add welcome notification on first load
  useEffect(() => {
    const hasSeenWelcome = localStorage.getItem('teaming24_welcomed')
    if (!hasSeenWelcome) {
      notify.info(
        'Welcome to Teaming24',
        'Get started by creating a new chat or exploring the Dashboard.',
        { label: 'Go to Dashboard', viewMode: 'dashboard' }
      )
      localStorage.setItem('teaming24_welcomed', 'true')
    }
  }, [])

  const handleNavigate = (mode: string) => {
    setViewMode(mode as ViewMode)
  }

  return (
    <div className="flex h-full w-full bg-dark-bg">
      {/* Sidebar */}
      <Sidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onNewChat={createSession}
        onSelectSession={setActiveSession}
        onDeleteSession={deleteSession}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
      />

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Unified Header */}
        <header className="relative z-[9999] flex items-center justify-between px-4 py-3 border-b border-dark-border bg-dark-surface/80 backdrop-blur-sm">
          <div className="flex items-center gap-4">
            {!sidebarOpen && (
              <button
                onClick={() => setSidebarOpen(true)}
                className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
              >
                <Bars3Icon className="w-5 h-5 text-gray-400" />
              </button>
            )}
            <div>
              <h1 className="text-lg font-semibold text-white">
                {VIEW_TITLES[viewMode]?.title || 'Teaming24'}
              </h1>
              {VIEW_TITLES[viewMode]?.subtitle && (
                <p className="text-xs text-gray-500">{VIEW_TITLES[viewMode].subtitle}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <WalletButton />
            <NotificationCenter onNavigate={handleNavigate} />
          </div>
        </header>
        
        {/* Views — z-0 creates a stacking context below the header (z-[9999])
            so dropdown panels from WalletButton / NotificationCenter always
            render above the view content, even in ChatView.

            ChatView is always mounted (hidden via CSS) so SSE connections,
            approval cards, and streaming state survive navigation to other
            views and back. Other views mount/unmount as usual. */}
        <div className="flex-1 overflow-hidden relative z-0">
          <div className={viewMode === 'chat' ? 'h-full' : 'hidden'}>
            <ChatView />
          </div>
          <Suspense fallback={<div className="h-full w-full animate-pulse bg-dark-bg/40" />}>
            {viewMode === 'dashboard' && <Dashboard />}
            {viewMode === 'sandbox' && <SandboxMonitor />}
            {viewMode === 'docs' && <DocsView />}
          </Suspense>
        </div>
      </main>
      
      {/* Toast Notifications (top-right corner) */}
      <ToastContainer />
      <ErrorDetailModal />

      {/* Global approval overlay — shows for chat session OR dashboard task approvals */}
      {globalApproval && (
        <div className="fixed inset-0 z-[999999] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="max-w-lg w-full animate-in fade-in zoom-in-95 duration-200">
            <ApprovalCard
              approval={globalApproval}
              onResolve={async (decision: string, budget?: number) => {
                if (globalApproval?.id) {
                  try {
                    const body: { decision: string; budget?: number } = { decision }
                    if (budget != null && budget > 0) body.budget = budget
                    await fetch(getApiUrl(`/agent/approvals/${globalApproval.id}/resolve`), {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(body),
                    })
                  } catch (e) {
                    console.error('Failed to resolve approval:', e)
                  }
                }
                if (approvalSessionId) setSessionApproval(approvalSessionId, null)
                setDashboardApproval(null)
              }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

export default App
