import { useEffect, useState } from 'react'
import { useAuthStore } from '../store/authStore'
import { useAdminStore } from '../store/adminStore'
import {
  ArrowRightOnRectangleIcon,
  UsersIcon,
  KeyIcon,
  ServerStackIcon,
  ChartBarIcon,
  TrashIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  XCircleIcon,
  SignalIcon,
  ShieldCheckIcon,
  Cog6ToothIcon,
  DocumentTextIcon,
  PlusIcon,
  PencilIcon,
} from '@heroicons/react/24/outline'

type TabId = 'stats' | 'users' | 'tokens' | 'nodes' | 'settings' | 'docs' | 'audit'

// Inline Audit Tab component
function AuditTab() {
  const [entries, setEntries] = useState<Array<{
    id: string; timestamp: number; user_id: string | null; action: string;
    target_type: string | null; target_id: string | null;
    ip_address: string | null; details: string | null;
  }>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadAudit = () => {
    setLoading(true)
    setError(null)
    fetch('/api/admin/audit?page_size=100')
      .then(r => {
        if (r.status === 429) throw new Error('Rate limited — too many requests. Please wait a moment.')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(data => setEntries(data.items ?? data))
      .catch((e: Error) => {
        console.error('Failed to load audit log:', e)
        setError(e.message || 'Failed to load audit log')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadAudit() }, [])

  const formatTime = (ts: number) => new Date(ts * 1000).toLocaleString()

  if (loading) return <div className="text-center py-12 text-gray-500">Loading audit log...</div>

  if (error) return (
    <div className="text-center py-12">
      <p className="text-red-400 text-sm mb-4">{error}</p>
      <button
        onClick={loadAudit}
        className="px-4 py-2 bg-dark-hover hover:bg-dark-border text-gray-300 rounded-lg text-sm transition-colors inline-flex items-center gap-2"
      >
        <ArrowPathIcon className="w-4 h-4" /> Retry
      </button>
    </div>
  )

  return (
    <div className="bg-dark-surface rounded-xl border border-dark-border overflow-x-auto">
      <table className="w-full min-w-[700px]">
        <thead className="bg-dark-bg">
          <tr>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Time</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Action</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Target</th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">IP</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-dark-border">
          {entries.map(e => (
            <tr key={e.id} className="hover:bg-dark-hover/50 transition-colors">
              <td className="px-6 py-4 text-sm text-gray-400 whitespace-nowrap">{formatTime(e.timestamp)}</td>
              <td className="px-6 py-4"><span className="font-mono text-sm text-primary-400">{e.action}</span></td>
              <td className="px-6 py-4 text-sm text-gray-300">{e.target_type ? `${e.target_type}:${e.target_id || '?'}` : '-'}</td>
              <td className="px-6 py-4 text-sm text-gray-500 font-mono">{e.ip_address || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {entries.length === 0 && <div className="px-6 py-12 text-center text-gray-500">No audit log entries</div>}
    </div>
  )
}

export default function AdminDashboard() {
  const { user, logout } = useAuthStore()
  const {
    stats, users, tokens, nodes, settings, docs, isLoading,
    fetchStats, fetchUsers, fetchTokens, fetchNodes, fetchSettings, fetchDocs,
    deleteUser, deleteNode, updateSetting, deleteSetting,
    createDoc, updateDoc, deleteDoc
  } = useAdminStore()
  const [activeTab, setActiveTab] = useState<TabId>('stats')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  
  // Settings state
  const [editingSetting, setEditingSetting] = useState<string | null>(null)
  const [settingValue, setSettingValue] = useState('')
  const [newSettingKey, setNewSettingKey] = useState('')
  const [newSettingValue, setNewSettingValue] = useState('')
  
  // Docs state
  const [editingDoc, setEditingDoc] = useState<string | null>(null)
  const [docTitle, setDocTitle] = useState('')
  const [docContent, setDocContent] = useState('')
  const [docCategory, setDocCategory] = useState('')
  const [showNewDoc, setShowNewDoc] = useState(false)
  const [newDocSlug, setNewDocSlug] = useState('')
  const [newDocTitle, setNewDocTitle] = useState('')
  const [newDocContent, setNewDocContent] = useState('')
  const [newDocCategory, setNewDocCategory] = useState('')

  // Reset state
  const RESET_CONFIRM_PHRASE = 'RESET ALL DATA'
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [resetConfirmText, setResetConfirmText] = useState('')
  const [resetStatus, setResetStatus] = useState<'idle' | 'loading' | 'success'>('idle')
  const [resetError, setResetError] = useState<string | null>(null)

  useEffect(() => {
    fetchStats()
    fetchUsers()
    fetchTokens()
    fetchNodes()
    fetchSettings()
    fetchDocs()
  }, [fetchStats, fetchUsers, fetchTokens, fetchNodes, fetchSettings, fetchDocs])

  const tabs = [
    { id: 'stats' as TabId, label: 'Overview', icon: ChartBarIcon },
    { id: 'users' as TabId, label: 'Users', icon: UsersIcon, count: stats?.users.total },
    { id: 'tokens' as TabId, label: 'Tokens', icon: KeyIcon, count: stats?.tokens.total },
    { id: 'nodes' as TabId, label: 'Nodes', icon: ServerStackIcon, count: stats?.nodes.total },
    { id: 'audit' as TabId, label: 'Audit', icon: ShieldCheckIcon, count: stats?.audit_entries },
    { id: 'settings' as TabId, label: 'Settings', icon: Cog6ToothIcon },
    { id: 'docs' as TabId, label: 'Docs', icon: DocumentTextIcon, count: docs.length },
  ]

  const formatDate = (ts: number | null) => ts ? new Date(ts * 1000).toLocaleString() : '-'
  const formatRelative = (ts: number | null) => {
    if (!ts) return '-'
    const diff = Date.now() / 1000 - ts
    if (diff < 60) return 'Just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
  }

  const handleSaveSetting = async (key: string) => {
    await updateSetting(key, settingValue)
    setEditingSetting(null)
  }

  const handleCreateSetting = async () => {
    if (newSettingKey && newSettingValue) {
      await updateSetting(newSettingKey, newSettingValue)
      setNewSettingKey('')
      setNewSettingValue('')
    }
  }

  const handleCreateDoc = async () => {
    if (newDocSlug && newDocTitle) {
      await createDoc({
        slug: newDocSlug,
        title: newDocTitle,
        content: newDocContent,
        category: newDocCategory || undefined,
      })
      setShowNewDoc(false)
      setNewDocSlug('')
      setNewDocTitle('')
      setNewDocContent('')
      setNewDocCategory('')
    }
  }

  const handleSaveDoc = async (slug: string) => {
    await updateDoc(slug, {
      title: docTitle,
      content: docContent,
      category: docCategory || undefined,
    })
    setEditingDoc(null)
  }

  const handleResetAllData = async () => {
    setResetStatus('loading')
    setResetError(null)
    try {
      const r = await fetch('/api/admin/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: RESET_CONFIRM_PHRASE }),
      })
      if (!r.ok) {
        const data = await r.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${r.status}`)
      }
      setResetStatus('success')
      setTimeout(() => window.location.reload(), 1500)
    } catch (e: unknown) {
      setResetError(e instanceof Error ? e.message : 'Reset failed')
      setResetStatus('idle')
    }
  }

  return (
    <div className="min-h-screen bg-dark-bg">
      {/* Header */}
      <header className="bg-dark-surface border-b border-dark-border sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500 to-red-600 flex items-center justify-center">
              <ShieldCheckIcon className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-white">Admin Dashboard</h1>
              <p className="text-xs text-gray-500">AgentaNet Central Service</p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <span className="px-2 py-1 bg-orange-500/20 text-orange-400 text-xs font-medium rounded">
              Admin
            </span>
            <span className="text-sm text-gray-400">{user?.username}</span>
            <button
              onClick={logout}
              className="p-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
            >
              <ArrowRightOnRectangleIcon className="w-5 h-5" />
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-6">
        {/* Tabs */}
        <div className="flex gap-2 mb-6 border-b border-dark-border pb-4 overflow-x-auto">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-all whitespace-nowrap
                ${activeTab === tab.id
                  ? 'bg-primary-500/20 text-primary-400'
                  : 'text-gray-400 hover:text-white hover:bg-dark-hover'}
              `}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
              {tab.count !== undefined && (
                <span className={`px-2 py-0.5 text-xs rounded-full ${
                  activeTab === tab.id ? 'bg-primary-500/30' : 'bg-dark-hover'
                }`}>
                  {tab.count}
                </span>
              )}
            </button>
          ))}
          
          <button
            onClick={() => {
              fetchStats()
              fetchUsers()
              fetchTokens()
              fetchNodes()
              fetchSettings()
              fetchDocs()
            }}
            disabled={isLoading}
            className="ml-auto p-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
          >
            <ArrowPathIcon className={`w-5 h-5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Stats Tab */}
        {activeTab === 'stats' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="bg-dark-surface rounded-xl border border-dark-border p-6">
                <div className="flex items-center justify-between mb-4">
                  <UsersIcon className="w-8 h-8 text-blue-400" />
                  <span className="text-3xl font-bold text-white">{stats?.users.total || 0}</span>
                </div>
                <p className="text-gray-400">Total Users</p>
              </div>
              
              <div className="bg-dark-surface rounded-xl border border-dark-border p-6">
                <div className="flex items-center justify-between mb-4">
                  <KeyIcon className="w-8 h-8 text-green-400" />
                  <div className="text-right">
                    <span className="text-3xl font-bold text-white">{stats?.tokens.active || 0}</span>
                    <span className="text-gray-500 text-lg">/{stats?.tokens.total || 0}</span>
                  </div>
                </div>
                <p className="text-gray-400">Active Tokens</p>
              </div>
              
              <div className="bg-dark-surface rounded-xl border border-dark-border p-6">
                <div className="flex items-center justify-between mb-4">
                  <ServerStackIcon className="w-8 h-8 text-purple-400" />
                  <div className="text-right">
                    <span className="text-3xl font-bold text-green-400">{stats?.nodes.online || 0}</span>
                    <span className="text-gray-500 text-lg">/{stats?.nodes.total || 0}</span>
                  </div>
                </div>
                <p className="text-gray-400">Online Nodes</p>
              </div>
            </div>

            <div className="bg-dark-surface rounded-xl border border-dark-border p-6">
              <h3 className="text-lg font-semibold text-white mb-4">System Overview</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="p-4 bg-dark-bg rounded-lg">
                  <p className="text-2xl font-bold text-white">{stats?.nodes.listed || 0}</p>
                  <p className="text-sm text-gray-500">Listed Nodes</p>
                </div>
                <div className="p-4 bg-dark-bg rounded-lg">
                  <p className="text-2xl font-bold text-white">{stats?.tokens.revoked || 0}</p>
                  <p className="text-sm text-gray-500">Revoked Tokens</p>
                </div>
                <div className="p-4 bg-dark-bg rounded-lg">
                  <p className="text-2xl font-bold text-white">{settings.length}</p>
                  <p className="text-sm text-gray-500">Settings</p>
                </div>
                <div className="p-4 bg-dark-bg rounded-lg">
                  <p className="text-2xl font-bold text-white">{docs.length}</p>
                  <p className="text-sm text-gray-500">Doc Pages</p>
                </div>
              </div>
            </div>

            {/* Danger Zone */}
            <div className="bg-dark-surface rounded-xl border border-red-900/40 p-6">
              <h3 className="text-lg font-semibold text-red-400 mb-1">Danger Zone</h3>
              <p className="text-sm text-gray-500 mb-4">Irreversible actions that affect all data in this service.</p>
              <div className="flex items-center justify-between p-4 bg-dark-bg rounded-lg border border-red-900/30">
                <div>
                  <p className="text-sm font-medium text-white">Clear All Data</p>
                  <p className="text-xs text-gray-500 mt-0.5">Wipe all users, tokens, nodes, settings, audit logs and docs. Returns service to fresh-deployment state.</p>
                </div>
                <button
                  onClick={() => { setResetDialogOpen(true); setResetConfirmText(''); setResetError(null); setResetStatus('idle') }}
                  className="ml-6 shrink-0 px-4 py-2 bg-red-600/20 hover:bg-red-600/40 text-red-400 border border-red-600/40 hover:border-red-500 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                >
                  <TrashIcon className="w-4 h-4" />
                  Clear All Data
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Users Tab */}
        {activeTab === 'users' && (
          <div className="bg-dark-surface rounded-xl border border-dark-border overflow-x-auto">
            <table className="w-full min-w-[980px]">
              <thead className="bg-dark-bg">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">User</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Email</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Tokens</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Last Login</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Created</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-400 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-border">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-dark-hover/50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                        {u.avatar_url ? (
                          <img src={u.avatar_url} alt="" className="w-8 h-8 rounded-full" />
                        ) : (
                          <div className="w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center">
                            <span className="text-xs text-gray-400">{u.username[0].toUpperCase()}</span>
                          </div>
                        )}
                        <div>
                          <p className="text-sm font-medium text-white">{u.username}</p>
                          {u.is_admin && <span className="text-xs text-orange-400">Admin</span>}
                          {u.is_suspended && <span className="text-xs text-red-400 ml-1">Suspended</span>}
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-400 max-w-[260px] truncate" title={u.email || '-'}>
                      {u.email || '-'}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-300">{u.token_count}</td>
                    <td className="px-6 py-4 text-sm text-gray-400">{formatRelative(u.last_login_at)}</td>
                    <td className="px-6 py-4 text-sm text-gray-400">{formatDate(u.created_at)}</td>
                    <td className="px-6 py-4 text-right">
                      {!u.is_admin && (
                        confirmDelete === u.id ? (
                          <div className="flex items-center justify-end gap-2">
                            <button onClick={() => { deleteUser(u.id); setConfirmDelete(null) }} className="px-2 py-1 bg-red-600 text-white text-xs rounded">Confirm</button>
                            <button onClick={() => setConfirmDelete(null)} className="px-2 py-1 bg-dark-hover text-gray-300 text-xs rounded">Cancel</button>
                          </div>
                        ) : (
                          <button onClick={() => setConfirmDelete(u.id)} className="p-1 text-gray-400 hover:text-red-400 transition-colors">
                            <TrashIcon className="w-4 h-4" />
                          </button>
                        )
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {users.length === 0 && <div className="px-6 py-12 text-center text-gray-500">No users found</div>}
          </div>
        )}

        {/* Tokens Tab */}
        {activeTab === 'tokens' && (
          <div className="bg-dark-surface rounded-xl border border-dark-border overflow-x-auto">
            <table className="w-full min-w-[860px]">
              <thead className="bg-dark-bg">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Node ID</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Owner</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Description</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Last Used</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-border">
                {tokens.map((t) => (
                  <tr key={t.id} className="hover:bg-dark-hover/50 transition-colors">
                    <td className="px-6 py-4">
                      <span className="font-mono text-sm text-primary-400 inline-block max-w-[280px] truncate" title={t.node_id}>
                        {t.node_id}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-300">{t.username}</td>
                    <td className="px-6 py-4 text-sm text-gray-400 max-w-[320px] truncate" title={t.description || '-'}>
                      {t.description || '-'}
                    </td>
                    <td className="px-6 py-4">
                      {t.is_active ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-500/20 text-green-400 text-xs rounded"><CheckCircleIcon className="w-3 h-3" /> Active</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-500/20 text-gray-400 text-xs rounded"><XCircleIcon className="w-3 h-3" /> Inactive</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-400">{formatRelative(t.last_used_at)}</td>
                    <td className="px-6 py-4 text-sm text-gray-400">{formatDate(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {tokens.length === 0 && <div className="px-6 py-12 text-center text-gray-500">No tokens found</div>}
          </div>
        )}

        {/* Nodes Tab */}
        {activeTab === 'nodes' && (
          <div className="bg-dark-surface rounded-xl border border-dark-border overflow-x-auto">
            <table className="w-full min-w-[980px]">
              <thead className="bg-dark-bg">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Node</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Owner</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Endpoint</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Last Seen</th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-400 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-border">
                {nodes.map((n) => (
                  <tr key={n.id} className="hover:bg-dark-hover/50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="max-w-[300px]">
                        <p className="text-sm font-medium text-white truncate" title={n.name}>{n.name}</p>
                        <p className="text-xs text-gray-500 font-mono truncate" title={n.id}>{n.id}</p>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-300">{n.owner_username}</td>
                    <td className="px-6 py-4 text-sm text-gray-400 font-mono truncate max-w-[220px]" title={n.ip && n.port ? `${n.ip}:${n.port}` : '-'}>
                      {n.ip && n.port ? `${n.ip}:${n.port}` : '-'}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        {n.status === 'online' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-500/20 text-green-400 text-xs rounded"><SignalIcon className="w-3 h-3" /> Online</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-500/20 text-gray-400 text-xs rounded">Offline</span>
                        )}
                        {n.is_listed && <span className="px-2 py-0.5 bg-blue-500/20 text-blue-400 text-xs rounded">Listed</span>}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-400">{formatRelative(n.last_seen)}</td>
                    <td className="px-6 py-4 text-right">
                      {confirmDelete === n.id ? (
                        <div className="flex items-center justify-end gap-2">
                          <button onClick={() => { deleteNode(n.id); setConfirmDelete(null) }} className="px-2 py-1 bg-red-600 text-white text-xs rounded">Confirm</button>
                          <button onClick={() => setConfirmDelete(null)} className="px-2 py-1 bg-dark-hover text-gray-300 text-xs rounded">Cancel</button>
                        </div>
                      ) : (
                        <button onClick={() => setConfirmDelete(n.id)} className="p-1 text-gray-400 hover:text-red-400 transition-colors">
                          <TrashIcon className="w-4 h-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {nodes.length === 0 && <div className="px-6 py-12 text-center text-gray-500">No nodes found</div>}
          </div>
        )}

        {/* Audit Tab */}
        {activeTab === 'audit' && (
          <AuditTab />
        )}

        {/* Settings Tab */}
        {activeTab === 'settings' && (
          <div className="space-y-6">
            {/* Add New Setting */}
            <div className="bg-dark-surface rounded-xl border border-dark-border p-6">
              <h3 className="text-lg font-semibold text-white mb-4">Add Setting</h3>
              <div className="flex gap-4">
                <input
                  type="text"
                  value={newSettingKey}
                  onChange={(e) => setNewSettingKey(e.target.value)}
                  placeholder="Key"
                  className="flex-1 px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500"
                />
                <input
                  type="text"
                  value={newSettingValue}
                  onChange={(e) => setNewSettingValue(e.target.value)}
                  placeholder="Value"
                  className="flex-1 px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500"
                />
                <button
                  onClick={handleCreateSetting}
                  disabled={!newSettingKey || !newSettingValue}
                  className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
                >
                  <PlusIcon className="w-4 h-4" /> Add
                </button>
              </div>
            </div>

            {/* Settings List */}
            <div className="bg-dark-surface rounded-xl border border-dark-border overflow-x-auto">
              <table className="w-full min-w-[820px]">
                <thead className="bg-dark-bg">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Key</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Value</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase">Updated</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-400 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-dark-border">
                  {settings.map((s) => (
                    <tr key={s.key} className="hover:bg-dark-hover/50 transition-colors">
                      <td className="px-6 py-4 font-mono text-sm text-primary-400 max-w-[240px] truncate" title={s.key}>{s.key}</td>
                      <td className="px-6 py-4">
                        {editingSetting === s.key ? (
                          <input
                            type="text"
                            value={settingValue}
                            onChange={(e) => setSettingValue(e.target.value)}
                            className="w-full px-3 py-1 bg-dark-bg border border-dark-border rounded text-white"
                            autoFocus
                          />
                        ) : (
                          <span className="text-sm text-gray-300 inline-block max-w-[420px] truncate" title={s.value || '-'}>
                            {s.value || '-'}
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-400">{formatRelative(s.updated_at)}</td>
                      <td className="px-6 py-4 text-right">
                        {editingSetting === s.key ? (
                          <div className="flex items-center justify-end gap-2">
                            <button onClick={() => handleSaveSetting(s.key)} className="px-2 py-1 bg-green-600 text-white text-xs rounded">Save</button>
                            <button onClick={() => setEditingSetting(null)} className="px-2 py-1 bg-dark-hover text-gray-300 text-xs rounded">Cancel</button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            <button onClick={() => { setEditingSetting(s.key); setSettingValue(s.value || '') }} className="p-1 text-gray-400 hover:text-white transition-colors">
                              <PencilIcon className="w-4 h-4" />
                            </button>
                            <button onClick={() => deleteSetting(s.key)} className="p-1 text-gray-400 hover:text-red-400 transition-colors">
                              <TrashIcon className="w-4 h-4" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {settings.length === 0 && <div className="px-6 py-12 text-center text-gray-500">No settings configured</div>}
            </div>
          </div>
        )}

        {/* Docs Tab */}
        {activeTab === 'docs' && (
          <div className="space-y-6">
            {/* Add New Doc */}
            {showNewDoc ? (
              <div className="bg-dark-surface rounded-xl border border-dark-border p-6">
                <h3 className="text-lg font-semibold text-white mb-4">Create Documentation Page</h3>
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <input
                      type="text"
                      value={newDocSlug}
                      onChange={(e) => setNewDocSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))}
                      placeholder="Slug (e.g., getting-started)"
                      className="px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500"
                    />
                    <input
                      type="text"
                      value={newDocCategory}
                      onChange={(e) => setNewDocCategory(e.target.value)}
                      placeholder="Category (optional)"
                      className="px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500"
                    />
                  </div>
                  <input
                    type="text"
                    value={newDocTitle}
                    onChange={(e) => setNewDocTitle(e.target.value)}
                    placeholder="Title"
                    className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500"
                  />
                  <textarea
                    value={newDocContent}
                    onChange={(e) => setNewDocContent(e.target.value)}
                    placeholder="Content (Markdown)"
                    rows={10}
                    className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-500 font-mono text-sm"
                  />
                  <div className="flex justify-end gap-3">
                    <button onClick={() => setShowNewDoc(false)} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                    <button
                      onClick={handleCreateDoc}
                      disabled={!newDocSlug || !newDocTitle}
                      className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg disabled:opacity-50"
                    >
                      Create Page
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setShowNewDoc(true)}
                className="w-full py-4 border-2 border-dashed border-dark-border rounded-xl text-gray-400 hover:text-white hover:border-gray-500 transition-colors flex items-center justify-center gap-2"
              >
                <PlusIcon className="w-5 h-5" /> Add Documentation Page
              </button>
            )}

            {/* Doc Pages List */}
            <div className="grid gap-4">
              {docs.map((doc) => (
                <div key={doc.id} className="bg-dark-surface rounded-xl border border-dark-border overflow-hidden">
                  {editingDoc === doc.slug ? (
                    <div className="p-6 space-y-4">
                      <div className="grid grid-cols-2 gap-4">
                        <input
                          type="text"
                          value={docTitle}
                          onChange={(e) => setDocTitle(e.target.value)}
                          placeholder="Title"
                          className="px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white"
                        />
                        <input
                          type="text"
                          value={docCategory}
                          onChange={(e) => setDocCategory(e.target.value)}
                          placeholder="Category"
                          className="px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white"
                        />
                      </div>
                      <textarea
                        value={docContent}
                        onChange={(e) => setDocContent(e.target.value)}
                        rows={15}
                        className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white font-mono text-sm"
                      />
                      <div className="flex justify-end gap-3">
                        <button onClick={() => setEditingDoc(null)} className="px-4 py-2 text-gray-400 hover:text-white">Cancel</button>
                        <button onClick={() => handleSaveDoc(doc.slug)} className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg">Save</button>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6">
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="text-lg font-semibold text-white">{doc.title}</h3>
                            {doc.category && (
                              <span className="px-2 py-0.5 bg-primary-500/20 text-primary-400 text-xs rounded">{doc.category}</span>
                            )}
                            {!doc.is_published && (
                              <span className="px-2 py-0.5 bg-yellow-500/20 text-yellow-400 text-xs rounded">Draft</span>
                            )}
                          </div>
                          <p className="text-sm text-gray-500 font-mono">/{doc.slug}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => {
                              setEditingDoc(doc.slug)
                              setDocTitle(doc.title)
                              setDocContent(doc.content || '')
                              setDocCategory(doc.category || '')
                            }}
                            className="p-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
                          >
                            <PencilIcon className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => deleteDoc(doc.slug)}
                            className="p-2 text-gray-400 hover:text-red-400 hover:bg-dark-hover rounded-lg transition-colors"
                          >
                            <TrashIcon className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                      {doc.content && (
                        <p className="mt-3 text-sm text-gray-400 line-clamp-2">{doc.content.substring(0, 200)}...</p>
                      )}
                      <p className="mt-3 text-xs text-gray-500">Updated {formatRelative(doc.updated_at)}</p>
                    </div>
                  )}
                </div>
              ))}
              {docs.length === 0 && !showNewDoc && (
                <div className="bg-dark-surface rounded-xl border border-dark-border p-12 text-center">
                  <DocumentTextIcon className="w-12 h-12 mx-auto text-gray-600 mb-4" />
                  <p className="text-gray-400">No documentation pages yet</p>
                  <p className="text-sm text-gray-500 mt-1">Create your first page to get started</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Clear All Data Confirmation Dialog */}
      {resetDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-dark-surface border border-dark-border rounded-2xl shadow-2xl w-full max-w-md mx-4 p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-xl bg-red-500/20 flex items-center justify-center shrink-0">
                <TrashIcon className="w-5 h-5 text-red-400" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-white">Clear All Data</h2>
                <p className="text-xs text-gray-500">This action cannot be undone</p>
              </div>
            </div>

            <div className="bg-red-950/30 border border-red-900/40 rounded-lg p-4 mb-5 text-sm text-gray-300 space-y-1">
              <p className="font-medium text-red-400 mb-2">The following will be permanently deleted:</p>
              <ul className="space-y-1 text-gray-400 list-disc list-inside text-xs">
                <li>All user accounts</li>
                <li>All authentication tokens</li>
                <li>All registered nodes</li>
                <li>All system settings</li>
                <li>All audit log entries</li>
                <li>All documentation pages</li>
              </ul>
            </div>

            <p className="text-sm text-gray-400 mb-3">
              Type <span className="font-mono font-semibold text-red-400">{RESET_CONFIRM_PHRASE}</span> to confirm:
            </p>
            <input
              type="text"
              value={resetConfirmText}
              onChange={(e) => setResetConfirmText(e.target.value)}
              placeholder={RESET_CONFIRM_PHRASE}
              className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-gray-600 font-mono mb-4 focus:outline-none focus:border-red-500"
              autoFocus
            />

            {resetError && (
              <p className="text-sm text-red-400 mb-3">{resetError}</p>
            )}
            {resetStatus === 'success' && (
              <p className="text-sm text-green-400 mb-3 flex items-center gap-2">
                <CheckCircleIcon className="w-4 h-4" /> Data cleared — reloading…
              </p>
            )}

            <div className="flex gap-3">
              <button
                onClick={() => { setResetDialogOpen(false); setResetConfirmText('') }}
                disabled={resetStatus === 'loading' || resetStatus === 'success'}
                className="flex-1 px-4 py-2 bg-dark-hover hover:bg-dark-border text-gray-300 rounded-lg transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleResetAllData}
                disabled={resetConfirmText !== RESET_CONFIRM_PHRASE || resetStatus !== 'idle'}
                className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed font-medium"
              >
                {resetStatus === 'loading' ? 'Clearing…' : resetStatus === 'success' ? 'Done' : 'Confirm Reset'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
