/**
 * AddNodeDialog - Dialog for connecting to a remote Agentic Node.
 * 
 * Features:
 * - Enter IP:port to connect
 * - Auto-fetch remote node info (name, capabilities)
 * - Requires local node to be online
 */

import { useState, useEffect } from 'react'
import { Dialog } from '@headlessui/react'
import { getApiBase } from '../../utils/api'
import { 
  XMarkIcon, 
  GlobeAltIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  ExclamationCircleIcon,
  SignalSlashIcon
} from '@heroicons/react/24/outline'
import { useNetworkStore } from '../../store/networkStore'
import { useConfigStore } from '../../store/configStore'

interface Props {
  isOpen: boolean
  onClose: () => void
}

interface RemoteNodeInfo {
  name: string
  capability: string
  capabilities?: { name: string; description: string }[]
  status: string
  version?: string
}

export default function AddNodeDialog({ isOpen, onClose }: Props) {
  const { status, connectToWanNode } = useNetworkStore()
  const serverPort = useConfigStore(s => s.serverPort)
  const defaultPort = String(serverPort || 8000)
  
  const [ip, setIp] = useState('')
  const [port, setPort] = useState(defaultPort)
  const [alias, setAlias] = useState('')
  const [password, setPassword] = useState('')
  
  // Remote node info (auto-fetched)
  const [nodeInfo, setNodeInfo] = useState<RemoteNodeInfo | null>(null)
  const [fetchStatus, setFetchStatus] = useState<'idle' | 'fetching' | 'success' | 'error'>('idle')
  const [fetchError, setFetchError] = useState('')
  
  const [connecting, setConnecting] = useState(false)
  
  const isOnline = status === 'online'
  
  // Reset state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setIp('')
      setPort(defaultPort)
      setAlias('')
      setPassword('')
      setNodeInfo(null)
      setFetchStatus('idle')
      setFetchError('')
    }
  }, [isOpen, defaultPort])
  
  // Auto-fetch node info when IP and port are entered
  useEffect(() => {
    const fetchNodeInfo = async () => {
      if (!ip || !port || !isOnline) {
        setNodeInfo(null)
        setFetchStatus('idle')
        return
      }
      
      // Validate IP format
      const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/
      if (!ipRegex.test(ip)) {
        setFetchStatus('idle')
        return
      }
      
      setFetchStatus('fetching')
      setFetchError('')
      
      try {
        const apiBase = getApiBase()
        const response = await fetch(`${apiBase}/api/network/probe`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ip, port: parseInt(port) })
        })
        
        if (!response.ok) {
          throw new Error('Node not reachable')
        }
        
        const data = await response.json()
        setNodeInfo(data)
        setFetchStatus('success')
      } catch (error) {
        setFetchStatus('error')
        setFetchError(error instanceof Error ? error.message : 'Failed to fetch node info')
        setNodeInfo(null)
      }
    }
    
    // Debounce the fetch
    const timer = setTimeout(fetchNodeInfo, 500)
    return () => clearTimeout(timer)
  }, [ip, port, isOnline])
  
  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ip || !port || !isOnline) return
    
    setConnecting(true)
    try {
      await connectToWanNode(ip, parseInt(port), password, alias || nodeInfo?.name)
      onClose()
    } finally {
      setConnecting(false)
    }
  }
  
  return (
    <Dialog open={isOpen} onClose={onClose} className="relative z-50">
      <div className="fixed inset-0 bg-black/60" aria-hidden="true" />
      
      <div className="fixed inset-0 flex items-center justify-center p-4">
        <Dialog.Panel className="w-full max-w-md rounded-2xl bg-dark-surface border border-dark-border shadow-xl">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-dark-border">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-orange-600/20 flex items-center justify-center">
                <GlobeAltIcon className="w-5 h-5 text-orange-400" />
              </div>
              <div>
                <Dialog.Title className="text-lg font-semibold text-white">
                  Add Agentic Node
                </Dialog.Title>
                <p className="text-xs text-gray-500">
                  Connect to a remote node by IP address
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors"
            >
              <XMarkIcon className="w-5 h-5" />
            </button>
          </div>
          
          {/* Offline Warning */}
          {!isOnline && (
            <div className="mx-6 mt-4 flex items-center gap-3 p-3 bg-orange-500/10 border border-orange-500/20 rounded-xl">
              <SignalSlashIcon className="w-5 h-5 text-orange-400 shrink-0" />
              <div>
                <p className="text-sm text-orange-300 font-medium">You are offline</p>
                <p className="text-xs text-orange-400/70">
                  Go online to connect to remote nodes
                </p>
              </div>
            </div>
          )}
          
          {/* Form */}
          <form onSubmit={handleConnect} className="p-6 space-y-4">
            {/* IP and Port */}
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-sm font-medium text-gray-300 mb-1.5">
                  IP Address *
                </label>
                <input
                  type="text"
                  value={ip}
                  onChange={e => setIp(e.target.value)}
                  placeholder="192.168.1.100"
                  disabled={!isOnline}
                  className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  required
                />
              </div>
              <div className="w-24">
                <label className="block text-sm font-medium text-gray-300 mb-1.5">
                  Port
                </label>
                <input
                  type="number"
                  value={port}
                  onChange={e => setPort(e.target.value)}
                  placeholder={defaultPort}
                  disabled={!isOnline}
                  className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                />
              </div>
            </div>
            
            {/* Node Info (Auto-fetched) */}
            {isOnline && ip && (
              <div className="p-4 bg-dark-bg rounded-xl border border-dark-border">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-gray-500 uppercase tracking-wider">Remote Node Info</span>
                  {fetchStatus === 'fetching' && (
                    <ArrowPathIcon className="w-4 h-4 text-gray-400 animate-spin" />
                  )}
                  {fetchStatus === 'success' && (
                    <CheckCircleIcon className="w-4 h-4 text-green-400" />
                  )}
                  {fetchStatus === 'error' && (
                    <ExclamationCircleIcon className="w-4 h-4 text-red-400" />
                  )}
                </div>
                
                {fetchStatus === 'fetching' && (
                  <p className="text-sm text-gray-400">Connecting to node...</p>
                )}
                
                {fetchStatus === 'error' && (
                  <p className="text-sm text-red-400">{fetchError}</p>
                )}
                
                {fetchStatus === 'success' && nodeInfo && (
                  <div className="space-y-2">
                    <div className="flex justify-between">
                      <span className="text-sm text-gray-400">Name:</span>
                      <span className="text-sm text-white font-medium">{nodeInfo.name}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-gray-400">Capability:</span>
                      <span className="text-sm text-primary-400">{nodeInfo.capability}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-gray-400">Status:</span>
                      <span className={`text-sm ${nodeInfo.status === 'online' ? 'text-green-400' : 'text-gray-400'}`}>
                        {nodeInfo.status}
                      </span>
                    </div>
                    {nodeInfo.capabilities && nodeInfo.capabilities.length > 0 && (
                      <div className="pt-2 border-t border-dark-border">
                        <span className="text-xs text-gray-500">Capabilities:</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {nodeInfo.capabilities.slice(0, 4).map((cap, i) => (
                            <span key={i} className="px-2 py-0.5 bg-dark-hover rounded text-xs text-gray-300">
                              {cap.name}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                
                {fetchStatus === 'idle' && (
                  <p className="text-sm text-gray-500">Enter IP address to probe node</p>
                )}
              </div>
            )}
            
            {/* Alias (Optional) */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1.5">
                Display Name (Optional)
              </label>
              <input
                type="text"
                value={alias}
                onChange={e => setAlias(e.target.value)}
                placeholder={nodeInfo?.name || "Custom name for this node"}
                disabled={!isOnline}
                className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>
            
            {/* Password (Optional) */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1.5">
                Password (Optional)
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="If node requires authentication"
                disabled={!isOnline}
                className="w-full px-4 py-2.5 bg-dark-bg border border-dark-border rounded-xl text-white placeholder-gray-500 focus:border-primary-500 focus:ring-1 focus:ring-primary-500 outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>
            
            {/* Actions */}
            <div className="flex gap-3 pt-4 border-t border-dark-border">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 px-4 py-2.5 bg-dark-hover hover:bg-dark-border text-gray-300 rounded-xl text-sm font-medium transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!isOnline || !ip || !port || connecting}
                className="flex-1 px-4 py-2.5 bg-primary-600 hover:bg-primary-500 text-white rounded-xl text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {connecting ? (
                  <>
                    <ArrowPathIcon className="w-4 h-4 animate-spin" />
                    Connecting...
                  </>
                ) : (
                  'Connect'
                )}
              </button>
            </div>
          </form>
        </Dialog.Panel>
      </div>
    </Dialog>
  )
}
