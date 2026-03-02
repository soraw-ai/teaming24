import { useEffect, useState } from 'react'
import { useAuthStore } from '../store/authStore'
import {
  ArrowRightOnRectangleIcon,
  PlusIcon,
  KeyIcon,
  ArrowPathIcon,
  TrashIcon,
  ClipboardIcon,
  CheckIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'

export default function Dashboard() {
  const { user, tokens, tokenLimit, logout, createToken, refreshToken, revokeToken, fetchTokens, isLoading, error, clearError } = useAuthStore()
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newNodeId, setNewNodeId] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [newToken, setNewToken] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [copyPulseId, setCopyPulseId] = useState<string | null>(null)
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [refreshingId, setRefreshingId] = useState<string | null>(null)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [lastSyncedAt, setLastSyncedAt] = useState<number>(Date.now())

  useEffect(() => {
    setLastSyncedAt(Date.now())
  }, [tokens])

  const handleCreate = async () => {
    if (!newNodeId.trim()) return
    setCreating(true)
    try {
      const token = await createToken(newNodeId.trim(), newDescription.trim() || undefined)
      if (token?.plain_token) {
        setNewToken(token.plain_token)
        setNewNodeId('')
        setNewDescription('')
        setShowCreateModal(false)
      }
    } catch (e) {
      console.error('Create token failed:', e)
    } finally {
      setCreating(false)
    }
  }

  const handleRefresh = async (tokenId: string) => {
    setRefreshingId(tokenId)
    try {
      const token = await refreshToken(tokenId)
      if (token?.plain_token) {
        setNewToken(token.plain_token)
      }
    } catch (e) {
      console.error(`Refresh token failed (${tokenId}):`, e)
    } finally {
      setRefreshingId(null)
    }
  }

  const handleRevoke = async (tokenId: string) => {
    setRevokingId(tokenId)
    try {
      const success = await revokeToken(tokenId)
      if (success) {
        setConfirmRevoke(null)
      }
    } catch (e) {
      console.error(`Revoke token failed (${tokenId}):`, e)
    } finally {
      setRevokingId(null)
    }
  }

  const handleManualRefresh = async () => {
    try {
      await fetchTokens()
      setLastSyncedAt(Date.now())
    } catch (e) {
      console.error('Manual token refresh failed:', e)
    }
  }

  const copyToClipboard = async (text: string, id: string) => {
    let copied = false
    try {
      await navigator.clipboard.writeText(text)
      copied = true
    } catch (err) {
      console.warn('Clipboard API failed, trying fallback copy:', err)
      try {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.setAttribute('readonly', '')
        textarea.style.position = 'absolute'
        textarea.style.left = '-9999px'
        document.body.appendChild(textarea)
        textarea.select()
        copied = document.execCommand('copy')
        document.body.removeChild(textarea)
      } catch (fallbackErr) {
        console.error('Fallback copy failed:', fallbackErr)
      }
    }

    if (!copied) return
    setCopyPulseId(id)
    setCopiedId(id)
    setTimeout(() => setCopyPulseId(null), 260)
    setTimeout(() => setCopiedId(null), 1800)
  }

  const formatDate = (ts: number) => new Date(ts * 1000).toLocaleDateString()

  return (
    <div className="min-h-screen bg-dark-bg">
      {/* Header */}
      <header className="bg-dark-surface border-b border-dark-border">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <span className="text-xl font-bold text-white">A</span>
            </div>
            <div>
              <h1 className="text-lg font-semibold text-white">AgentaNet Central</h1>
              <p className="text-xs text-gray-500">Manage your tokens</p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              {user?.avatar_url && (
                <img src={user.avatar_url} alt="" className="w-8 h-8 rounded-full" />
              )}
              <span className="text-sm text-gray-300">{user?.username}</span>
            </div>
            <button
              onClick={logout}
              className="p-2 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors"
              title="Logout"
            >
              <ArrowRightOnRectangleIcon className="w-5 h-5" />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-8">
        {/* Error Banner */}
        {error && (
          <div className="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg flex items-start gap-3">
            <ExclamationTriangleIcon className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm text-red-400">{error}</p>
            </div>
            <button onClick={clearError} className="text-red-400 hover:text-red-300">
              &times;
            </button>
          </div>
        )}

        {/* New Token Display */}
        {newToken && (
          <div className="mb-6 p-4 bg-green-500/10 border border-green-500/20 rounded-lg">
            <div className="flex items-start gap-3">
              <KeyIcon className="w-5 h-5 text-green-400 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-green-400 mb-1">Token Created</p>
                <p className="text-xs text-green-400/70 mb-2">
                  Copy this token now. It won't be shown again!
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 px-3 py-2 bg-dark-bg rounded text-xs sm:text-sm text-gray-200 font-mono break-all">
                    {newToken}
                  </code>
                  <button
                    onClick={() => copyToClipboard(newToken, 'new-token')}
                    className={`px-3 py-2 bg-green-600 hover:bg-green-700 text-white rounded transition-all duration-200 flex items-center gap-1 ${
                      copyPulseId === 'new-token' ? 'scale-95 animate-pulse' : 'scale-100'
                    }`}
                  >
                    {copiedId === 'new-token' ? (
                      <>
                        <CheckIcon className="w-4 h-4" />
                        Copied
                      </>
                    ) : (
                      <>
                        <ClipboardIcon className="w-4 h-4" />
                        Copy
                      </>
                    )}
                  </button>
                </div>
              </div>
              <button onClick={() => setNewToken(null)} className="text-green-400 hover:text-green-300">
                &times;
              </button>
            </div>
          </div>
        )}

        {/* Token Management */}
        <div className="bg-dark-surface rounded-xl border border-dark-border">
          <div className="px-6 py-4 border-b border-dark-border flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">API Tokens</h2>
              <p className="text-sm text-gray-500">
                Manage your node authentication tokens
                <span className="ml-2 text-gray-600 text-xs">
                  Synced {new Date(lastSyncedAt).toLocaleTimeString()}
                </span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => void handleManualRefresh()}
                disabled={isLoading}
                className="p-2 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors disabled:opacity-50"
                title="Refresh token list"
              >
                <ArrowPathIcon className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
              </button>
              <button
                onClick={() => setShowCreateModal(true)}
                disabled={tokens.length >= tokenLimit}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg
                         transition-colors disabled:opacity-50 disabled:cursor-not-allowed
                         flex items-center gap-2"
              >
                <PlusIcon className="w-4 h-4" />
                New Token
              </button>
            </div>
          </div>

          {/* Token List */}
          <div className="divide-y divide-dark-border">
            {tokens.length === 0 ? (
              <div className="px-6 py-12 text-center">
                <KeyIcon className="w-12 h-12 mx-auto text-gray-600 mb-4" />
                <p className="text-gray-400">No tokens yet</p>
                <p className="text-sm text-gray-500 mt-1">
                  Create a token to register your agentic node
                </p>
              </div>
            ) : (
              tokens.map((token) => (
                <div key={token.id} className="px-6 py-4">
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm text-primary-400 truncate" title={token.node_id}>
                          {token.node_id}
                        </span>
                        {token.is_active ? (
                          <span className="px-2 py-0.5 bg-green-500/20 text-green-400 text-xs rounded">
                            Active
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 bg-gray-500/20 text-gray-400 text-xs rounded">
                            Inactive
                          </span>
                        )}
                      </div>
                      {token.description && (
                        <p className="text-sm text-gray-400 mt-1 break-words line-clamp-2" title={token.description}>
                          {token.description}
                        </p>
                      )}
                      <p className="text-xs text-gray-500 mt-2 break-words">
                        Created: {formatDate(token.created_at)}
                        {token.last_used_at && ` • Last used: ${formatDate(token.last_used_at)}`}
                        {token.expires_at && ` • Expires: ${formatDate(token.expires_at)}`}
                      </p>
                    </div>
                    
                    <div className="flex items-center gap-2 ml-4">
                      <button
                        onClick={() => handleRefresh(token.id)}
                        disabled={isLoading || creating || revokingId === token.id}
                        className="p-2 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors"
                        title="Refresh token"
                      >
                        <ArrowPathIcon className={`w-4 h-4 ${refreshingId === token.id ? 'animate-spin text-primary-400' : ''}`} />
                      </button>
                      
                      {confirmRevoke === token.id ? (
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleRevoke(token.id)}
                            disabled={revokingId === token.id}
                            className="px-2 py-1 bg-red-600 text-white text-xs rounded disabled:opacity-50"
                          >
                            {revokingId === token.id ? 'Revoking...' : 'Confirm'}
                          </button>
                          <button
                            onClick={() => setConfirmRevoke(null)}
                            disabled={revokingId === token.id}
                            className="px-2 py-1 bg-dark-hover text-gray-300 text-xs rounded"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setConfirmRevoke(token.id)}
                          className="p-2 text-gray-400 hover:text-red-400 hover:bg-dark-hover rounded-lg transition-colors"
                          title="Revoke token"
                        >
                          <TrashIcon className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Token Limit Notice */}
          <div className="px-6 py-3 bg-dark-bg border-t border-dark-border">
            <p className="text-xs text-gray-500">
              {tokens.length}/{tokenLimit} tokens used • Node IDs are globally unique and permanent
            </p>
          </div>
        </div>
      </main>

      {/* Create Token Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
          <div className="w-full max-w-md bg-dark-surface rounded-xl border border-dark-border">
            <div className="px-6 py-4 border-b border-dark-border">
              <h3 className="text-lg font-semibold text-white">Create New Token</h3>
            </div>
            
            <div className="px-6 py-4 space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">
                  Node ID <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={newNodeId}
                  onChange={(e) => setNewNodeId(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))}
                  placeholder="my-unique-node-id"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg
                           text-gray-200 focus:outline-none focus:border-primary-500"
                  maxLength={64}
                />
                <p className="text-xs text-gray-500 mt-1">
                  Only letters, numbers, hyphens, underscores. Cannot be changed later.
                </p>
              </div>
              
              <div>
                <label className="block text-sm text-gray-400 mb-1">Description (optional)</label>
                <input
                  type="text"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="My production agent"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg
                           text-gray-200 focus:outline-none focus:border-primary-500"
                  maxLength={256}
                />
              </div>
            </div>
            
            <div className="px-6 py-4 border-t border-dark-border flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowCreateModal(false)
                  setNewNodeId('')
                  setNewDescription('')
                }}
                className="px-4 py-2 text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!newNodeId.trim() || creating || isLoading}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg
                         transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {creating ? 'Creating...' : 'Create Token'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
