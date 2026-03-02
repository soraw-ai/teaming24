import { useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  XMarkIcon,
  PlusIcon,
  WrenchScrewdriverIcon,
  CpuChipIcon,
  DocumentTextIcon,
  UserCircleIcon,
  CheckIcon,
  AcademicCapIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useAgentStore, type AgentCapability, type LocalAgentRole, type Agent } from '../../store/agentStore'
import { useNetworkStore } from '../../store/networkStore'
import { useConfigStore } from '../../store/configStore'
import { useSkillStore } from '../../store/skillStore'
import { getApiBase } from '../../utils/api'

interface ToolDef {
  id: string
  label: string
  description: string
}

interface ToolSection {
  id: string
  label: string
  tools: ToolDef[]
}

type ToolProfileId = 'minimal' | 'coding' | 'networking' | 'full'

const PROFILE_OPTIONS: { id: ToolProfileId; label: string }[] = [
  { id: 'minimal', label: 'Minimal' },
  { id: 'coding', label: 'Coding' },
  { id: 'networking', label: 'Networking' },
  { id: 'full', label: 'Full' },
]

interface AgentEditorDialogProps {
  onClose: () => void
  editAgent?: Agent | null
}

const roleOptions: { value: LocalAgentRole; label: string; description: string }[] = [
  { value: 'organizer', label: 'Organizer', description: 'Plans and distributes tasks across agents' },
  { value: 'coordinator', label: 'Coordinator', description: 'Coordinates between local workers and network' },
  { value: 'worker', label: 'Worker', description: 'Executes assigned tasks with specific tools' },
]

const modelOptions = [
  { value: 'flock/gpt-5.2', label: 'FLock GPT-5.2' },
  { value: 'flock/gpt-5.3-codex', label: 'FLock GPT-5.3-Codex' },
  { value: 'flock/gpt-5.2-pro', label: 'FLock GPT-5.2 Pro' },
  { value: 'flock/gpt-5-mini', label: 'FLock GPT-5-mini' },
  { value: 'flock/qwen3-max', label: 'FLock qwen3-max' },
  { value: 'flock/gemini/gemini-2.5-pro', label: 'FLock gemini-2.5-pro' },
  { value: 'openai/gpt-5.2', label: 'OpenAI GPT-5.2' },
  { value: 'openai/gpt-5.3-codex', label: 'OpenAI GPT-5.3-Codex' },
  { value: 'openai/gpt-5.2-pro', label: 'OpenAI GPT-5.2 Pro' },
  { value: 'openai/gpt-5-mini', label: 'OpenAI GPT-5-mini' },
  { value: 'anthropic/claude-opus-4-6', label: 'Anthropic claude-opus-4-6' },
  { value: 'anthropic/claude-sonnet-4-6', label: 'Anthropic claude-sonnet-4-6' },
  { value: 'local/llama3.1', label: 'Local Llama 3.1' },
  { value: 'ollama/llama3.1', label: 'Ollama Llama 3.1' },
]

type EditorTab = 'general' | 'model' | 'tools'

