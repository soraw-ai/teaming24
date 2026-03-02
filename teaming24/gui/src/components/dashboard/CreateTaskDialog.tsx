import { useState } from 'react'
import { createPortal } from 'react-dom'
import { XMarkIcon, CheckIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useAgentStore } from '../../store/agentStore'

interface CreateTaskDialogProps {
  onClose: () => void
}

export default function CreateTaskDialog({ onClose }: CreateTaskDialogProps) {
  const { agents, createTask } = useAgentStore()
  
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selectedAgents, setSelectedAgents] = useState<string[]>([])

  const toggleAgent = (agentId: string) => {
    setSelectedAgents(prev =>
      prev.includes(agentId)
        ? prev.filter(id => id !== agentId)
        : [...prev, agentId]
    )
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    
    createTask({
      name: name.trim(),
      description: description.trim(),
      assignedAgents: selectedAgents,
    })
    
    onClose()
  }

  return createPortal(
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-[99999]">
      <div className="bg-dark-surface border border-dark-border rounded-xl w-full max-w-lg mx-4">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border">
          <h2 className="text-lg font-semibold text-white">Create New Task</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
          >
            <XMarkIcon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Task Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Analyze codebase for security issues"
              className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                         text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe what this task should accomplish..."
              rows={3}
              className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg 
                         text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500 resize-none"
            />
          </div>

          {/* Assign Agents */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-2">
              Assign Agents
            </label>
            {agents.length > 0 ? (
              <div className="grid grid-cols-2 gap-2">
                {agents.map((agent) => {
                  const isSelected = selectedAgents.includes(agent.id)
                  const isAvailable = agent.status === 'online' || agent.status === 'idle'
                  
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => toggleAgent(agent.id)}
                      disabled={!isAvailable}
                      className={clsx(
                        'flex items-center gap-2 p-3 rounded-lg border transition-all text-left',
                        isSelected
                          ? 'border-primary-500 bg-primary-500/10'
                          : 'border-dark-border bg-dark-bg hover:border-dark-hover',
                        !isAvailable && 'opacity-50 cursor-not-allowed'
                      )}
                    >
                      <div className={clsx(
                        'w-5 h-5 rounded border flex items-center justify-center shrink-0',
                        isSelected
                          ? 'bg-primary-500 border-primary-500'
                          : 'border-gray-600'
                      )}>
                        {isSelected && <CheckIcon className="w-3 h-3 text-white" />}
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm text-gray-200 truncate">{agent.name}</p>
                        <p className={clsx(
                          'text-xs capitalize',
                          agent.status === 'online' && 'text-green-400',
                          agent.status === 'idle' && 'text-blue-400',
                          agent.status === 'busy' && 'text-yellow-400',
                          agent.status === 'offline' && 'text-gray-500',
                          agent.status === 'error' && 'text-red-400',
                        )}>
                          {agent.status}
                        </p>
                      </div>
                    </button>
                  )
                })}
              </div>
            ) : (
              <p className="text-gray-500 text-sm p-3 bg-dark-bg rounded-lg">
                No agents available. Add an agent first.
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-400 hover:text-gray-200 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!name.trim()}
              className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:bg-gray-700 
                         disabled:cursor-not-allowed text-white rounded-lg transition-colors"
            >
              Create Task
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}
