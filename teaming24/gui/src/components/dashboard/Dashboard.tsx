import React, { useState, useEffect, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { getApiBase } from '../../utils/api'
import { 
  PlusIcon, 
  UserGroupIcon,
  ArrowsPointingInIcon,
  WrenchScrewdriverIcon,
  GlobeAltIcon,
  ClipboardDocumentListIcon,
  ChartBarIcon,
  ArrowPathIcon,
  WalletIcon,
  BoltIcon,
  CheckCircleIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  CpuChipIcon,
  AcademicCapIcon,
  TrashIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import { Group as PanelGroup, Panel } from 'react-resizable-panels'
import clsx from 'clsx'
import { useAgentStore, type TaskStatus } from '../../store/agentStore'
import { useWalletStore } from '../../store/walletStore'
import { useNetworkStore, getNodeDisplayName } from '../../store/networkStore'
import { useDataStore } from '../../store/dataStore'
import AgentCard from './AgentCard'
import TaskCard from './TaskCard'
import AgentDetail from './AgentDetail'
import TaskDetail from './TaskDetail'
import LogViewer from './LogViewer'
import MessageFlow from './MessageFlow'
import NetworkTopology from './NetworkTopology'
import NetworkControls from './NetworkControls'
import LANNodesList from './LANNodesList'
import ConnectedNodesList from './ConnectedNodesList'
import ConnectionHistory from './ConnectionHistory'
import NetworkEventBridge from './NetworkEventBridge'
import AgentEventBridge from './AgentEventBridge'
import WSEventBridge from './WSEventBridge'
import InboundPeersList from './InboundPeersList'
import AddNodeDialog from './AddNodeDialog'
import Marketplace from './Marketplace'
import WalletCard from './WalletCard'
import AgentEditorDialog from './AgentEditorDialog'
import CreateTaskDialog from './CreateTaskDialog'
import SchedulerPanel from './SchedulerPanel'
import MemoryPanel from './MemoryPanel'
import ChannelsPanel from './ChannelsPanel'
import GatewayPanel from './GatewayPanel'
import SkillsPanel from './SkillsPanel'
import ToolsPanel from './ToolsPanel'
import ResizeHandle from '../ResizeHandle'
import NetworkStatusSwitch from '../NetworkStatusSwitch'
import { useSkillStore } from '../../store/skillStore'
import { COORDINATOR_ID, LOCAL_COORDINATOR_NAME, isDemoId } from '../../utils/ids'
import { debugLog } from '../../utils/debug'
import { formatUSDC } from '../../utils/format'
import { reportUiError } from '../../utils/errorReporting'

type TabType = 'overview' | 'agents' | 'tasks' | 'network' | 'wallet' | 'system'
type AgentSubTab = 'team' | 'skills' | 'tools'

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabType>('overview')
  const [agentSubTab, setAgentSubTab] = useState<AgentSubTab>('team')
  const [showCreateAgent, setShowCreateAgent] = useState(false)
  const [showCreateTask, setShowCreateTask] = useState(false)
  const [showAddNode, setShowAddNode] = useState(false)
  const [expandedPanel, setExpandedPanel] = useState<'logs' | null>(null)
  const [tasksSortDesc, setTasksSortDesc] = useState(true) // default: newest first
  const [taskStatusFilter, setTaskStatusFilter] = useState<'all' | 'active' | TaskStatus>('all')
  const [taskOriginFilter, setTaskOriginFilter] = useState<'all' | 'local' | 'remote'>('all')
  const [taskCapabilityFilter, setTaskCapabilityFilter] = useState<string>('all')
  const [taskSearchQuery, setTaskSearchQuery] = useState('')
  
  const { 
    agents, 
    tasks, 
    selectedAgentId, 
    selectedTaskId,
    setSelectedAgent,
    setSelectedTask,
    createTask,
    clearAllTasks,
    loadTasksFromDB,
    loadAgentsFromDB,
    isLoadingTasks,
  } = useAgentStore()
  const [showClearTasksConfirm, setShowClearTasksConfirm] = useState(false)

  const { balance, isMock, tokenSymbol } = useWalletStore()
  const { status: networkStatus, wanNodes, connectionSessions } = useNetworkStore()
  const { skills, loadSkills } = useSkillStore()

  useEffect(() => { loadSkills() }, [loadSkills])
  
  // Use centralized data refresh
  const { refresh: loadAllDemoData } = useDataStore()
  
  // Centralized API base URL
  const apiBase = getApiBase()
  
  // Close fullscreen panel on Escape
  useEffect(() => {
    if (!expandedPanel) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpandedPanel(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [expandedPanel])

  // Load persisted tasks and agents from database on mount
  useEffect(() => {
    debugLog('[Dashboard] Loading tasks and agents from database...')
    loadTasksFromDB()
    loadAgentsFromDB()
    
    // Also fetch in-memory tasks for any active tasks
    const fetchActiveTasks = async () => {
      try {
        const url = `${apiBase}/api/agent/tasks`
        const response = await fetch(url)
        if (response.ok) {
          const data = await response.json()
          debugLog('[Dashboard] Fetched active tasks:', data.tasks?.length || 0)
          // Add each task to the store (will skip duplicates due to id check in createTask)
          data.tasks?.forEach((task: Record<string, unknown>) => {
            const createdAtRaw = (task.created_at as number | undefined) ?? (task.createdAt as number | undefined)
            createTask({
              id: task.id as string,
              name: (task.name as string) || (task.prompt as string)?.substring(0, 50) || (task.description as string)?.substring(0, 50) || 'Task',
              description: (task.prompt as string) || (task.description as string) || '',
              assignedAgents: (task.assigned_agents as string[]) || [COORDINATOR_ID],
              status: task.status as TaskStatus,
              createdAt: createdAtRaw ? (createdAtRaw > 1e12 ? createdAtRaw : createdAtRaw * 1000) : Date.now(),
            })
          })
        }
      } catch (error) {
        reportUiError({
          source: 'Dashboard',
          title: 'Task Sync Failed',
          userMessage: 'Failed to fetch active tasks from backend.',
          error,
        })
      }
    }
    fetchActiveTasks()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  
  // Remote nodes (connected via network)
  const remoteNodes = wanNodes
  const lanRemoteCount = remoteNodes.filter(n => n.type === 'lan').length
  const wanRemoteCount = remoteNodes.filter(n => n.type !== 'lan').length

  // Filter out demo data - only show real tasks/agents
  const realTasksUnsorted = useMemo(
    () => tasks.filter(t => !isDemoId(t.id)),
    [tasks],
  )
  // Sort tasks by creation time (toggle asc/desc)
  const realTasks = useMemo(() => {
    const sorted = [...realTasksUnsorted]
    sorted.sort((a, b) =>
      tasksSortDesc
        ? (b.createdAt || 0) - (a.createdAt || 0)
        : (a.createdAt || 0) - (b.createdAt || 0)
    )
    return sorted
  }, [realTasksUnsorted, tasksSortDesc])
  const taskCapabilityOptions = useMemo(() => {
    const caps = new Set<string>()
    for (const task of realTasksUnsorted) {
      for (const member of task.poolMembers || []) {
        for (const cap of member.capabilities || []) {
          const normalized = String(cap || '').trim()
          if (normalized) caps.add(normalized)
        }
      }
    }
    return Array.from(caps).sort((a, b) => a.localeCompare(b)).slice(0, 8)
  }, [realTasksUnsorted])
  const filteredTasks = useMemo(() => {
    const query = taskSearchQuery.trim().toLowerCase()
    return realTasks.filter((task) => {
      if (taskStatusFilter !== 'all') {
        const isActive = task.status === 'running' || task.status === 'delegated'
        if (taskStatusFilter === 'active') {
          if (!isActive) return false
        } else if (task.status !== taskStatusFilter) {
          return false
        }
      }

      if (taskOriginFilter === 'local' && task.origin === 'remote') return false
      if (taskOriginFilter === 'remote' && task.origin !== 'remote') return false

      if (taskCapabilityFilter !== 'all') {
        const cap = taskCapabilityFilter.toLowerCase()
        const matchedPool = (task.poolMembers || []).some((member) =>
          (member.capabilities || []).some((c) => String(c || '').toLowerCase().includes(cap))
        )
        const matchedText = `${task.name} ${task.description}`.toLowerCase().includes(cap)
        if (!matchedPool && !matchedText) return false
      }

      if (query) {
        const searchable = [
          task.name,
          task.description,
          task.assignedTo || '',
          task.requesterId || '',
          (task.poolMembers || []).map((m) => `${m.name} ${(m.capabilities || []).join(' ')}`).join(' '),
        ].join(' ').toLowerCase()
        if (!searchable.includes(query)) return false
      }
      return true
    })
  }, [realTasks, taskCapabilityFilter, taskOriginFilter, taskSearchQuery, taskStatusFilter])
  const filteredRunningTasks = useMemo(
    () => filteredTasks.filter((task) => task.status === 'running' || task.status === 'delegated').length,
    [filteredTasks],
  )
  const realAgents = agents.filter(a => !isDemoId(a.id))
  
  // Find selected agent (from local agents or remote nodes)
  const selectedAgent = agents.find(a => a.id === selectedAgentId) || 
                        remoteNodes.find(n => n.id === selectedAgentId)
  const selectedTask = tasks.find(t => t.id === selectedTaskId)

  // Count by role (exclude demo)
  const organizers = realAgents.filter(a => a.type === 'organizer')
  const coordinators = realAgents.filter(a => a.type === 'coordinator')
  const workers = realAgents.filter(a => a.type === 'worker')
  const onlineWorkerCount = workers.filter(w => w.status !== 'offline').length
  const offlineWorkerCount = workers.filter(w => w.status === 'offline').length
  
  const totalAgentCount = realAgents.length + remoteNodes.length
  const onlineAgents = realAgents.filter(a => a.status === 'online' || a.status === 'busy' || a.status === 'idle').length +
                       remoteNodes.filter(n => n.status === 'online' || n.status === 'busy').length
  // Active tasks = running OR delegated (both are "in-flight")
  const runningTasks = realTasksUnsorted.filter(t => t.status === 'running' || t.status === 'delegated').length
  const completedTasks = realTasksUnsorted.filter(t => t.status === 'completed').length
  const failedTasks = realTasksUnsorted.filter(t => t.status === 'failed').length

  // Only count nodes that are actually connected (status online/busy AND have connectedSince)
  const onlineNodes = wanNodes.filter(n =>
    (n.status === 'online' || n.status === 'busy') && n.connectedSince
  ).length
  
  const tabs: Array<{ 
    id: TabType; 
    label: string; 
    icon: React.ElementType; 
    active?: number;  // Active/online count
    total?: number;   // Total count
  }> = [
    { id: 'overview', label: 'Overview', icon: ChartBarIcon },
    { id: 'agents', label: 'Agents', icon: UserGroupIcon, active: onlineAgents, total: totalAgentCount },
    { id: 'tasks', label: 'Tasks', icon: ClipboardDocumentListIcon, active: runningTasks, total: realTasksUnsorted.length },
    { id: 'network', label: 'AgentaNet', icon: GlobeAltIcon, active: onlineNodes, total: wanNodes.length },
    { id: 'wallet', label: 'Wallet', icon: WalletIcon },
    { id: 'system', label: 'System', icon: CpuChipIcon },
  ]

  return (
    <PanelGroup orientation="horizontal" className="h-full">
      {/* Real-time event bridges */}
      <AgentEventBridge />
      <WSEventBridge />
      
      {/* Main Content */}
      <Panel defaultSize={75} minSize={50} className="flex flex-col min-w-0">
        {/* Tabs + Actions (single row) */}
        <div className="flex items-center justify-between px-6 py-2 border-b border-dark-border bg-dark-bg/50">
          {/* Tabs */}
          <div className="flex items-center gap-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={clsx(
                  'flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors',
                  activeTab === tab.id
                    ? 'bg-primary-500/20 text-primary-400'
                    : 'text-gray-400 hover:bg-dark-hover hover:text-gray-200'
                )}
              >
                <tab.icon className="w-4 h-4" />
                <span className="text-sm font-medium">{tab.label}</span>
                {tab.total !== undefined && (
                  <span className={clsx(
                    'px-1.5 py-0.5 rounded-full text-xs',
                    activeTab === tab.id ? 'bg-primary-500/30' : 'bg-dark-hover'
                  )}>
                    <span className={tab.active && tab.active > 0 ? 'text-green-400' : ''}>{tab.active}</span>
                    <span className="text-gray-500 mx-0.5">/</span>
                    <span>{tab.total}</span>
                  </span>
                )}
              </button>
            ))}
          </div>
          
          {/* Actions */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-3 mr-2 text-xs">
              <span className="flex items-center gap-1">
                <span className="text-gray-500">Agents</span>
                <span className={onlineAgents > 0 ? 'text-green-400 font-medium' : 'text-gray-500'}>{onlineAgents}</span>
                <span className="text-gray-600">/</span>
                <span className="text-gray-400">{totalAgentCount}</span>
              </span>
              <span className="flex items-center gap-1">
                <span className="text-gray-500">Tasks</span>
                <span className={runningTasks > 0 ? 'text-primary-400 font-medium' : 'text-gray-500'}>{runningTasks}</span>
                <span className="text-gray-600">/</span>
                <span className="text-gray-400">{realTasksUnsorted.length}</span>
              </span>
            </div>
            {import.meta.env.VITE_ENABLE_DEMO === 'true' && (
              <button
                onClick={loadAllDemoData}
                className="p-1.5 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors"
                title="Reload demo data"
              >
                <ArrowPathIcon className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={() => setShowCreateAgent(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 bg-primary-600 hover:bg-primary-700 
                         text-white rounded-lg transition-colors text-xs"
            >
              <PlusIcon className="w-3.5 h-3.5" />
              Agent
            </button>
            <button
              onClick={() => setShowCreateTask(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 bg-dark-hover hover:bg-dark-border 
                         text-gray-300 rounded-lg transition-colors text-xs"
            >
              <PlusIcon className="w-3.5 h-3.5" />
              Task
            </button>
          </div>
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-y-auto thin-scrollbar p-6">
          {activeTab === 'overview' && (
            <div className="space-y-6">
              {/* Top Row: Key Stats */}
              <div className="grid grid-cols-5 gap-4">
                {/* Workers Online */}
                <div className="p-4 rounded-xl bg-dark-surface border border-dark-border">
                  <div className="flex items-center gap-2 mb-2">
                    <WrenchScrewdriverIcon className="w-5 h-5 text-green-400" />
                    <p className="text-sm text-gray-500">Workers</p>
                  </div>
                  <p className="text-2xl font-bold text-green-400">{onlineWorkerCount}<span className="text-sm font-normal text-gray-500">/{workers.length}</span></p>
                  <p className="text-xs text-gray-500 mt-1">
                    {offlineWorkerCount > 0 ? `${offlineWorkerCount} offline` : 'All online'}
                  </p>
                </div>
                {/* Running Tasks */}
                <div className="p-4 rounded-xl bg-dark-surface border border-dark-border">
                  <div className="flex items-center gap-2 mb-2">
                    <BoltIcon className="w-5 h-5 text-blue-400" />
                    <p className="text-sm text-gray-500">Active Tasks</p>
                  </div>
                  <p className="text-2xl font-bold text-blue-400">{runningTasks}</p>
                  <p className="text-xs text-gray-500 mt-1">{realTasks.length} total</p>
                </div>
                {/* Completed / Failed */}
                <div className="p-4 rounded-xl bg-dark-surface border border-dark-border">
                  <div className="flex items-center gap-2 mb-2">
                    <CheckCircleIcon className="w-5 h-5 text-emerald-400" />
                    <p className="text-sm text-gray-500">Completed</p>
                  </div>
                  <p className="text-2xl font-bold text-emerald-400">{completedTasks}</p>
                  {failedTasks > 0 && (
                    <p className="text-xs text-red-400 mt-1">{failedTasks} failed</p>
                  )}
                  {failedTasks === 0 && (
                    <p className="text-xs text-gray-500 mt-1">0 failed</p>
                  )}
                </div>
                {/* Agentic Nodes */}
                <div className="p-4 rounded-xl bg-dark-surface border border-dark-border">
                  <div className="flex items-center gap-2 mb-2">
                    <GlobeAltIcon className="w-5 h-5 text-orange-400" />
                    <p className="text-sm text-gray-500">Agentic Nodes</p>
                  </div>
                  <p className="text-2xl font-bold text-orange-400">{remoteNodes.length}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {onlineNodes > 0 ? `${onlineNodes} connected` : 'None connected'}
                  </p>
                </div>
                {/* Wallet Balance */}
                <div className="p-4 rounded-xl bg-dark-surface border border-dark-border bg-gradient-to-br from-primary-500/5 to-purple-500/5">
                  <div className="flex items-center gap-2 mb-2">
                    <WalletIcon className="w-5 h-5 text-primary-400" />
                    <p className="text-sm text-gray-500">Balance</p>
                    {isMock && (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-yellow-500/20 text-yellow-400">MOCK</span>
                    )}
                  </div>
                  <p className="text-2xl font-bold text-primary-400">{formatUSDC(balance)} {tokenSymbol}</p>
                  <p className="text-xs text-gray-500 mt-1">x402 wallet</p>
                </div>
              </div>

              {/* Network + Wallet - Resizable */}
              <PanelGroup orientation="horizontal" className="min-h-[320px]">
                <Panel defaultSize={65} minSize={40}>
                  <div className="h-full rounded-xl bg-dark-surface border border-dark-border overflow-hidden flex flex-col">
                    <div className="px-4 py-2 border-b border-dark-border shrink-0">
                      <h3 className="text-sm font-medium text-gray-400">AgentaNet Topology</h3>
                    </div>
                    <div className="flex-1 min-h-0">
                      <NetworkTopology />
                    </div>
                  </div>
                </Panel>
                
                <ResizeHandle direction="horizontal" />
                
                <Panel defaultSize={35} minSize={20}>
                  <div className="h-full ml-2">
                    <WalletCard />
                  </div>
                </Panel>
              </PanelGroup>

              {/* Tasks + Messages - Resizable */}
              <PanelGroup orientation="horizontal" className="min-h-[280px]">
                <Panel defaultSize={50} minSize={25}>
                  <div className="h-full pr-2 min-w-0">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-medium text-gray-400">Recent Tasks</h3>
                      <div className="flex items-center gap-4 text-xs">
                        <span className="text-blue-400">{runningTasks} running</span>
                        {realTasks.filter(t => t.origin === 'remote').length > 0 && (
                          <span className="text-cyan-400">{realTasks.filter(t => t.origin === 'remote').length} remote</span>
                        )}
                      </div>
                    </div>
                    {/* Local tasks first, then remote tasks */}
                    <div className="overflow-x-auto overflow-y-auto thin-scrollbar min-w-0" style={{ maxHeight: '420px' }}>
                      <div className="space-y-3 min-w-[320px] pr-1">
                        {/* Local tasks */}
                        {realTasks.filter(t => t.origin !== 'remote').map((task) => (
                          <TaskCard
                            key={task.id}
                            task={task}
                            isSelected={task.id === selectedTaskId}
                            onClick={() => setSelectedTask(task.id)}
                          />
                        ))}
                        {/* Remote tasks (received from other ANs) */}
                        {realTasks.filter(t => t.origin === 'remote').length > 0 && (
                          <>
                            <div className="flex items-center gap-2 pt-2">
                              <div className="h-px flex-1 bg-cyan-500/20" />
                              <span className="text-[10px] text-cyan-400 font-medium">REMOTE TASKS</span>
                              <div className="h-px flex-1 bg-cyan-500/20" />
                            </div>
                            {realTasks.filter(t => t.origin === 'remote').map((task) => (
                              <TaskCard
                                key={task.id}
                                task={task}
                                isSelected={task.id === selectedTaskId}
                                onClick={() => setSelectedTask(task.id)}
                              />
                            ))}
                          </>
                        )}
                        {realTasks.length === 0 && (
                          <p className="text-gray-500">No tasks yet. Start a task in Chat.</p>
                        )}
                      </div>
                    </div>
                  </div>
                </Panel>
                
                <ResizeHandle direction="horizontal" />
                
                <Panel defaultSize={50} minSize={25}>
                  <div className="h-full rounded-xl bg-dark-surface border border-dark-border overflow-hidden ml-2">
                    <MessageFlow maxHeight="248px" />
                  </div>
                </Panel>
              </PanelGroup>

              {/* Logs */}
              <div className="rounded-xl bg-dark-surface border border-dark-border overflow-hidden h-[200px]">
                <LogViewer maxHeight="168px" onExpand={() => setExpandedPanel('logs')} />
              </div>
            </div>
          )}

          {activeTab === 'agents' && (
            <div className="space-y-4">
              {/* Sub-tab navigation */}
              <div className="flex items-center gap-1 p-1 bg-dark-surface border border-dark-border rounded-xl">
                {([
                  { id: 'team' as AgentSubTab, label: 'Team', icon: UserGroupIcon, count: totalAgentCount },
                  { id: 'skills' as AgentSubTab, label: 'Skills', icon: AcademicCapIcon, count: skills.length },
                  { id: 'tools' as AgentSubTab, label: 'Tools', icon: WrenchScrewdriverIcon },
                ]).map(sub => {
                  const SubIcon = sub.icon
                  return (
                    <button key={sub.id} onClick={() => setAgentSubTab(sub.id)}
                      className={clsx(
                        'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors flex-1 justify-center',
                        agentSubTab === sub.id
                          ? 'bg-primary-500/15 text-primary-400 shadow-sm'
                          : 'text-gray-500 hover:text-gray-300 hover:bg-dark-hover'
                      )}>
                      <SubIcon className="w-4 h-4" />
                      {sub.label}
                      {sub.count !== undefined && (
                        <span className={clsx('px-1.5 py-0.5 rounded-full text-[10px]',
                          agentSubTab === sub.id ? 'bg-primary-500/20' : 'bg-dark-bg text-gray-500'
                        )}>{sub.count}</span>
                      )}
                    </button>
                  )
                })}
              </div>

              {/* Sub-tab content */}
              {agentSubTab === 'team' && (
                <div className="space-y-6">
                  {/* Organizer & Coordinator -- compact summary */}
                  {(organizers.length > 0 || coordinators.length > 0) && (
                    <div className="flex items-center gap-3 p-3 rounded-xl bg-dark-surface border border-dark-border">
                      {organizers.map((org) => (
                        <button
                          key={org.id}
                          onClick={() => setSelectedAgent(org.id)}
                          className={clsx(
                            'flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors',
                            org.id === selectedAgentId ? 'bg-purple-500/20 ring-1 ring-purple-500/40' : 'hover:bg-dark-hover'
                          )}
                        >
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: org.status === 'online' || org.status === 'busy' ? '#a855f7' : '#6b7280' }} />
                          <span className="text-xs font-medium text-purple-400">Organizer</span>
                          <span className={clsx(
                            'text-[10px] px-1.5 py-0.5 rounded-full capitalize',
                            org.status === 'online' && 'bg-green-500/20 text-green-400',
                            org.status === 'busy' && 'bg-yellow-500/20 text-yellow-400',
                            org.status === 'offline' && 'bg-gray-500/20 text-gray-500',
                            org.status === 'error' && 'bg-red-500/20 text-red-400',
                            org.status === 'idle' && 'bg-blue-500/20 text-blue-400',
                          )}>{org.status}</span>
                        </button>
                      ))}
                      {organizers.length > 0 && coordinators.length > 0 && (
                        <div className="w-px h-6 bg-dark-border" />
                      )}
                      {coordinators.map((coord) => (
                        <button
                          key={coord.id}
                          onClick={() => setSelectedAgent(coord.id)}
                          className={clsx(
                            'flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors',
                            coord.id === selectedAgentId ? 'bg-blue-500/20 ring-1 ring-blue-500/40' : 'hover:bg-dark-hover'
                          )}
                        >
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: coord.status === 'online' || coord.status === 'busy' ? '#3b82f6' : '#6b7280' }} />
                          <span className="text-xs font-medium text-blue-400">{coord.name || LOCAL_COORDINATOR_NAME}</span>
                          <span className={clsx(
                            'text-[10px] px-1.5 py-0.5 rounded-full capitalize',
                            coord.status === 'online' && 'bg-green-500/20 text-green-400',
                            coord.status === 'busy' && 'bg-yellow-500/20 text-yellow-400',
                            coord.status === 'offline' && 'bg-gray-500/20 text-gray-500',
                            coord.status === 'error' && 'bg-red-500/20 text-red-400',
                            coord.status === 'idle' && 'bg-blue-500/20 text-blue-400',
                          )}>{coord.status}</span>
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Workers */}
                  {(() => {
                    const onlineWorkers = workers.filter(w => w.status !== 'offline')
                    const offlineWorkers = workers.filter(w => w.status === 'offline')
                    return (
                      <>
                        <div>
                          <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
                            <span>Workers</span>
                            <span className="px-2 py-0.5 rounded-full bg-green-500/20 text-green-400 text-xs">
                              {onlineWorkers.length} online
                            </span>
                            {offlineWorkers.length > 0 && (
                              <span className="px-2 py-0.5 rounded-full bg-gray-500/20 text-gray-500 text-xs">
                                {offlineWorkers.length} offline
                              </span>
                            )}
                          </h3>
                          <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
                            {onlineWorkers.map((agent) => (
                              <AgentCard
                                key={agent.id}
                                agent={agent}
                                isSelected={agent.id === selectedAgentId}
                                onClick={() => setSelectedAgent(agent.id)}
                              />
                            ))}
                            {onlineWorkers.length === 0 && (
                              <p className="text-gray-500 col-span-full">No online workers. Bring workers online from the offline list below.</p>
                            )}
                          </div>
                        </div>

                        {offlineWorkers.length > 0 && (
                          <div>
                            <h3 className="text-sm font-medium text-gray-500 mb-3 flex items-center gap-2">
                              <span>Offline Workers</span>
                              <span className="px-2 py-0.5 rounded-full bg-gray-500/20 text-gray-500 text-xs">{offlineWorkers.length}</span>
                            </h3>
                            <div className="grid grid-cols-2 xl:grid-cols-3 gap-4 opacity-60">
                              {offlineWorkers.map((agent) => (
                                <AgentCard
                                  key={agent.id}
                                  agent={agent}
                                  isSelected={agent.id === selectedAgentId}
                                  onClick={() => setSelectedAgent(agent.id)}
                                />
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    )
                  })()}

                  {/* Agentic Nodes */}
                  <div>
                    <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
                      <GlobeAltIcon className="w-4 h-4 text-orange-400" />
                      <span>Remote Agentic Nodes</span>
                      <span className="px-2 py-0.5 rounded-full bg-dark-hover text-gray-300 text-xs">{remoteNodes.length}</span>
                      {lanRemoteCount > 0 && (
                        <span className="px-2 py-0.5 rounded-full bg-green-500/20 text-green-400 text-xs">
                          LAN {lanRemoteCount}
                        </span>
                      )}
                      {wanRemoteCount > 0 && (
                        <span className="px-2 py-0.5 rounded-full bg-orange-500/20 text-orange-400 text-xs">
                          WAN {wanRemoteCount}
                        </span>
                      )}
                    </h3>
                    <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
                      {remoteNodes.map((node) => (
                        <AgentCard
                          key={node.id}
                          agent={{
                            id: node.id,
                            name: getNodeDisplayName(node),
                            type: 'agentic_node',
                            endpoint: node.endpoint || `${node.ip}:${node.port}`,
                            status: node.status,
                            capabilities: node.capabilities || [],
                            lastSeen: node.last_seen,
                            metadata: { region: node.region, type: node.type },
                          }}
                          isSelected={node.id === selectedAgentId}
                          onClick={() => setSelectedAgent(node.id)}
                        />
                      ))}
                      {remoteNodes.length === 0 && (
                        <p className="text-gray-500 col-span-full">No Agentic Nodes connected</p>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {agentSubTab === 'skills' && (
                <SkillsPanel />
              )}

              {agentSubTab === 'tools' && (
                <ToolsPanel />
              )}
            </div>
          )}

          {activeTab === 'tasks' && (
            <div>
              {/* Sort control + Clear all */}
              <div className="flex items-center justify-between mb-4">
                <span className="text-sm text-gray-400">
                  {filteredTasks.length} task{filteredTasks.length !== 1 ? 's' : ''}
                  {filteredRunningTasks > 0 && <span className="text-blue-400 ml-2">{filteredRunningTasks} active</span>}
                </span>
                <div className="flex items-center gap-2">
                  {realTasks.length > 0 && (
                    <button
                      onClick={() => setShowClearTasksConfirm(true)}
                      className="flex items-center gap-1 text-xs text-gray-500 hover:text-red-400 transition-colors px-2 py-1 rounded hover:bg-red-500/10"
                      title="Clear all tasks"
                    >
                      <TrashIcon className="w-3.5 h-3.5" />
                      Clear All
                    </button>
                  )}
                  {realTasks.length > 1 && (
                    <button
                      onClick={() => setTasksSortDesc(d => !d)}
                      className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 transition-colors px-2 py-1 rounded hover:bg-dark-hover"
                      title={tasksSortDesc ? 'Newest first' : 'Oldest first'}
                    >
                      {tasksSortDesc ? (
                        <><ChevronDownIcon className="w-3.5 h-3.5" /> Newest first</>
                      ) : (
                        <><ChevronUpIcon className="w-3.5 h-3.5" /> Oldest first</>
                      )}
                    </button>
                  )}
                </div>
              </div>
              <div className="mb-4 rounded-xl border border-dark-border bg-dark-surface p-3 space-y-2">
                <div className="flex flex-wrap gap-2">
                  {(['all', 'active', 'running', 'completed', 'failed', 'cancelled'] as const).map((status) => (
                    <button
                      key={status}
                      type="button"
                      onClick={() => setTaskStatusFilter(status)}
                      className={clsx(
                        'px-2.5 py-1 rounded-full text-xs border transition-colors',
                        taskStatusFilter === status
                          ? 'border-primary-500/40 bg-primary-500/15 text-primary-300'
                          : 'border-dark-border bg-dark-bg text-gray-400 hover:text-gray-200',
                      )}
                    >
                      {status}
                    </button>
                  ))}
                  {(['all', 'local', 'remote'] as const).map((origin) => (
                    <button
                      key={origin}
                      type="button"
                      onClick={() => setTaskOriginFilter(origin)}
                      className={clsx(
                        'px-2.5 py-1 rounded-full text-xs border transition-colors',
                        taskOriginFilter === origin
                          ? 'border-cyan-500/40 bg-cyan-500/12 text-cyan-300'
                          : 'border-dark-border bg-dark-bg text-gray-400 hover:text-gray-200',
                      )}
                    >
                      {origin}
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={taskCapabilityFilter}
                    onChange={(e) => setTaskCapabilityFilter(e.target.value)}
                    className="px-2.5 py-1.5 rounded-lg border border-dark-border bg-dark-bg text-xs text-gray-300"
                  >
                    <option value="all">All capabilities</option>
                    {taskCapabilityOptions.map((cap) => (
                      <option key={cap} value={cap}>{cap}</option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={taskSearchQuery}
                    onChange={(e) => setTaskSearchQuery(e.target.value)}
                    placeholder="Search tasks..."
                    className="min-w-[180px] flex-1 px-3 py-1.5 rounded-lg border border-dark-border bg-dark-bg text-xs text-white placeholder-gray-500"
                  />
                  {(taskStatusFilter !== 'all' || taskOriginFilter !== 'all' || taskCapabilityFilter !== 'all' || taskSearchQuery.trim()) && (
                    <button
                      type="button"
                      onClick={() => {
                        setTaskStatusFilter('all')
                        setTaskOriginFilter('all')
                        setTaskCapabilityFilter('all')
                        setTaskSearchQuery('')
                      }}
                      className="px-2.5 py-1.5 rounded-lg text-xs border border-dark-border text-gray-400 hover:text-gray-200 hover:bg-dark-bg"
                    >
                      Reset filters
                    </button>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                {isLoadingTasks && (
                  <div className="col-span-full text-center py-4 text-gray-500">
                    <ArrowPathIcon className="w-6 h-6 mx-auto mb-2 animate-spin" />
                    <p className="text-sm">Loading tasks from database...</p>
                  </div>
                )}
                {filteredTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    isSelected={task.id === selectedTaskId}
                    onClick={() => setSelectedTask(task.id)}
                  />
                ))}
                {!isLoadingTasks && filteredTasks.length === 0 && (
                  <div className="col-span-full text-center py-12 text-gray-500">
                    <ClipboardDocumentListIcon className="w-12 h-12 mx-auto mb-3 opacity-50" />
                    <p>No tasks match current filters. Adjust filters or create a new task.</p>
                    <button
                      onClick={() => setShowCreateTask(true)}
                      className="mt-4 text-primary-400 hover:text-primary-300"
                    >
                      Create your first task
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'network' && (
            <div className="space-y-3 h-full flex flex-col min-h-0 overflow-y-auto thin-scrollbar">
              <NetworkEventBridge />
              {/* Header with Online/Offline Switch */}
              <div className="flex items-center justify-between shrink-0">
                <div className="flex items-center gap-4">
                  <div>
                    <h3 className="text-lg font-medium text-white">AgentaNet Network</h3>
                    <p className="text-sm text-gray-500">
                      Global network of autonomous agents
                    </p>
                  </div>
                  {/* Online/Offline Switch - prominent placement */}
                  <NetworkStatusSwitch />
                </div>
                <div className="flex items-center gap-3">
                  <NetworkControls />
                  <button
                    onClick={() => setShowAddNode(true)}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium rounded-lg transition-colors"
                  >
                    <GlobeAltIcon className="w-3.5 h-3.5" />
                    Add Node
                  </button>
                </div>
              </div>
              
              {/* LAN Nodes List - shows when discovery is active */}
              <div className="shrink-0">
                <LANNodesList />
              </div>
              
              {/* Main Content: Topology + Marketplace */}
              <PanelGroup orientation="horizontal" className="flex-1 min-h-[320px]">
                <Panel defaultSize={65} minSize={30}>
                  <div className="h-full rounded-xl bg-dark-surface border border-dark-border overflow-hidden flex flex-col">
                    <div className="px-4 py-2 border-b border-dark-border shrink-0">
                      <h3 className="text-sm font-medium text-gray-400">Network Topology</h3>
                    </div>
                    <div className="flex-1 min-h-0">
                      <NetworkTopology />
                    </div>
                  </div>
                </Panel>
                
                <ResizeHandle direction="horizontal" />
                
                <Panel defaultSize={35} minSize={20}>
                  <div className="h-full rounded-xl bg-dark-surface border border-dark-border overflow-hidden flex flex-col ml-2">
                    <Marketplace />
                  </div>
                </Panel>
              </PanelGroup>
              
              {/* Bottom Section: Connected Nodes / Connected To Me / Sessions
                 - Only render panels that have content, so they always fill available width. */}
              {(() => {
                const showConnected = wanNodes.length > 0
                const showInbound = networkStatus === 'online'
                const showSessions = connectionSessions.length > 0 || showConnected || showInbound
                const panels = [
                  showConnected ? <ConnectedNodesList key="connected" /> : null,
                  showInbound ? <InboundPeersList key="inbound" /> : null,
                  showSessions ? <ConnectionHistory key="sessions" /> : null,
                ].filter(Boolean) as React.ReactElement[]

                if (panels.length === 0) return null
                if (panels.length === 1) {
                  return <div className="shrink-0">{panels[0]}</div>
                }

                const defaultSize = 100 / panels.length
                return (
                  <PanelGroup orientation="horizontal" className="shrink-0" style={{ height: 'auto' }}>
                    {panels.map((panelEl, idx) => (
                      <React.Fragment key={panelEl.key ?? idx}>
                        <Panel defaultSize={defaultSize} minSize={20}>
                          {panelEl}
                        </Panel>
                        {idx < panels.length - 1 && <ResizeHandle direction="horizontal" />}
                      </React.Fragment>
                    ))}
                  </PanelGroup>
                )
              })()}
            </div>
          )}

          {activeTab === 'wallet' && (
            <div className="max-w-2xl mx-auto">
              <WalletCard />
            </div>
          )}

          {activeTab === 'system' && (
            <div className="space-y-6 max-w-3xl mx-auto">
              <GatewayPanel />
              <ChannelsPanel />
              <SchedulerPanel />
              <MemoryPanel />
            </div>
          )}
        </div>
      </Panel>

      {/* Agent Detail - Floating Modal Popup */}
      {selectedAgent && (
        <AgentDetail
          agent={selectedAgent as any}
          onClose={() => setSelectedAgent(null)}
        />
      )}

      {/* Task Detail - Floating Modal Popup */}
      {selectedTask && !selectedAgent && (
        <TaskDetail
          task={selectedTask}
          onClose={() => setSelectedTask(null)}
        />
      )}

      {/* Dialogs */}
      {showCreateAgent && (
        <AgentEditorDialog onClose={() => setShowCreateAgent(false)} />
      )}
      {showCreateTask && (
        <CreateTaskDialog onClose={() => setShowCreateTask(false)} />
      )}
      <AddNodeDialog 
        isOpen={showAddNode} 
        onClose={() => setShowAddNode(false)} 
      />

      {/* Clear All Tasks Confirmation */}
      {showClearTasksConfirm && createPortal(
        <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md">
          <div className="bg-dark-surface border border-dark-border rounded-xl p-5 max-w-sm w-full mx-4 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="p-2 rounded-lg bg-red-500/20">
                <ExclamationTriangleIcon className="w-5 h-5 text-red-400" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">Clear All Tasks</h3>
                <p className="text-xs text-gray-400 mt-1">
                  This will permanently delete all {realTasks.length} task{realTasks.length !== 1 ? 's' : ''} and their history. This cannot be undone.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowClearTasksConfirm(false)}
                className="px-3 py-1.5 text-xs text-gray-400 hover:text-white hover:bg-dark-hover rounded-lg transition-colors">
                Cancel
              </button>
              <button onClick={async () => { await clearAllTasks(); setShowClearTasksConfirm(false) }}
                className="px-3 py-1.5 text-xs font-medium bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors">
                Delete All Tasks
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Fullscreen Logs Modal — rendered via portal to escape stacking context */}
      {expandedPanel === 'logs' && createPortal(
        <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-md">
          <div className="w-[90vw] h-[85vh] bg-dark-surface border border-dark-border rounded-xl shadow-2xl flex flex-col animate-fade-in">
            <div className="flex items-center justify-between px-6 py-4 border-b border-dark-border shrink-0">
              <h2 className="text-lg font-semibold text-white">Logs</h2>
              <button
                onClick={() => setExpandedPanel(null)}
                className="p-2 hover:bg-dark-hover rounded-lg transition-colors text-gray-400 hover:text-white"
                title="Close (Esc)"
              >
                <ArrowsPointingInIcon className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-hidden">
              <LogViewer maxHeight="100%" />
            </div>
          </div>
        </div>,
        document.body
      )}
    </PanelGroup>
  )
}
