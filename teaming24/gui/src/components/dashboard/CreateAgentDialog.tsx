import { useState } from 'react'
import { createPortal } from 'react-dom'
import { XMarkIcon, PlusIcon, TrashIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useAgentStore, type AgentCapability, type LocalAgentRole } from '../../store/agentStore'
import { useNetworkStore } from '../../store/networkStore'
import { useConfigStore } from '../../store/configStore'

interface CreateAgentDialogProps {
  onClose: () => void
}

const roleOptions: { value: LocalAgentRole; label: string; description: string }[] = [
  { value: 'organizer', label: 'Organizer', description: 'Plans and distributes tasks' },
  { value: 'coordinator', label: 'Coordinator', description: 'Coordinates between agents' },
  { value: 'worker', label: 'Worker', description: 'Executes assigned tasks' },
]

export default function CreateAgentDialog({ onClose }: CreateAgentDialogProps) {
  const { addAgent, saveAgentToDB } = useAgentStore()
  const { connectToWanNode } = useNetworkStore()
  const serverPort = useConfigStore(s => s.serverPort)
  const defaultPort = String(serverPort || 8000)
  
  const [isAgenticNode, setIsAgenticNode] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState<LocalAgentRole>('worker')
  // Agentic Node connection fields
  const [ip, setIp] = useState('')
  const [port, setPort] = useState(defaultPort)
  const [password, setPassword] = useState('')
  const [alias, setAlias] = useState('')  // Custom display name
  const [capabilities, setCapabilities] = useState<AgentCapability[]>([])
  const [newCapName, setNewCapName] = useState('')
  const [newCapDesc, setNewCapDesc] = useState('')
  const [loading, setLoading] = useState(false)

  const handleAddCapability = () => {
    if (newCapName.trim()) {
      setCapabilities([
        ...capabilities,
        { name: newCapName.trim(), description: newCapDesc.trim() }
      ])
      setNewCapName('')
      setNewCapDesc('')
    }
  }

  const handleRemoveCapability = (index: number) => {
    setCapabilities(capabilities.filter((_, i) => i !== index))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (isAgenticNode) {
      // Connect to remote node via IP/Port
      if (!ip.trim()) return
      setLoading(true)
      try {
        await connectToWanNode(ip, parseInt(port), password, alias.trim() || undefined)
        onClose()
      } catch (error) {
        // Error handled in store with notification
      } finally {
        setLoading(false)
      }
    } else {
      // Add local agent and persist to DB
      if (!name.trim()) return
      const agentData = {
        name: name.trim(),
        type: role,
        status: 'offline' as const,
        capabilities,
      }
      addAgent(agentData)
      // Find the newly added agent to get its ID and persist
      const agents = useAgentStore.getState().agents
      const newAgent = agents.find(a => a.name === name.trim())
      if (newAgent) {
        saveAgentToDB(newAgent)
      }
      onClose()
    }
  }

  return createPortal(
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-[99999]">
      <div className="bg-dark-surface border border-dark-border rounded-xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border sticky top-0 bg-dark-surface">
          <h2 className="text-lg font-semibold text-white">
            {isAgenticNode ? 'Add Agentic Node' : 'Add Local Agent'}
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
          >
            <XMarkIcon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          {/* Type Toggle */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-2">Type</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setIsAgenticNode(false)}
                className={clsx(
                  'flex-1 px-4 py-3 rounded-lg border transition-colors text-sm',
                  !isAgenticNode
                    ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                    : 'border-dark-border text-gray-400 hover:border-dark-hover'
                )}
              >
                <div className="font-medium">Local Agent</div>
                <div className="text-xs opacity-70">Organizer / Coordinator / Worker</div>
              </button>
              <button
                type="button"
                onClick={() => setIsAgenticNode(true)}
                className={clsx(
                  'flex-1 px-4 py-3 rounded-lg border transition-colors text-sm',
                  isAgenticNode
                    ? 'border-orange-500 bg-orange-500/10 text-orange-400'
                    : 'border-dark-border text-gray-400 hover:border-dark-hover'
                )}
              >
                <div className="font-medium">Agentic Node (AN)</div>
                <div className="text-xs opacity-70">Remote node via AgentaNet</div>
              </button>
            </div>
          </div>

          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={isAgenticNode ? 'e.g., ML Processing AN' : 'e.g., Code Review Worker'}
              className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                         text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
            />
          </div>

          {/* Role (for local agents) */}
          {!isAgenticNode && (
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-2">Role</label>
              <div className="space-y-2">
                {roleOptions.map((option) => (
                  <label
                    key={option.value}
                    className={clsx(
                      'flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors',
                      role === option.value
                        ? 'border-primary-500 bg-primary-500/10'
                        : 'border-dark-border hover:border-dark-hover'
                    )}
                  >
                    <input
                      type="radio"
                      value={option.value}
                      checked={role === option.value}
                      onChange={() => setRole(option.value)}
                      className="text-primary-500 focus:ring-primary-500"
                    />
                    <div>
                      <div className="text-sm font-medium text-gray-200">{option.label}</div>
                      <div className="text-xs text-gray-500">{option.description}</div>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Connection Settings (for Agentic Nodes) */}
          {isAgenticNode && (
            <>
              {/* IP Address */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">IP Address / Hostname</label>
                <input
                  type="text"
                  value={ip}
                  onChange={(e) => setIp(e.target.value)}
                  placeholder="192.168.1.100 or node.example.com"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                             text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500"
                />
              </div>
              {/* Port */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Port</label>
                <input
                  type="number"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  placeholder={defaultPort}
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                             text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500"
                />
              </div>
              {/* Password */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Password (Optional)</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                             text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500"
                />
              </div>
              {/* Alias */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Display Name (Optional)</label>
                <input
                  type="text"
                  value={alias}
                  onChange={(e) => setAlias(e.target.value)}
                  placeholder="e.g., My ML Server"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                             text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500"
                />
                <p className="text-xs text-gray-500 mt-1">Custom name to identify this node</p>
              </div>
            </>
          )}

          {/* Capabilities */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-2">Capabilities</label>
            
            {capabilities.length > 0 && (
              <div className="space-y-2 mb-3">
                {capabilities.map((cap, index) => (
                  <div
                    key={index}
                    className="flex items-center justify-between p-2 bg-dark-bg rounded-lg"
                  >
                    <div>
                      <p className="text-sm text-gray-200">{cap.name}</p>
                      {cap.description && (
                        <p className="text-xs text-gray-500">{cap.description}</p>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRemoveCapability(index)}
                      className="p-1 hover:bg-red-500/20 rounded transition-colors"
                    >
                      <TrashIcon className="w-4 h-4 text-red-400" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="flex gap-2">
              <input
                type="text"
                value={newCapName}
                onChange={(e) => setNewCapName(e.target.value)}
                placeholder="Capability name"
                className="flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                           text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-primary-500"
              />
              <button
                type="button"
                onClick={handleAddCapability}
                disabled={!newCapName.trim()}
                className="px-3 py-2 bg-dark-hover hover:bg-dark-border disabled:opacity-50 
                           rounded-lg transition-colors"
              >
                <PlusIcon className="w-4 h-4 text-gray-400" />
              </button>
            </div>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || (!isAgenticNode && !name.trim()) || (isAgenticNode && !ip.trim())}
              className={clsx(
                'px-4 py-2 rounded-lg transition-colors text-white',
                isAgenticNode 
                  ? 'bg-orange-600 hover:bg-orange-700 disabled:bg-gray-700'
                  : 'bg-primary-600 hover:bg-primary-700 disabled:bg-gray-700',
                'disabled:cursor-not-allowed'
              )}
            >
              {loading ? 'Connecting...' : isAgenticNode ? 'Connect' : 'Add Agent'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}
