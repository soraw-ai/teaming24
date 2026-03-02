import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { 
  XMarkIcon, 
  UserGroupIcon,
  ArrowsPointingOutIcon,
  WrenchScrewdriverIcon,
  GlobeAltIcon,
  PlayIcon,
  StopIcon,
  PencilIcon,
  CpuChipIcon,
  DocumentTextIcon,
  AcademicCapIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import type { Agent, AgentType } from '../../store/agentStore'
import { useAgentStore } from '../../store/agentStore'
import { useSkillStore } from '../../store/skillStore'
import { formatDateTime } from '../../utils/date'
import { LOCAL_COORDINATOR_NAME } from '../../utils/ids'
import AgentEditorDialog from './AgentEditorDialog'

interface AgentDetailProps {
  agent: Agent
  onClose: () => void
}

const typeConfig: Record<AgentType, { 
  icon: typeof UserGroupIcon
  label: string
  color: string
  bgColor: string
}> = {
  organizer: { icon: UserGroupIcon, label: 'Organizer', color: 'text-purple-400', bgColor: 'bg-purple-500/20' },
  coordinator: { icon: ArrowsPointingOutIcon, label: LOCAL_COORDINATOR_NAME, color: 'text-blue-400', bgColor: 'bg-blue-500/20' },
  worker: { icon: WrenchScrewdriverIcon, label: 'Worker', color: 'text-green-400', bgColor: 'bg-green-500/20' },
  agentic_node: { icon: GlobeAltIcon, label: 'Remote Agentic Node', color: 'text-orange-400', bgColor: 'bg-orange-500/20' },
}

function getInitials(name: string): string {
  return name
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() || '')
    .join('') || name.substring(0, 2).toUpperCase()
}