export default function AgentEditorDialog({ onClose, editAgent }: AgentEditorDialogProps) {
  const { addAgent, saveAgentToDB, updateAgent, updateAgentInDB } = useAgentStore()
  const { connectToWanNode } = useNetworkStore()
  const serverPort = useConfigStore(s => s.serverPort)
  const defaultPort = String(serverPort || 8000)

  const isEditing = !!editAgent
  const [activeTab, setActiveTab] = useState<EditorTab>('general')

  // General
  const [isAgenticNode, setIsAgenticNode] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState<LocalAgentRole>('worker')
  const [goal, setGoal] = useState('')
  const [backstory, setBackstory] = useState('')
  const [capabilities, setCapabilities] = useState<AgentCapability[]>([])
  const [newCapName, setNewCapName] = useState('')
  const [newCapDesc, setNewCapDesc] = useState('')

  // Agentic Node fields
  const [ip, setIp] = useState('')
  const [port, setPort] = useState(defaultPort)
  const [password, setPassword] = useState('')
  const [alias, setAlias] = useState('')

  // Model & Prompt
  const [model, setModel] = useState('flock/gpt-5.2')
  const [customModel, setCustomModel] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [allowDelegation, setAllowDelegation] = useState(true)

  // Tools — profile + overrides
  const [toolSections, setToolSections] = useState<ToolSection[]>([])
  const [toolProfiles, setToolProfiles] = useState<Record<string, { allow?: string[] }>>({})
  const [toolGroups, setToolGroups] = useState<Record<string, string[]>>({})
  const [toolProfile, setToolProfile] = useState<ToolProfileId>('full')
  const [toolAlsoAllow, setToolAlsoAllow] = useState<Set<string>>(new Set())
  const [toolDeny, setToolDeny] = useState<Set<string>>(new Set())
  const [toolsLoading, setToolsLoading] = useState(false)

  // Skills
  const { skills: allSkills, loadSkills, getAgentSkillIds, assignSkillsToAgent } = useSkillStore()
  const [selectedSkills, setSelectedSkills] = useState<string[]>([])
  const [skillsLoaded, setSkillsLoaded] = useState(false)

  const [loading, setLoading] = useState(false)

  // Populate fields when editing
  useEffect(() => {
    if (editAgent) {
      setName(editAgent.name)
      setRole(editAgent.type === 'agentic_node' ? 'worker' : editAgent.type as LocalAgentRole)
      setIsAgenticNode(editAgent.type === 'agentic_node')
      setGoal(editAgent.goal || '')
      setBackstory(editAgent.backstory || '')
      setCapabilities(editAgent.capabilities || [])
      setModel(editAgent.model || 'flock/gpt-5.2')
      setSystemPrompt(editAgent.system_prompt || '')
      setAllowDelegation(editAgent.allow_delegation ?? true)
      // Infer tool overrides from stored tools list (best-effort)
      if (editAgent.tools?.length) {
        setToolProfile('full')
        setToolDeny(new Set())
        setToolAlsoAllow(new Set())
      }
    }
  }, [editAgent])

  const fetchTools = useCallback(async () => {
    setToolsLoading(true)
    try {
      const res = await fetch(`${getApiBase()}/api/agent/available-tools`)
      if (res.ok) {
        const data = await res.json()
        setToolSections(data.sections || [])
        setToolProfiles(data.profiles || {})
        setToolGroups(data.groups || {})
      }
    } catch (e) { console.warn('AgentEditorDialog error:', e); }
    setToolsLoading(false)
  }, [])

  useEffect(() => { fetchTools() }, [fetchTools])

  // Load skills
  useEffect(() => {
    if (!skillsLoaded) {
      loadSkills().then(() => setSkillsLoaded(true))
    }
  }, [loadSkills, skillsLoaded])

  // Load agent's assigned skills when editing
  useEffect(() => {
    if (editAgent) {
      getAgentSkillIds(editAgent.id).then(ids => setSelectedSkills(ids))
    }
  }, [editAgent, getAgentSkillIds])

  const handleAddCapability = () => {
    if (newCapName.trim()) {
      setCapabilities(prev => [...prev, { name: newCapName.trim(), description: newCapDesc.trim() }])
      setNewCapName('')
      setNewCapDesc('')
    }
  }

  const allToolIds = toolSections.flatMap(s => s.tools.map(t => t.id))

  const expandGroupsFn = (entries: string[]): Set<string> => {
    const result = new Set<string>()
    for (const entry of entries) {
      if (entry.startsWith('group:') && toolGroups[entry]) {
        toolGroups[entry].forEach(id => result.add(id))
      } else {
        result.add(entry)
      }
    }
    return result
  }

  const isToolEnabled = (toolId: string): boolean => {
    const profileDef = toolProfiles[toolProfile] || {}
    const baseAllowed = profileDef.allow === undefined
      ? true
      : expandGroupsFn(profileDef.allow).has(toolId)
    return ((baseAllowed || toolAlsoAllow.has(toolId)) && !toolDeny.has(toolId))
  }

  const isBaseAllowed = (toolId: string): boolean => {
    const profileDef = toolProfiles[toolProfile] || {}
    return profileDef.allow === undefined
      ? true
      : expandGroupsFn(profileDef.allow).has(toolId)
  }

  const toggleTool = (toolId: string) => {
    if (isToolEnabled(toolId)) {
      setToolAlsoAllow(p => { const n = new Set(p); n.delete(toolId); return n })
      setToolDeny(p => new Set(p).add(toolId))
    } else {
      setToolDeny(p => { const n = new Set(p); n.delete(toolId); return n })
      if (!isBaseAllowed(toolId)) {
        setToolAlsoAllow(p => new Set(p).add(toolId))
      }
    }
  }

  const resolvedTools = allToolIds.filter(isToolEnabled)

  const toggleSkill = (skillId: string) => {
    setSelectedSkills(prev =>
      prev.includes(skillId) ? prev.filter(s => s !== skillId) : [...prev, skillId]
    )
  }

  const resolvedModel = modelOptions.find(m => m.value === model) ? model : customModel || model

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (isAgenticNode && !isEditing) {
      if (!ip.trim()) return
      setLoading(true)
      try {
        await connectToWanNode(ip, parseInt(port), password, alias.trim() || undefined)
        onClose()
      } finally {
        setLoading(false)
      }
      return
    }

    if (!name.trim()) return
    setLoading(true)

    const agentData = {
      name: name.trim(),
      type: role as Agent['type'],
      status: (isEditing && editAgent ? editAgent.status : 'offline'),
      capabilities,
      goal: goal.trim() || undefined,
      backstory: backstory.trim() || undefined,
      model: resolvedModel || undefined,
      tools: resolvedTools.length > 0 ? resolvedTools : undefined,
      system_prompt: systemPrompt.trim() || undefined,
      allow_delegation: allowDelegation,
    }

    try {
      if (isEditing && editAgent) {
        updateAgent(editAgent.id, agentData)
        await updateAgentInDB(editAgent.id, agentData)
        await assignSkillsToAgent(editAgent.id, selectedSkills)
      } else {
        addAgent(agentData)
        const agents = useAgentStore.getState().agents
        const newAgent = agents.find(a => a.name === name.trim())
        if (newAgent) {
          await saveAgentToDB(newAgent)
          if (selectedSkills.length > 0) {
            await assignSkillsToAgent(newAgent.id, selectedSkills)
          }
        }
      }
      onClose()
    } finally {
      setLoading(false)
    }
  }

  const tabs: { id: EditorTab; label: string; icon: typeof UserCircleIcon }[] = [
    { id: 'general', label: 'General', icon: UserCircleIcon },
    { id: 'model', label: 'Model & Prompt', icon: CpuChipIcon },
    { id: 'tools', label: 'Tools & Skills', icon: WrenchScrewdriverIcon },
  ]

  const sectionIcons: Record<string, string> = {
    sandbox: 'text-green-400',
    network: 'text-orange-400',
    memory: 'text-purple-400',
  }

  return createPortal(
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-[99999]">
      <div className="bg-dark-surface border border-dark-border rounded-xl w-full max-w-2xl mx-4 max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border shrink-0">
          <h2 className="text-lg font-semibold text-white">
            {isEditing ? `Edit ${editAgent?.name}` : isAgenticNode ? 'Add Agentic Node' : 'Add Local Agent'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-dark-hover rounded-lg transition-colors">
            <XMarkIcon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Tab nav (only for local agents / editing) */}
        {!isAgenticNode && (
          <div className="flex border-b border-dark-border shrink-0">
            {tabs.map(tab => {
              const Icon = tab.icon
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={clsx(
                    'flex items-center gap-2 px-4 py-2.5 text-sm transition-colors border-b-2 -mb-px',
                    activeTab === tab.id
                      ? 'border-primary-500 text-primary-400'
                      : 'border-transparent text-gray-500 hover:text-gray-300'
                  )}
                >
                  <Icon className="w-4 h-4" />
                  {tab.label}
                </button>
              )
            })}
          </div>
        )}

        {/* Body */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Type toggle — only when creating new */}
          {!isEditing && activeTab === 'general' && (
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
                  <div className="font-medium">Agentic Node</div>
                  <div className="text-xs opacity-70">Remote node via AgentaNet</div>
                </button>
              </div>
            </div>
          )}

          {/* ===== Agentic Node Connection ===== */}
          {isAgenticNode && !isEditing && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">IP Address / Hostname</label>
                <input type="text" value={ip} onChange={e => setIp(e.target.value)}
                  placeholder="192.168.1.100 or node.example.com"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Port</label>
                  <input type="number" value={port} onChange={e => setPort(e.target.value)}
                    className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 focus:outline-none focus:border-orange-500" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Password (Optional)</label>
                  <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500" />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Display Name (Optional)</label>
                <input type="text" value={alias} onChange={e => setAlias(e.target.value)}
                  placeholder="e.g., My ML Server"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-orange-500" />
              </div>
            </>
          )}

          {/* ===== General Tab ===== */}
          {!isAgenticNode && activeTab === 'general' && (
            <>
              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Name</label>
                <input type="text" value={name} onChange={e => setName(e.target.value)}
                  placeholder="e.g., Code Review Worker"
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500" />
              </div>

              {/* Role */}
              {!isEditing && (
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-2">Role</label>
                  <div className="grid grid-cols-3 gap-2">
                    {roleOptions.map(opt => (
                      <button key={opt.value} type="button" onClick={() => setRole(opt.value)}
                        className={clsx(
                          'px-3 py-3 rounded-lg border transition-colors text-sm text-left',
                          role === opt.value
                            ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                            : 'border-dark-border text-gray-400 hover:border-dark-hover'
                        )}>
                        <div className="font-medium">{opt.label}</div>
                        <div className="text-xs opacity-70 mt-0.5">{opt.description}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Goal */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Goal</label>
                <textarea value={goal} onChange={e => setGoal(e.target.value)}
                  placeholder="What is this agent's primary objective?"
                  rows={2}
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500 resize-none" />
              </div>

              {/* Backstory */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Backstory</label>
                <textarea value={backstory} onChange={e => setBackstory(e.target.value)}
                  placeholder="Background context and expertise of this agent..."
                  rows={2}
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500 resize-none" />
              </div>

              {/* Capabilities */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">Capabilities</label>
                {capabilities.length > 0 && (
                  <div className="flex flex-wrap gap-2 mb-3">
                    {capabilities.map((cap, i) => (
                      <span key={i} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-dark-bg text-sm text-gray-300">
                        {cap.name}
                        <button type="button" onClick={() => setCapabilities(prev => prev.filter((_, idx) => idx !== i))}
                          className="hover:text-red-400 transition-colors">
                          <XMarkIcon className="w-3.5 h-3.5" />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <input type="text" value={newCapName} onChange={e => setNewCapName(e.target.value)}
                    placeholder="Capability name"
                    onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), handleAddCapability())}
                    className="flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-primary-500" />
                  <input type="text" value={newCapDesc} onChange={e => setNewCapDesc(e.target.value)}
                    placeholder="Description (optional)"
                    onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), handleAddCapability())}
                    className="flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-primary-500" />
                  <button type="button" onClick={handleAddCapability} disabled={!newCapName.trim()}
                    className="px-3 py-2 bg-dark-hover hover:bg-dark-border disabled:opacity-50 rounded-lg transition-colors">
                    <PlusIcon className="w-4 h-4 text-gray-400" />
                  </button>
                </div>
              </div>
            </>
          )}

          {/* ===== Model & Prompt Tab ===== */}
          {!isAgenticNode && activeTab === 'model' && (
            <>
              {/* Model Selection */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">LLM Model</label>
                <div className="grid grid-cols-2 gap-2 mb-3">
                  {modelOptions.map(opt => (
                    <button key={opt.value} type="button"
                      onClick={() => { setModel(opt.value); setCustomModel('') }}
                      className={clsx(
                        'px-3 py-2 rounded-lg border transition-colors text-sm text-left',
                        model === opt.value
                          ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                          : 'border-dark-border text-gray-400 hover:border-dark-hover'
                      )}>
                      {opt.label}
                    </button>
                  ))}
                  <button type="button"
                    onClick={() => setModel('custom')}
                    className={clsx(
                      'px-3 py-2 rounded-lg border transition-colors text-sm text-left',
                      model === 'custom'
                        ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                        : 'border-dark-border text-gray-400 hover:border-dark-hover'
                    )}>
                    Custom...
                  </button>
                </div>
                {model === 'custom' && (
                  <input type="text" value={customModel} onChange={e => setCustomModel(e.target.value)}
                    placeholder="provider/model-name (e.g., flock/gpt-5.2)"
                    className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-primary-500" />
                )}
              </div>

              {/* System Prompt */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  <DocumentTextIcon className="w-4 h-4 inline mr-1" />
                  System Prompt
                </label>
                <p className="text-xs text-gray-500 mb-2">
                  Custom instructions for this agent. Overrides the default system prompt derived from role/goal/backstory.
                </p>
                <textarea value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)}
                  placeholder="You are a specialized agent that..."
                  rows={6}
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-primary-500 resize-none font-mono" />
              </div>

              {/* Allow Delegation */}
              <div className="flex items-center justify-between p-3 bg-dark-bg rounded-lg">
                <div>
                  <span className="text-sm text-gray-300">Allow Delegation</span>
                  <p className="text-xs text-gray-500">Allow this agent to delegate tasks to other agents</p>
                </div>
                <button type="button" onClick={() => setAllowDelegation(v => !v)}
                  className={clsx(
                    'w-10 h-6 rounded-full transition-colors relative',
                    allowDelegation ? 'bg-primary-600' : 'bg-gray-600'
                  )}>
                  <span className={clsx(
                    'absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform',
                    allowDelegation ? 'left-[18px]' : 'left-0.5'
                  )} />
                </button>
              </div>
            </>
          )}

          {/* ===== Tools & Skills Tab ===== */}
          {!isAgenticNode && activeTab === 'tools' && (
            <>
              <div className="flex items-center justify-between mb-1">
                <label className="text-sm font-medium text-gray-400">Tool Access</label>
                <span className="text-xs text-gray-500">
                  {resolvedTools.length}/{allToolIds.length} enabled
                </span>
              </div>
              <p className="text-xs text-gray-500 mb-3">
                Select a profile preset, then fine-tune individual tool access.
              </p>

              {/* Profile selector */}
              <div className="flex gap-1.5 mb-4">
                {PROFILE_OPTIONS.map(opt => (
                  <button key={opt.id} type="button"
                    onClick={() => { setToolProfile(opt.id); setToolAlsoAllow(new Set()); setToolDeny(new Set()) }}
                    className={clsx(
                      'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                      toolProfile === opt.id
                        ? 'bg-green-500/20 text-green-400'
                        : 'text-gray-500 hover:bg-dark-hover hover:text-gray-300'
                    )}>
                    {opt.label}
                  </button>
                ))}
              </div>

              {toolsLoading ? (
                <div className="text-center py-8 text-gray-500 text-sm">Loading tools...</div>
              ) : (
                <div className="space-y-4">
                  {toolSections.map(section => (
                    <div key={section.id}>
                      <div className="flex items-center gap-2 mb-2">
                        <span className={clsx('text-xs font-semibold uppercase tracking-wider', sectionIcons[section.id] || 'text-gray-400')}>
                          {section.label}
                        </span>
                        <span className="text-[10px] text-gray-600">
                          {section.tools.filter(t => isToolEnabled(t.id)).length}/{section.tools.length}
                        </span>
                      </div>
                      <div className="space-y-1">
                        {section.tools.map(tool => {
                          const enabled = isToolEnabled(tool.id)
                          return (
                            <div key={tool.id}
                              className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-dark-border hover:border-dark-hover transition-colors">
                              <div className="min-w-0 flex-1">
                                <div className="text-sm text-gray-200 font-mono">{tool.label}</div>
                                <div className="text-xs text-gray-500 truncate">{tool.description}</div>
                              </div>
                              <button type="button" onClick={() => toggleTool(tool.id)}
                                className={clsx(
                                  'w-9 h-5 rounded-full transition-colors relative shrink-0',
                                  enabled ? 'bg-green-600' : 'bg-gray-600'
                                )}>
                                <span className={clsx(
                                  'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                                  enabled ? 'left-[18px]' : 'left-0.5'
                                )} />
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                  {toolSections.length === 0 && !toolsLoading && (
                    <p className="text-gray-500 text-sm text-center py-6">No tools available</p>
                  )}
                </div>
              )}

              {/* Skills Section */}
              <div className="mt-6 pt-4 border-t border-dark-border">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <AcademicCapIcon className="w-4 h-4 text-yellow-400" />
                    <label className="text-sm font-medium text-gray-400">Assign Skills</label>
                  </div>
                  <span className="text-xs text-gray-500">
                    {selectedSkills.length} selected
                  </span>
                </div>
                <p className="text-xs text-gray-500 mb-3">
                  Skills provide high-level knowledge and workflows that guide agent behavior.
                </p>
                {allSkills.filter(s => s.enabled).length === 0 ? (
                  <p className="text-gray-500 text-xs text-center py-4">No skills available. Create skills in the Skills panel.</p>
                ) : (
                  <div className="space-y-1">
                    {allSkills.filter(s => s.enabled).map(skill => (
                      <button key={skill.id} type="button"
                        onClick={() => toggleSkill(skill.id)}
                        className={clsx(
                          'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors text-left',
                          selectedSkills.includes(skill.id)
                            ? 'border-yellow-500/50 bg-yellow-500/10'
                            : 'border-dark-border hover:border-dark-hover'
                        )}>
                        <span className={clsx(
                          'w-5 h-5 rounded flex items-center justify-center shrink-0 border',
                          selectedSkills.includes(skill.id)
                            ? 'bg-yellow-600 border-yellow-600'
                            : 'border-gray-600'
                        )}>
                          {selectedSkills.includes(skill.id) && <CheckIcon className="w-3.5 h-3.5 text-white" />}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-gray-200">{skill.name}</span>
                            <span className="text-[10px] text-gray-500 bg-dark-bg px-1.5 py-0.5 rounded">{skill.category}</span>
                          </div>
                          <div className="text-xs text-gray-500 truncate">{skill.description}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4 border-t border-dark-border">
            <button type="button" onClick={onClose} disabled={loading}
              className="px-4 py-2 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50">
              Cancel
            </button>
            <button type="submit"
              disabled={loading || (!isAgenticNode && !name.trim()) || (isAgenticNode && !isEditing && !ip.trim())}
              className={clsx(
                'px-5 py-2 rounded-lg transition-colors text-white font-medium',
                isAgenticNode
                  ? 'bg-orange-600 hover:bg-orange-700 disabled:bg-gray-700'
                  : 'bg-primary-600 hover:bg-primary-700 disabled:bg-gray-700',
                'disabled:cursor-not-allowed'
              )}>
              {loading
                ? 'Saving...'
                : isEditing
                  ? 'Save Changes'
                  : isAgenticNode
                    ? 'Connect'
                    : 'Create Agent'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}