export default function AgentDetail({ agent: agentProp, onClose }: AgentDetailProps) {
  const { agents: allAgents, logs, tasks, updateAgent, addLog } = useAgentStore()
  const { skills: allSkills, getAgentSkillIds, loadSkills } = useSkillStore()
  const agent = allAgents.find(a => a.id === agentProp.id) || agentProp
  const [showEditor, setShowEditor] = useState(false)
  const [activeSection, setActiveSection] = useState<'info' | 'prompt' | 'tools' | 'activity'>('info')
  const [agentSkillIds, setAgentSkillIds] = useState<string[]>([])
  
  const typeInfo = typeConfig[agent.type as keyof typeof typeConfig] || typeConfig.worker
  const safeTypeInfo = typeInfo || typeConfig.worker
  const isAgenticNode = agent.type === 'agentic_node'

  useEffect(() => {
    loadSkills()
    getAgentSkillIds(agent.id).then(ids => setAgentSkillIds(ids))
  }, [agent.id, getAgentSkillIds, loadSkills])

  const assignedSkills = allSkills.filter(s => agentSkillIds.includes(s.id))
  
  const agentLogs = logs.filter(l => l.agentId === agent.id).slice(-30)

  const agentTasks = useMemo(() => {
    return tasks
      .filter(t => {
        if (t.assignedAgents?.includes(agent.id)) return true
        if (t.executingAgents?.includes(agent.id)) return true
        if (t.delegatedAgents?.includes(agent.id)) return true
        if (agent.currentTask && t.id === agent.currentTask) return true
        return false
      })
      .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
      .slice(0, 10)
  }, [tasks, agent.id, agent.currentTask])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !showEditor) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, showEditor])

  const sections: { id: typeof activeSection; label: string }[] = [
    { id: 'info', label: 'Info' },
    { id: 'prompt', label: 'Prompt' },
    { id: 'tools', label: 'Tools' },
    { id: 'activity', label: 'Activity' },
  ]

  return createPortal(
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-[480px] max-h-[85vh] flex flex-col bg-dark-surface border border-dark-border rounded-xl shadow-2xl overflow-hidden animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-dark-border shrink-0">
        <div className="flex items-center gap-3 min-w-0 flex-1 overflow-hidden mr-2">
          <div className={clsx(
              'w-11 h-11 rounded-lg flex items-center justify-center shrink-0 relative',
              safeTypeInfo.bgColor,
              agent.status === 'busy' && 'ring-2 ring-blue-400/50'
            )}>
              <span className={clsx('text-base font-bold', safeTypeInfo.color)}>
                {getInitials(agent.name)}
              </span>
              <span className={clsx(
                'absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-dark-surface',
                agent.status === 'online' && 'bg-green-400',
                agent.status === 'idle' && 'bg-gray-400',
                agent.status === 'busy' && 'bg-blue-400',
                agent.status === 'error' && 'bg-red-400',
                agent.status === 'offline' && 'bg-gray-600',
              )} />
          </div>
          <div className="min-w-0 flex-1 overflow-hidden">
            <h2 className="font-semibold text-white truncate">{agent.name}</h2>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500">{safeTypeInfo.label}</span>
                {agent.model && (
                  <span className="text-xs text-gray-600 flex items-center gap-1">
                    <CpuChipIcon className="w-3 h-3" />
                    {agent.model.split('/').pop()}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {!isAgenticNode && (
              <button onClick={() => setShowEditor(true)}
                className="p-2 hover:bg-dark-hover rounded-lg transition-colors" title="Edit agent">
                <PencilIcon className="w-4 h-4 text-gray-400" />
              </button>
            )}
            <button onClick={onClose} className="p-2 hover:bg-dark-hover rounded-lg transition-colors">
          <XMarkIcon className="w-5 h-5 text-gray-400" />
        </button>
          </div>
        </div>

        {/* Controls */}
        {!isAgenticNode ? (
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-dark-border">
            {agent.status === 'offline' ? (
              <button
                onClick={() => {
                  updateAgent(agent.id, { status: 'idle' })
                  addLog({ level: 'info', agentId: agent.id, message: `${agent.name} brought online` })
                }}
                className="flex items-center gap-2 px-3 py-1.5 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors text-sm">
                <PlayIcon className="w-4 h-4" />
                <span>Online</span>
              </button>
            ) : (
              <button
                onClick={() => {
                  updateAgent(agent.id, { status: 'offline', currentTask: undefined })
                  addLog({ level: 'warn', agentId: agent.id, message: `${agent.name} taken offline` })
                }}
                className="flex items-center gap-2 px-3 py-1.5 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors text-sm">
                <StopIcon className="w-4 h-4" />
                <span>Offline</span>
              </button>
            )}
            <div className="flex-1" />
            <span className={clsx(
              'text-xs px-2 py-1 rounded-full',
              agent.status === 'online' && 'bg-green-500/20 text-green-400',
              agent.status === 'busy' && 'bg-yellow-500/20 text-yellow-400',
              agent.status === 'offline' && 'bg-gray-500/20 text-gray-500',
              agent.status === 'error' && 'bg-red-500/20 text-red-400',
              agent.status === 'idle' && 'bg-blue-500/20 text-blue-400',
            )}>
              {agent.status}
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-dark-border">
            <div className="flex items-center gap-2 px-3 py-1.5 bg-orange-500/10 text-orange-400 rounded-lg text-sm">
              <GlobeAltIcon className="w-4 h-4" />
              <span>Remote AN via AgentaNet (View Only)</span>
            </div>
          </div>
        )}

        {/* Section tabs */}
        <div className="flex border-b border-dark-border shrink-0 px-2">
          {sections.map(sec => (
            <button key={sec.id}
              onClick={() => setActiveSection(sec.id)}
              className={clsx(
                'px-3 py-2 text-xs transition-colors border-b-2 -mb-px',
                activeSection === sec.id
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              )}>
              {sec.label}
            </button>
          ))}
        </div>

        {/* Section content */}
        <div className="flex-1 overflow-y-auto thin-scrollbar">
          {/* Info */}
          {activeSection === 'info' && (
            <div className="p-4 space-y-4">
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-gray-500">ID</span>
                  <span className="text-gray-200 font-mono text-xs truncate" title={agent.id}>{agent.id}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-gray-500">Type</span>
                  <span className={safeTypeInfo.color}>{safeTypeInfo.label}</span>
                </div>
                {agent.model && (
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-gray-500">Model</span>
                    <span className="text-gray-200 text-xs">{agent.model}</span>
                  </div>
                )}
          {isAgenticNode && agent.endpoint && (
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-gray-500">Endpoint</span>
                    <span className="text-gray-200 text-xs truncate" title={agent.endpoint}>{agent.endpoint}</span>
                  </div>
                )}
              </div>

              {/* Goal */}
              {agent.goal && (
                <div>
                  <h4 className="text-xs font-medium text-gray-500 mb-1.5">Goal</h4>
                  <p className="text-sm text-gray-300 bg-dark-bg rounded-lg px-3 py-2 whitespace-pre-wrap">{agent.goal}</p>
                </div>
              )}

              {/* Backstory */}
              {agent.backstory && (
                <div>
                  <h4 className="text-xs font-medium text-gray-500 mb-1.5">Backstory</h4>
                  <p className="text-sm text-gray-300 bg-dark-bg rounded-lg px-3 py-2 whitespace-pre-wrap">{agent.backstory}</p>
                </div>
              )}

              {/* Capabilities */}
              <div>
                <h4 className="text-xs font-medium text-gray-500 mb-1.5">Capabilities</h4>
                <div className="flex flex-wrap gap-2">
                  {agent.capabilities.map(cap => (
                    <span key={cap.name} className="px-2 py-1 rounded-lg bg-dark-bg text-xs text-gray-300" title={cap.description}>
                      {cap.name}
                    </span>
                  ))}
                  {agent.capabilities.length === 0 && (
                    <span className="text-gray-500 text-xs">No capabilities defined</span>
                  )}
                </div>
              </div>

              {/* Tasks */}
              <div>
                <h4 className="text-xs font-medium text-gray-500 mb-1.5">Tasks ({agentTasks.length})</h4>
                {agentTasks.length === 0 ? (
                  <p className="text-gray-500 text-xs">No tasks assigned</p>
                ) : (
                  <div className="space-y-1 max-h-[140px] overflow-y-auto thin-scrollbar">
                    {agentTasks.map(task => (
                      <div key={task.id} className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-dark-bg">
                        <span className={clsx(
                          'w-2 h-2 rounded-full shrink-0',
                          task.status === 'running' && 'bg-blue-400 animate-pulse',
                          task.status === 'completed' && 'bg-green-400',
                          task.status === 'failed' && 'bg-red-400',
                          task.status === 'pending' && 'bg-yellow-400',
                        )} />
                        <span className="text-xs text-gray-300 truncate flex-1">{task.name}</span>
                        <span className={clsx(
                          'text-[10px] px-1.5 py-0.5 rounded-full shrink-0',
                          task.status === 'running' && 'bg-blue-500/20 text-blue-400',
                          task.status === 'completed' && 'bg-green-500/20 text-green-400',
                          task.status === 'failed' && 'bg-red-500/20 text-red-400',
                          task.status === 'pending' && 'bg-yellow-500/20 text-yellow-400',
                        )}>
                          {task.status || 'unknown'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Prompt */}
          {activeSection === 'prompt' && (
            <div className="p-4 space-y-4">
              {agent.system_prompt ? (
                <div>
                  <h4 className="text-xs font-medium text-gray-500 mb-1.5 flex items-center gap-1">
                    <DocumentTextIcon className="w-3.5 h-3.5" />
                    System Prompt
                  </h4>
                  <pre className="text-sm text-gray-300 bg-dark-bg rounded-lg px-3 py-2 whitespace-pre-wrap font-mono text-xs max-h-[400px] overflow-y-auto thin-scrollbar">
                    {agent.system_prompt}
                  </pre>
                </div>
              ) : (
                <div className="text-center py-8">
                  <DocumentTextIcon className="w-8 h-8 mx-auto text-gray-600 mb-2" />
                  <p className="text-sm text-gray-500">No custom system prompt defined</p>
                  <p className="text-xs text-gray-600 mt-1">The agent uses its goal and backstory as prompt context</p>
                </div>
              )}
              {agent.allow_delegation !== undefined && (
                <div className="flex items-center justify-between p-3 bg-dark-bg rounded-lg">
                  <span className="text-sm text-gray-400">Allow Delegation</span>
                  <span className={clsx('text-xs px-2 py-0.5 rounded-full', agent.allow_delegation ? 'bg-green-500/20 text-green-400' : 'bg-gray-500/20 text-gray-500')}>
                    {agent.allow_delegation ? 'Enabled' : 'Disabled'}
                  </span>
            </div>
              )}
            </div>
          )}

          {/* Tools & Skills */}
          {activeSection === 'tools' && (
            <div className="p-4 space-y-5">
              {/* Tools */}
              <div>
                <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Tools</h4>
                {agent.tools && agent.tools.length > 0 ? (
                  <div className="space-y-1.5">
                    {agent.tools.map(tool => (
                      <div key={tool} className="flex items-center gap-2 px-3 py-2 bg-dark-bg rounded-lg">
                        <WrenchScrewdriverIcon className="w-4 h-4 text-primary-400 shrink-0" />
                        <span className="text-sm text-gray-200 font-mono">{tool}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 text-center py-4">No tools assigned</p>
                )}
              </div>

              {/* Skills */}
              <div>
                <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Skills</h4>
                {assignedSkills.length > 0 ? (
                  <div className="space-y-1.5">
                    {assignedSkills.map(skill => (
                      <div key={skill.id} className="flex items-start gap-2 px-3 py-2 bg-dark-bg rounded-lg">
                        <AcademicCapIcon className="w-4 h-4 text-yellow-400 shrink-0 mt-0.5" />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-gray-200">{skill.name}</span>
                            <span className="text-[10px] text-gray-500 bg-dark-surface px-1.5 py-0.5 rounded">{skill.category}</span>
                          </div>
                          <p className="text-xs text-gray-500 truncate">{skill.description}</p>
        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 text-center py-4">No skills assigned</p>
                )}
      </div>

              {(!agent.tools || agent.tools.length === 0) && assignedSkills.length === 0 && (
                <div className="text-center py-4">
                  <p className="text-xs text-gray-600">Edit this agent to assign tools and skills</p>
                </div>
              )}
            </div>
          )}

          {/* Activity */}
          {activeSection === 'activity' && (
            <div className="p-4 space-y-2">
          {agentLogs.length === 0 ? (
                <p className="text-gray-500 text-sm text-center py-8">No recent activity</p>
          ) : (
                agentLogs.map(log => (
                  <div key={log.id}
                className={clsx(
                  'px-3 py-2 rounded-lg text-xs',
                  log.level === 'error' && 'bg-red-500/10 text-red-400',
                  log.level === 'warn' && 'bg-yellow-500/10 text-yellow-400',
                  log.level === 'info' && 'bg-dark-bg text-gray-400',
                  log.level === 'debug' && 'bg-dark-bg text-gray-500',
                    )}>
                <div className="flex items-center justify-between mb-1 gap-2">
                  <span className="font-medium uppercase shrink-0">{log.level}</span>
                  <span className="text-gray-500 shrink-0">{formatDateTime(log.timestamp)}</span>
                </div>
                <p className="break-words">{log.message}</p>
              </div>
            ))
          )}
        </div>
          )}
        </div>
    </div>

      {/* Editor dialog */}
      {showEditor && (
        <AgentEditorDialog editAgent={agent} onClose={() => setShowEditor(false)} />
      )}
    </div>,
    document.body
  )
}
