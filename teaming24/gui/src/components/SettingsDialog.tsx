import { Fragment, useState, useEffect } from 'react'
import { Dialog, Transition, Switch } from '@headlessui/react'
import {
  XMarkIcon,
  Cog6ToothIcon,
  ComputerDesktopIcon,
  GlobeAltIcon,
  CpuChipIcon,
  BellIcon,
  WrenchScrewdriverIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  ChatBubbleLeftRightIcon,
  PuzzlePieceIcon,
} from '@heroicons/react/24/outline'
import { useSettingsStore, DEFAULT_SETTINGS, Settings } from '../store/settingsStore'
import { useAgentStore } from '../store/agentStore'
import { useChatStore } from '../store/chatStore'
import { useDataStore } from '../store/dataStore'
import { useNetworkStore } from '../store/networkStore'
import { useWalletStore } from '../store/walletStore'
import { getApiBase } from '../utils/api'
import { formatNumberNoTrailingZeros } from '../utils/format'

interface SettingsDialogProps {
  isOpen: boolean
  onClose: () => void
}

type SettingsTab = 'general' | 'node' | 'network' | 'llm' | 'agent' | 'channels' | 'integrations' | 'notifications' | 'advanced'
type ProviderKey = 'openai' | 'anthropic' | 'flock' | 'local'
const PROVIDER_KEYS: ProviderKey[] = ['openai', 'anthropic', 'flock', 'local']

const PROVIDER_MODEL_OPTIONS: Record<ProviderKey, string[]> = {
  openai: ['gpt-5.2', 'gpt-5.3-codex', 'gpt-5.2-pro', 'gpt-5-mini'],
  anthropic: ['claude-opus-4-6', 'claude-sonnet-4-6'],
  flock: ['gpt-5.2', 'qwen3-max', 'gemini/gemini-2.5-pro', 'gpt-5.3-codex', 'gpt-5.2-pro', 'gpt-5-mini'],
  local: [],
}

const PROVIDER_LABELS: Record<ProviderKey, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  flock: 'FLock.io',
  local: 'Local',
}

const MODEL_LABELS: Record<string, string> = {
  'gpt-5.2': 'GPT-5.2',
  'gpt-5.3-codex': 'GPT-5.3-Codex',
  'gpt-5.2-pro': 'GPT-5.2 Pro',
  'gpt-5-mini': 'GPT-5-mini',
  'claude-opus-4-6': 'claude-opus-4-6',
  'claude-sonnet-4-6': 'claude-sonnet-4-6',
}

const PROVIDER_SETTING_KEYS: Record<
  ProviderKey,
  { apiKey: keyof Settings; baseUrl: keyof Settings }
> = {
  openai: { apiKey: 'openaiApiKey', baseUrl: 'openaiBaseUrl' },
  anthropic: { apiKey: 'anthropicApiKey', baseUrl: 'anthropicBaseUrl' },
  flock: { apiKey: 'flockApiKey', baseUrl: 'flockBaseUrl' },
  local: { apiKey: 'localApiKey', baseUrl: 'localBaseUrl' },
}

const DEFAULT_COMPONENT_MODEL = 'flock/gpt-5.2'

function normalizeProviderKey(value: unknown): ProviderKey {
  const normalized = String(value || '').trim().toLowerCase()
  return (PROVIDER_KEYS as string[]).includes(normalized) ? (normalized as ProviderKey) : 'flock'
}

export default function SettingsDialog({ isOpen, onClose }: SettingsDialogProps) {
  const settings = useSettingsStore()
  const loadAgentsFromDB = useAgentStore(s => s.loadAgentsFromDB)
  const network = useNetworkStore()
  const [activeTab, setActiveTab] = useState<SettingsTab>('general')
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [showClearAllData, setShowClearAllData] = useState(false)
  const [clearConfirmText, setClearConfirmText] = useState('')
  const [clearingData, setClearingData] = useState(false)
  const [localSettings, setLocalSettings] = useState<Partial<Settings>>({})
  const [hasChanges, setHasChanges] = useState(false)

  // Channel configuration state
  const [channelConfigs, setChannelConfigs] = useState<Record<string, { enabled: boolean; token: string; bot_token: string; app_token: string }>>({
    telegram: { enabled: false, token: '', bot_token: '', app_token: '' },
    slack: { enabled: false, token: '', bot_token: '', app_token: '' },
    discord: { enabled: false, token: '', bot_token: '', app_token: '' },
  })
  const [channelConfigsInitial, setChannelConfigsInitial] = useState(channelConfigs)

  // Integration state
  const [frameworkBackend, setFrameworkBackend] = useState<string>('crewai')
  const [frameworkBackendInitial, setFrameworkBackendInitial] = useState('crewai')

  // Fetch channel and integration data when dialog opens
  useEffect(() => {
    if (!isOpen) return
    fetch(`${getApiBase()}/api/channels`).then(r => r.json()).then(data => {
      const base = {
        telegram: { enabled: false, token: '', bot_token: '', app_token: '' },
        slack: { enabled: false, token: '', bot_token: '', app_token: '' },
        discord: { enabled: false, token: '', bot_token: '', app_token: '' },
      } as typeof channelConfigs
      for (const ch of (data.channels || [])) {
        if (base[ch.id]) {
          base[ch.id] = { ...base[ch.id], enabled: ch.enabled }
        }
      }
      setChannelConfigs(base)
      setChannelConfigsInitial(base)
    }).catch((e) => console.warn('Failed to fetch channel configs:', e))
    fetch(`${getApiBase()}/api/framework`).then(r => r.json()).then(data => {
      const b = data.backend || 'crewai'
      setFrameworkBackend(b)
      setFrameworkBackendInitial(b)
    }).catch((e) => console.warn('Failed to fetch framework backend:', e))
  }, [isOpen])

  const updateChannelConfig = (channel: string, patch: Partial<typeof channelConfigs['telegram']>) => {
    setChannelConfigs(prev => ({ ...prev, [channel]: { ...prev[channel], ...patch } }))
    setHasChanges(true)
  }

  const updateFrameworkBackend = (backend: string) => {
    setFrameworkBackend(backend)
    setHasChanges(true)
  }

  const selectedProvider = normalizeProviderKey(localSettings.defaultLLMProvider || 'flock')
  const providerModels = PROVIDER_MODEL_OPTIONS[selectedProvider] || []

  const getProviderDefaultModel = (provider: ProviderKey): string => {
    if (provider === 'local') {
      return String(localSettings.localCustomModel || DEFAULT_SETTINGS.localCustomModel || 'llama3.1').trim()
    }
    return PROVIDER_MODEL_OPTIONS[provider][0] || 'gpt-5.2'
  }

  const resolvedDefaultModel =
    selectedProvider === 'local'
      ? String(localSettings.localCustomModel || DEFAULT_SETTINGS.localCustomModel || 'llama3.1').trim()
      : (
          providerModels.includes(String(localSettings.defaultModel || ''))
            ? String(localSettings.defaultModel || '')
            : getProviderDefaultModel(selectedProvider)
        )

  const allProviderModels = Array.from(
    new Set([
      ...PROVIDER_MODEL_OPTIONS.openai.map((m) => `openai/${m}`),
      ...PROVIDER_MODEL_OPTIONS.anthropic.map((m) => `anthropic/${m}`),
      ...PROVIDER_MODEL_OPTIONS.flock.map((m) => `flock/${m}`),
      `local/${String(localSettings.localCustomModel || DEFAULT_SETTINGS.localCustomModel || 'llama3.1').trim()}`,
    ]),
  )

  const handleProviderChange = (provider: ProviderKey) => {
    const next: Partial<Settings> = { defaultLLMProvider: provider }
    const current = String(localSettings.defaultModel || '').trim()
    const nextDefault = getProviderDefaultModel(provider)
    if (provider === 'local') {
      next.defaultModel = nextDefault
    } else if (!current) {
      next.defaultModel = nextDefault
    } else if (!PROVIDER_MODEL_OPTIONS[provider].includes(current)) {
      next.defaultModel = nextDefault
    }
    setLocalSettings((prev) => ({ ...prev, ...next }))
    setHasChanges(true)
  }

  const setLocalCustomModel = (value: string) => {
    const modelName = value.trim()
    const patch: Partial<Settings> = { localCustomModel: value }
    if ((localSettings.defaultLLMProvider || 'flock') === 'local') {
      patch.defaultModel = modelName || DEFAULT_SETTINGS.localCustomModel
    }
    setLocalSettings((prev) => ({ ...prev, ...patch }))
    setHasChanges(true)
  }
  
  // Load current settings when dialog opens (use actual network state for network settings)
  useEffect(() => {
    if (isOpen) {
      setLocalSettings({
        theme: settings.theme,
        compactMode: settings.compactMode,
        localNodeName: settings.localNodeName,
        localNodeDescription: settings.localNodeDescription,
        localNodeCapability: settings.localNodeCapability,
        localNodeRegion: settings.localNodeRegion,
        autoConnectOnStartup: settings.autoConnectOnStartup,
        autoJoinMarketplace: settings.autoJoinMarketplace,
        // Use actual network state to reflect current status
        lanDiscoveryEnabled: network.isDiscovering,
        lanDiscoverable: network.isDiscoverable,
        // AgentaNet Central
        agentanetCentralUrl: settings.agentanetCentralUrl,
        agentanetToken: settings.agentanetToken,
        // LLM
        defaultLLMProvider: settings.defaultLLMProvider,
        defaultModel: settings.defaultModel,
        openaiApiKey: settings.openaiApiKey,
        anthropicApiKey: settings.anthropicApiKey,
        flockApiKey: settings.flockApiKey,
        localApiKey: settings.localApiKey,
        openaiBaseUrl: settings.openaiBaseUrl,
        anthropicBaseUrl: settings.anthropicBaseUrl,
        flockBaseUrl: settings.flockBaseUrl,
        localBaseUrl: settings.localBaseUrl,
        localCustomModel: settings.localCustomModel,
        temperature: settings.temperature,
        maxTokens: settings.maxTokens,
                sandboxEnabled: settings.sandboxEnabled,
                sandboxTimeout: settings.sandboxTimeout,
                // CrewAI / Agent
                crewaiVerbose: settings.crewaiVerbose,
                crewaiProcess: settings.crewaiProcess,
                crewaiMemory: settings.crewaiMemory,
                crewaiMaxRpm: settings.crewaiMaxRpm,
                agentScenario: settings.agentScenario,
                organizerModel: settings.organizerModel,
                coordinatorModel: settings.coordinatorModel,
                workerDefaultModel: settings.workerDefaultModel,
                anRouterModel: settings.anRouterModel,
                localAgentRouterModel: settings.localAgentRouterModel,
                // CrewAI Planning & Reasoning
                crewaiPlanning: settings.crewaiPlanning,
                crewaiPlanningLlm: settings.crewaiPlanningLlm,
                crewaiReasoning: settings.crewaiReasoning,
                crewaiMaxReasoningAttempts: settings.crewaiMaxReasoningAttempts,
                crewaiStreaming: settings.crewaiStreaming,
                taskExecutionTimeout: settings.taskExecutionTimeout,
                // Task Output
                taskOutputEnabled: settings.taskOutputEnabled,
                taskOutputDir: settings.taskOutputDir,
                notificationsEnabled: settings.notificationsEnabled,
        soundEnabled: settings.soundEnabled,
        debugMode: settings.debugMode,
        logLevel: settings.logLevel,
      })
      setHasChanges(false)
    }
  }, [isOpen, settings, network.isDiscovering, network.isDiscoverable])
  
  const updateLocal = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setLocalSettings(prev => ({ ...prev, [key]: value }))
    setHasChanges(true)
  }

  const updateLocalDynamic = (key: keyof Settings, value: Settings[keyof Settings]) => {
    setLocalSettings(prev => ({ ...prev, [key]: value }))
    setHasChanges(true)
  }
  
  const handleSave = async () => {
    const savePayload: Partial<Settings> = {
      ...localSettings,
      defaultLLMProvider: selectedProvider,
      defaultModel: resolvedDefaultModel,
      organizerModel: String(localSettings.organizerModel || DEFAULT_COMPONENT_MODEL),
      coordinatorModel: String(localSettings.coordinatorModel || DEFAULT_COMPONENT_MODEL),
      workerDefaultModel: String(localSettings.workerDefaultModel || DEFAULT_COMPONENT_MODEL),
      anRouterModel: String(localSettings.anRouterModel || DEFAULT_COMPONENT_MODEL),
      localAgentRouterModel: String(localSettings.localAgentRouterModel || DEFAULT_COMPONENT_MODEL),
      crewaiPlanningLlm: String(localSettings.crewaiPlanningLlm || DEFAULT_SETTINGS.crewaiPlanningLlm),
    }
    await settings.saveSettings(savePayload)

    // Sync network settings with networkStore if online
    if (network.status === 'online') {
      if (localSettings.lanDiscoveryEnabled !== undefined) {
        if (localSettings.lanDiscoveryEnabled && !network.isDiscovering) {
          await network.startDiscovery()
        } else if (!localSettings.lanDiscoveryEnabled && network.isDiscovering) {
          await network.stopDiscovery()
        }
      }
      if (localSettings.lanDiscoverable !== undefined && localSettings.lanDiscoverable !== network.isDiscoverable) {
        await network.setDiscoverable(localSettings.lanDiscoverable)
      }
    }

    // Save framework backend if changed
    if (frameworkBackend !== frameworkBackendInitial) {
      await fetch(`${getApiBase()}/api/framework`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backend: frameworkBackend }),
      }).catch((e) => console.warn('Failed to save framework backend:', e))
      setFrameworkBackendInitial(frameworkBackend)
    }

    // Save channel configs if changed
    for (const channel of ['telegram', 'slack', 'discord'] as const) {
      const cur = channelConfigs[channel]
      const init = channelConfigsInitial[channel]
      const changed = cur.enabled !== init.enabled
        || cur.token !== init.token
        || cur.bot_token !== init.bot_token
        || cur.app_token !== init.app_token
      if (changed) {
        await fetch(`${getApiBase()}/api/channels/config`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel, ...cur }),
        }).catch((e) => console.warn('Failed to save channel configs:', e))
      }
    }
    setChannelConfigsInitial(channelConfigs)

    await loadAgentsFromDB()

    setHasChanges(false)
    onClose()
  }
  
  const handleReset = async () => {
    await settings.resetToDefaults()
    setLocalSettings({ ...DEFAULT_SETTINGS })
    setShowResetConfirm(false)
    setHasChanges(false)
  }

  const handleClearAllData = async () => {
    if (clearConfirmText !== 'DELETE ALL DATA') return
    setClearingData(true)
    try {
      const response = await fetch(`${getApiBase()}/api/db/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: 'DELETE ALL DATA' }),
      })
      if (!response.ok) {
        throw new Error(`Failed to clear all data: HTTP ${response.status}`)
      }

      useWalletStore.getState().reset()
      useDataStore.getState().reset()
      useAgentStore.setState({
        tasks: [],
        selectedTaskId: null,
        logs: [],
        selectedAgentId: null,
      })
      useChatStore.setState({
        sessions: [],
        activeSessionId: null,
        isStreaming: false,
        currentTaskId: null,
        totalUnreadCount: 0,
      })
      useNetworkStore.setState({
        status: 'offline',
        lastStatusChange: null,
        connectedSince: null,
        nodeId: null,
        nodeName: 'Local Node',
        peerCount: 0,
        isDiscovering: false,
        isDiscoverable: true,
        lanNodes: [],
        wanNodes: [],
        inboundPeers: [],
        connectionHistory: [],
        connectionSessions: [],
        isListedOnMarketplace: false,
        marketplaceListing: null,
      })

      for (const key of ['teaming24-chat-storage', 'teaming24-wallet', 'teaming24-network']) {
        window.localStorage.removeItem(key)
      }

      setShowClearAllData(false)
      setClearConfirmText('')
      window.location.reload()
    } catch (err) {
      console.error('Failed to clear all data:', err)
    }
    setClearingData(false)
  }

  const tabs = [
    { id: 'general' as SettingsTab, label: 'General', icon: ComputerDesktopIcon },
    { id: 'node' as SettingsTab, label: 'Local Node', icon: CpuChipIcon },
    { id: 'network' as SettingsTab, label: 'Network', icon: GlobeAltIcon },
    { id: 'llm' as SettingsTab, label: 'LLM', icon: CpuChipIcon },
    { id: 'agent' as SettingsTab, label: 'Agent', icon: CpuChipIcon },
    { id: 'channels' as SettingsTab, label: 'Channels', icon: ChatBubbleLeftRightIcon },
    { id: 'integrations' as SettingsTab, label: 'Integrations', icon: PuzzlePieceIcon },
    { id: 'notifications' as SettingsTab, label: 'Notifications', icon: BellIcon },
    { id: 'advanced' as SettingsTab, label: 'Advanced', icon: WrenchScrewdriverIcon },
  ]

  return (
    <Transition appear show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/50" />
        </Transition.Child>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel className="w-full max-w-3xl transform overflow-hidden rounded-xl bg-dark-surface border border-dark-border shadow-xl transition-all">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-dark-border">
                  <div className="flex items-center gap-3">
                    <Cog6ToothIcon className="w-6 h-6 text-primary-400" />
                    <Dialog.Title className="text-lg font-semibold text-white">
                      Settings
                    </Dialog.Title>
                  </div>
                  <button
                    onClick={onClose}
                    className="p-2 hover:bg-dark-hover rounded-lg transition-colors"
                  >
                    <XMarkIcon className="w-5 h-5 text-gray-400" />
                  </button>
                </div>

                <div className="flex h-[500px]">
                  {/* Sidebar */}
                  <div className="w-48 border-r border-dark-border p-2">
                    {tabs.map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`
                          w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors
                          ${activeTab === tab.id 
                            ? 'bg-primary-500/20 text-primary-400' 
                            : 'text-gray-400 hover:bg-dark-hover hover:text-gray-200'}
                        `}
                      >
                        <tab.icon className="w-4 h-4" />
                        {tab.label}
                      </button>
                    ))}
                  </div>

                  {/* Content */}
                  <div className="flex-1 p-6 overflow-y-auto">
                    {/* General Tab */}
                    {activeTab === 'general' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Appearance</h3>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Compact Mode</p>
                                <p className="text-xs text-gray-500">Use smaller UI elements</p>
                              </div>
                              <Switch
                                checked={localSettings.compactMode || false}
                                onChange={(v) => updateLocal('compactMode', v)}
                                className={`${localSettings.compactMode ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.compactMode ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Local Node Tab */}
                    {activeTab === 'node' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Local Node Identity</h3>
                          
                          <div className="space-y-4">
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Node Name</label>
                              <input
                                type="text"
                                value={localSettings.localNodeName || ''}
                                onChange={(e) => updateLocal('localNodeName', e.target.value)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                                placeholder="Local Agentic Node"
                              />
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Description</label>
                              <textarea
                                value={localSettings.localNodeDescription || ''}
                                onChange={(e) => updateLocal('localNodeDescription', e.target.value)}
                                rows={2}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500 resize-none"
                                placeholder="Describe your node..."
                              />
                            </div>
                            
                            <div className="grid grid-cols-2 gap-4">
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">Capability</label>
                                <input
                                  type="text"
                                  value={localSettings.localNodeCapability || ''}
                                  onChange={(e) => updateLocal('localNodeCapability', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                           focus:outline-none focus:border-primary-500"
                                  placeholder="General Purpose"
                                />
                              </div>
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">Region</label>
                                <input
                                  type="text"
                                  value={localSettings.localNodeRegion || ''}
                                  onChange={(e) => updateLocal('localNodeRegion', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                           focus:outline-none focus:border-primary-500"
                                  placeholder="Local"
                                />
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Network Tab */}
                    {activeTab === 'network' && (
                      <div className="space-y-6">
                        {/* AgentaNet Central Service */}
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">AgentaNet Central Service</h3>
                          <p className="text-xs text-gray-500 mb-4">
                            Create token in Central first, then bind URL + token here. Only after binding can this node be listed globally.
                          </p>
                          
                          <div className="space-y-4">
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Central Service URL</label>
                              <input
                                type="text"
                                value={localSettings.agentanetCentralUrl || ''}
                                onChange={(e) => updateLocal('agentanetCentralUrl', e.target.value)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                                placeholder="http://100.64.1.3:8080"
                              />
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">AgentaNet Token</label>
                              <input
                                type="password"
                                value={localSettings.agentanetToken || ''}
                                onChange={(e) => updateLocal('agentanetToken', e.target.value)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500 font-mono"
                                placeholder="agn_xxxxxxxxxxxxxxxx"
                              />
                              <p className="text-xs text-gray-500 mt-1">
                                Get your token from the AgentaNet Central dashboard
                              </p>
                            </div>
                          </div>
                        </div>
                        
                        <div className="border-t border-dark-border pt-6">
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Network Settings</h3>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Auto-connect on startup</p>
                                <p className="text-xs text-gray-500">Automatically go online when app starts</p>
                              </div>
                              <Switch
                                checked={localSettings.autoConnectOnStartup || false}
                                onChange={(v) => updateLocal('autoConnectOnStartup', v)}
                                className={`${localSettings.autoConnectOnStartup ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.autoConnectOnStartup ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Auto-join Agentic Node Marketplace</p>
                                <p className="text-xs text-gray-500">Automatically list on the Agentic Node Marketplace when online</p>
                              </div>
                              <Switch
                                checked={localSettings.autoJoinMarketplace || false}
                                onChange={(v) => updateLocal('autoJoinMarketplace', v)}
                                className={`${localSettings.autoJoinMarketplace ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.autoJoinMarketplace ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">LAN Visible</p>
                                <p className="text-xs text-gray-500">Others can find you when they scan LAN</p>
                              </div>
                              <Switch
                                checked={localSettings.lanDiscoverable || false}
                                onChange={(v) => updateLocal('lanDiscoverable', v)}
                                className={`${localSettings.lanDiscoverable ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.lanDiscoverable ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>

                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">LAN Scan</p>
                                <p className="text-xs text-gray-500">Scan local network to find other visible nodes</p>
                              </div>
                              <Switch
                                checked={localSettings.lanDiscoveryEnabled || false}
                                onChange={(v) => updateLocal('lanDiscoveryEnabled', v)}
                                className={`${localSettings.lanDiscoveryEnabled ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.lanDiscoveryEnabled ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* LLM Tab */}
                    {activeTab === 'llm' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">LLM Configuration</h3>

                          <div className="space-y-4">
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Default Provider</label>
                              <select
                                value={selectedProvider}
                                onChange={(e) => handleProviderChange(e.target.value as ProviderKey)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                              >
                                <option value="openai">{PROVIDER_LABELS.openai}</option>
                                <option value="anthropic">{PROVIDER_LABELS.anthropic}</option>
                                <option value="flock">{PROVIDER_LABELS.flock}</option>
                                <option value="local">{PROVIDER_LABELS.local}</option>
                              </select>
                            </div>

                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Default Model</label>
                              {selectedProvider === 'local' ? (
                                <input
                                  type="text"
                                  value={localSettings.localCustomModel || ''}
                                  onChange={(e) => setLocalCustomModel(e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                           focus:outline-none focus:border-primary-500"
                                  placeholder="llama3.1"
                                />
                              ) : (
                                <select
                                  value={resolvedDefaultModel}
                                  onChange={(e) => updateLocal('defaultModel', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                           focus:outline-none focus:border-primary-500"
                                >
                                  {providerModels.map((modelName) => (
                                    <option key={modelName} value={modelName}>{MODEL_LABELS[modelName] || modelName}</option>
                                  ))}
                                </select>
                              )}
                              <p className="text-xs text-gray-500 mt-1">
                                Runtime resolves this as <code>{`${selectedProvider}/${resolvedDefaultModel}`}</code>.
                              </p>
                            </div>

                            <div className="grid grid-cols-1 gap-3">
                              {(Object.keys(PROVIDER_SETTING_KEYS) as ProviderKey[]).map((providerKey) => {
                                const settingKeys = PROVIDER_SETTING_KEYS[providerKey]
                                const apiKeyValue = String(localSettings[settingKeys.apiKey] || '')
                                const baseUrlValue = String(localSettings[settingKeys.baseUrl] || '')
                                return (
                                  <div key={providerKey} className="border border-dark-border rounded-lg p-3 space-y-2">
                                    <p className="text-sm text-gray-200">{PROVIDER_LABELS[providerKey]}</p>
                                    <input
                                      type="password"
                                      value={apiKeyValue}
                                      onChange={(e) => updateLocalDynamic(settingKeys.apiKey, e.target.value)}
                                      className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                               focus:outline-none focus:border-primary-500 font-mono text-sm"
                                      placeholder={`${PROVIDER_LABELS[providerKey]} API Key`}
                                    />
                                    <input
                                      type="text"
                                      value={baseUrlValue}
                                      onChange={(e) => updateLocalDynamic(settingKeys.baseUrl, e.target.value)}
                                      className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                               focus:outline-none focus:border-primary-500 font-mono text-sm"
                                      placeholder={`${PROVIDER_LABELS[providerKey]} Base URL`}
                                    />
                                  </div>
                                )
                              })}
                            </div>

                            <div>
                              <label className="block text-sm text-gray-400 mb-1">
                                Temperature: {localSettings.temperature != null ? formatNumberNoTrailingZeros(localSettings.temperature, 1) : '0.7'}
                              </label>
                              <input
                                type="range"
                                min="0"
                                max="2"
                                step="0.1"
                                value={localSettings.temperature || 0.7}
                                onChange={(e) => updateLocal('temperature', parseFloat(e.target.value))}
                                className="w-full"
                              />
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Max Tokens</label>
                              <input
                                type="number"
                                value={localSettings.maxTokens || 4096}
                                onChange={(e) => updateLocal('maxTokens', parseInt(e.target.value))}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Agent Tab */}
                    {activeTab === 'agent' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">CrewAI Settings</h3>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Verbose Mode</p>
                                <p className="text-xs text-gray-500">Show detailed agent execution logs for tracking</p>
                              </div>
                              <Switch
                                checked={localSettings.crewaiVerbose || false}
                                onChange={(v) => updateLocal('crewaiVerbose', v)}
                                className={`${localSettings.crewaiVerbose ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.crewaiVerbose ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Process Type</label>
                              <select
                                value={localSettings.crewaiProcess || 'hierarchical'}
                                onChange={(e) => updateLocal('crewaiProcess', e.target.value as 'sequential' | 'hierarchical')}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                              >
                                <option value="sequential">Sequential (agents work in order)</option>
                                <option value="hierarchical">Hierarchical (manager delegates to workers)</option>
                              </select>
                              <p className="text-xs text-gray-500 mt-1">
                                Hierarchical enables Organizer → Coordinator → Worker delegation
                              </p>
                            </div>
                            
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Agent Memory</p>
                                <p className="text-xs text-gray-500">Enable memory across task executions</p>
                              </div>
                              <Switch
                                checked={localSettings.crewaiMemory || false}
                                onChange={(v) => updateLocal('crewaiMemory', v)}
                                className={`${localSettings.crewaiMemory ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.crewaiMemory ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>

                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Durable Local Memory</p>
                                <p className="text-xs text-gray-500">
                                  Use persistent memory for local organizer, local team coordinator, and workers only
                                </p>
                              </div>
                              <Switch
                                checked={localSettings.agentMemoryEnabled ?? true}
                                onChange={(v) => updateLocal('agentMemoryEnabled', v)}
                                className={`${localSettings.agentMemoryEnabled ?? true ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.agentMemoryEnabled ?? true ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>

                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Respect Context Window</p>
                                <p className="text-xs text-gray-500">
                                  Trim rebuilt prompts to the configured context budget before execution
                                </p>
                              </div>
                              <Switch
                                checked={localSettings.respectContextWindow ?? true}
                                onChange={(v) => updateLocal('respectContextWindow', v)}
                                className={`${localSettings.respectContextWindow ?? true ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.respectContextWindow ?? true ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Max Requests/Minute</label>
                              <input
                                type="number"
                                min="1"
                                max="100"
                                value={localSettings.crewaiMaxRpm || 10}
                                onChange={(e) => updateLocal('crewaiMaxRpm', parseInt(e.target.value))}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                              />
                              <p className="text-xs text-gray-500 mt-1">Rate limit for LLM API calls</p>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Agent Scenario</label>
                              <select
                                value={localSettings.agentScenario || 'product_team'}
                                onChange={(e) => updateLocal('agentScenario', e.target.value)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                              >
                                <option value="product_team">Product Team (PM, Dev, ML, QA)</option>
                                <option value="research_team">Research Team</option>
                                <option value="custom">Custom</option>
                              </select>
                              <p className="text-xs text-gray-500 mt-1">Worker agent configuration</p>
                            </div>

                            <div className="border border-dark-border rounded-lg p-4 space-y-3">
                              <p className="text-sm text-gray-200">Role and Component Model Overrides</p>
                              <p className="text-xs text-gray-500">
                                Configure different models per role. Use provider prefixes like
                                <code className="mx-1">openai/</code>,
                                <code className="mx-1">anthropic/</code>,
                                <code className="mx-1">flock/</code>,
                                <code className="mx-1">local/</code>, or
                                <code className="ml-1">ollama/</code>.
                              </p>
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">Organizer Model</label>
                                <select
                                  value={String(localSettings.organizerModel || DEFAULT_COMPONENT_MODEL)}
                                  onChange={(e) => updateLocal('organizerModel', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                                >
                                  {allProviderModels.map((modelName) => (
                                    <option key={`organizer-${modelName}`} value={modelName}>{modelName}</option>
                                  ))}
                                </select>
                              </div>
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">Coordinator Model</label>
                                <select
                                  value={String(localSettings.coordinatorModel || DEFAULT_COMPONENT_MODEL)}
                                  onChange={(e) => updateLocal('coordinatorModel', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                                >
                                  {allProviderModels.map((modelName) => (
                                    <option key={`coordinator-${modelName}`} value={modelName}>{modelName}</option>
                                  ))}
                                </select>
                              </div>
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">Worker Default Model</label>
                                <select
                                  value={String(localSettings.workerDefaultModel || DEFAULT_COMPONENT_MODEL)}
                                  onChange={(e) => updateLocal('workerDefaultModel', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                                >
                                  {allProviderModels.map((modelName) => (
                                    <option key={`worker-${modelName}`} value={modelName}>{modelName}</option>
                                  ))}
                                </select>
                              </div>
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">AN Router Model</label>
                                <select
                                  value={String(localSettings.anRouterModel || DEFAULT_COMPONENT_MODEL)}
                                  onChange={(e) => updateLocal('anRouterModel', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                                >
                                  {allProviderModels.map((modelName) => (
                                    <option key={`an-router-${modelName}`} value={modelName}>{modelName}</option>
                                  ))}
                                </select>
                              </div>
                              <div>
                                <label className="block text-sm text-gray-400 mb-1">Local Agent Router Model</label>
                                <select
                                  value={String(localSettings.localAgentRouterModel || DEFAULT_COMPONENT_MODEL)}
                                  onChange={(e) => updateLocal('localAgentRouterModel', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                                >
                                  {allProviderModels.map((modelName) => (
                                    <option key={`local-agent-router-${modelName}`} value={modelName}>{modelName}</option>
                                  ))}
                                </select>
                              </div>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Task Execution Timeout (seconds)</label>
                              <input
                                type="number"
                                min="0"
                                value={localSettings.taskExecutionTimeout ?? 0}
                                onChange={(e) => updateLocal('taskExecutionTimeout', parseInt(e.target.value) || 0)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200
                                         focus:outline-none focus:border-primary-500"
                              />
                              <p className="text-xs text-gray-500 mt-1">Idle timeout (s): task fails if no step/keepalive for this long. 0 = disabled (keep waiting). Timer resets on each step or keepalive.</p>
                            </div>
                          </div>
                        </div>
                        
                        {/* Planning & Reasoning Section */}
                        <div className="border-t border-dark-border pt-6">
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Planning & Reasoning</h3>
                          <p className="text-xs text-gray-500 mb-4">
                            Advanced CrewAI features for better task execution
                          </p>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Planning Mode</p>
                                <p className="text-xs text-gray-500">Creates step-by-step plan before execution</p>
                              </div>
                              <Switch
                                checked={localSettings.crewaiPlanning || false}
                                onChange={(v) => updateLocal('crewaiPlanning', v)}
                                className={`${localSettings.crewaiPlanning ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.crewaiPlanning ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            {localSettings.crewaiPlanning && (
                              <div className="ml-4">
                                <label className="block text-sm text-gray-400 mb-1">Planning LLM</label>
                                <select
                                  value={String(localSettings.crewaiPlanningLlm || DEFAULT_SETTINGS.crewaiPlanningLlm)}
                                  onChange={(e) => updateLocal('crewaiPlanningLlm', e.target.value)}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                           focus:outline-none focus:border-primary-500"
                                >
                                  {allProviderModels.map((modelName) => (
                                    <option key={`planning-${modelName}`} value={modelName}>{modelName}</option>
                                  ))}
                                </select>
                                <p className="text-xs text-gray-500 mt-1">Model used for planning (same provider/model format as other component overrides)</p>
                              </div>
                            )}
                            
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Reasoning Mode</p>
                                <p className="text-xs text-gray-500">Agents reflect and create plan before executing</p>
                              </div>
                              <Switch
                                checked={localSettings.crewaiReasoning || false}
                                onChange={(v) => updateLocal('crewaiReasoning', v)}
                                className={`${localSettings.crewaiReasoning ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.crewaiReasoning ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            {localSettings.crewaiReasoning && (
                              <div className="ml-4">
                                <label className="block text-sm text-gray-400 mb-1">Max Reasoning Attempts</label>
                                <input
                                  type="number"
                                  min="1"
                                  max="10"
                                  value={localSettings.crewaiMaxReasoningAttempts || 3}
                                  onChange={(e) => updateLocal('crewaiMaxReasoningAttempts', parseInt(e.target.value))}
                                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                           focus:outline-none focus:border-primary-500"
                                />
                                <p className="text-xs text-gray-500 mt-1">Number of reasoning iterations per task</p>
                              </div>
                            )}
                            
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Real-time Streaming</p>
                                <p className="text-xs text-gray-500">Stream thinking process via event listeners</p>
                              </div>
                              <Switch
                                checked={localSettings.crewaiStreaming || false}
                                onChange={(v) => updateLocal('crewaiStreaming', v)}
                                className={`${localSettings.crewaiStreaming ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.crewaiStreaming ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                          </div>
                        </div>
                        
                        <div className="border-t border-dark-border pt-6">
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Task Output</h3>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Auto-save Output</p>
                                <p className="text-xs text-gray-500">Save task outputs to organized folders</p>
                              </div>
                              <Switch
                                checked={localSettings.taskOutputEnabled || false}
                                onChange={(v) => updateLocal('taskOutputEnabled', v)}
                                className={`${localSettings.taskOutputEnabled ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.taskOutputEnabled ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Output Directory</label>
                              <input
                                type="text"
                                value={localSettings.taskOutputDir || '~/.teaming24/outputs'}
                                onChange={(e) => updateLocal('taskOutputDir', e.target.value)}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500 font-mono text-sm"
                              />
                              <p className="text-xs text-gray-500 mt-1">Where task outputs are saved</p>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Channels Tab */}
                    {activeTab === 'channels' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-2">Multi-Channel Messaging</h3>
                          <p className="text-xs text-gray-500 mb-4">
                            Connect messaging platforms so agents can receive tasks from Telegram, Slack, or Discord.
                          </p>
                        </div>

                        {/* Telegram */}
                        <div className="p-4 bg-dark-bg border border-dark-border rounded-lg">
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center gap-2">
                              <span className="text-lg">✈</span>
                              <span className="text-sm font-medium text-blue-400">Telegram</span>
                            </div>
                            <Switch
                              checked={channelConfigs.telegram.enabled}
                              onChange={(v) => updateChannelConfig('telegram', { enabled: v })}
                              className={`${channelConfigs.telegram.enabled ? 'bg-primary-600' : 'bg-gray-700'}
                                relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                            >
                              <span className={`${channelConfigs.telegram.enabled ? 'translate-x-6' : 'translate-x-1'}
                                inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                              />
                            </Switch>
                          </div>
                          <input
                            type="password"
                            value={channelConfigs.telegram.bot_token}
                            onChange={e => updateChannelConfig('telegram', { bot_token: e.target.value })}
                            placeholder="Bot Token (from @BotFather)"
                            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm font-mono focus:outline-none focus:border-primary-500"
                          />
                        </div>

                        {/* Slack */}
                        <div className="p-4 bg-dark-bg border border-dark-border rounded-lg">
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center gap-2">
                              <span className="text-lg font-bold text-green-400">#</span>
                              <span className="text-sm font-medium text-green-400">Slack</span>
                            </div>
                            <Switch
                              checked={channelConfigs.slack.enabled}
                              onChange={(v) => updateChannelConfig('slack', { enabled: v })}
                              className={`${channelConfigs.slack.enabled ? 'bg-primary-600' : 'bg-gray-700'}
                                relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                            >
                              <span className={`${channelConfigs.slack.enabled ? 'translate-x-6' : 'translate-x-1'}
                                inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                              />
                            </Switch>
                          </div>
                          <div className="space-y-2">
                            <input
                              type="password"
                              value={channelConfigs.slack.app_token}
                              onChange={e => updateChannelConfig('slack', { app_token: e.target.value })}
                              placeholder="App Token (xapp-...)"
                              className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm font-mono focus:outline-none focus:border-primary-500"
                            />
                            <input
                              type="password"
                              value={channelConfigs.slack.bot_token}
                              onChange={e => updateChannelConfig('slack', { bot_token: e.target.value })}
                              placeholder="Bot Token (xoxb-...)"
                              className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm font-mono focus:outline-none focus:border-primary-500"
                            />
                          </div>
                        </div>

                        {/* Discord */}
                        <div className="p-4 bg-dark-bg border border-dark-border rounded-lg">
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center gap-2">
                              <span className="text-lg">🎮</span>
                              <span className="text-sm font-medium text-indigo-400">Discord</span>
                            </div>
                            <Switch
                              checked={channelConfigs.discord.enabled}
                              onChange={(v) => updateChannelConfig('discord', { enabled: v })}
                              className={`${channelConfigs.discord.enabled ? 'bg-primary-600' : 'bg-gray-700'}
                                relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                            >
                              <span className={`${channelConfigs.discord.enabled ? 'translate-x-6' : 'translate-x-1'}
                                inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                              />
                            </Switch>
                          </div>
                          <input
                            type="password"
                            value={channelConfigs.discord.token}
                            onChange={e => updateChannelConfig('discord', { token: e.target.value })}
                            placeholder="Bot Token"
                            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm font-mono focus:outline-none focus:border-primary-500"
                          />
                        </div>
                      </div>
                    )}

                    {/* Integrations Tab */}
                    {activeTab === 'integrations' && (
                      <div className="space-y-6">
                        {/* Framework Backend */}
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-2">Agent Framework</h3>
                          <p className="text-xs text-gray-500 mb-4">
                            Choose the execution backend for multi-agent tasks. Requires server restart.
                          </p>
                          <div className="grid grid-cols-2 gap-3">
                            <button
                              onClick={() => updateFrameworkBackend('native')}
                              className={`p-4 rounded-lg border transition-colors text-left ${
                                frameworkBackend === 'native'
                                  ? 'border-primary-500 bg-primary-500/10'
                                  : 'border-dark-border bg-dark-bg hover:border-gray-600'
                              }`}
                            >
                              <span className="text-sm font-medium text-gray-200">Native Runtime</span>
                              <p className="text-xs text-gray-500 mt-1">Teaming24's own engine (litellm)</p>
                            </button>
                            <button
                              onClick={() => updateFrameworkBackend('crewai')}
                              className={`p-4 rounded-lg border transition-colors text-left ${
                                frameworkBackend === 'crewai'
                                  ? 'border-primary-500 bg-primary-500/10'
                                  : 'border-dark-border bg-dark-bg hover:border-gray-600'
                              }`}
                            >
                              <span className="text-sm font-medium text-gray-200">CrewAI</span>
                              <p className="text-xs text-gray-500 mt-1">CrewAI framework integration</p>
                            </button>
                          </div>
                        </div>

                      </div>
                    )}

                    {/* Notifications Tab */}
                    {activeTab === 'notifications' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Notification Preferences</h3>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Enable Notifications</p>
                                <p className="text-xs text-gray-500">Show in-app notifications</p>
                              </div>
                              <Switch
                                checked={localSettings.notificationsEnabled || false}
                                onChange={(v) => updateLocal('notificationsEnabled', v)}
                                className={`${localSettings.notificationsEnabled ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.notificationsEnabled ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Sound</p>
                                <p className="text-xs text-gray-500">Play sound on notifications</p>
                              </div>
                              <Switch
                                checked={localSettings.soundEnabled || false}
                                onChange={(v) => updateLocal('soundEnabled', v)}
                                className={`${localSettings.soundEnabled ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.soundEnabled ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Advanced Tab */}
                    {activeTab === 'advanced' && (
                      <div className="space-y-6">
                        <div>
                          <h3 className="text-sm font-medium text-gray-300 mb-4">Advanced Settings</h3>
                          
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <p className="text-sm text-gray-200">Debug Mode</p>
                                <p className="text-xs text-gray-500">Show debug information</p>
                              </div>
                              <Switch
                                checked={localSettings.debugMode || false}
                                onChange={(v) => updateLocal('debugMode', v)}
                                className={`${localSettings.debugMode ? 'bg-primary-600' : 'bg-gray-700'}
                                  relative inline-flex h-6 w-11 items-center rounded-full transition-colors`}
                              >
                                <span className={`${localSettings.debugMode ? 'translate-x-6' : 'translate-x-1'}
                                  inline-block h-4 w-4 transform rounded-full bg-white transition-transform`}
                                />
                              </Switch>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">Log Level</label>
                              <select
                                value={localSettings.logLevel || 'INFO'}
                                onChange={(e) => updateLocal('logLevel', e.target.value as Settings['logLevel'])}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                              >
                                <option value="DEBUG">Debug</option>
                                <option value="INFO">Info</option>
                                <option value="WARNING">Warning</option>
                                <option value="ERROR">Error</option>
                              </select>
                            </div>
                            
                            <div>
                              <label className="block text-sm text-gray-400 mb-1">
                                Sandbox Timeout (seconds)
                              </label>
                              <input
                                type="number"
                                value={localSettings.sandboxTimeout || 300}
                                onChange={(e) => updateLocal('sandboxTimeout', parseInt(e.target.value))}
                                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 
                                         focus:outline-none focus:border-primary-500"
                              />
                            </div>
                          </div>
                        </div>

                        {/* Danger Zone */}
                        <div className="pt-4 border-t border-dark-border">
                          <h3 className="text-sm font-medium text-red-400 mb-4">Danger Zone</h3>
                          
                          <div className="space-y-3">
                            {/* Reset Settings */}
                          {!showResetConfirm ? (
                            <button
                              onClick={() => setShowResetConfirm(true)}
                                className="flex items-center gap-2 px-4 py-2 bg-red-500/10 text-red-400 border border-red-500/20
                                         rounded-lg hover:bg-red-500/20 transition-colors w-full"
                            >
                              <ArrowPathIcon className="w-4 h-4" />
                                <div className="text-left">
                                  <span className="text-sm">Reset All Settings</span>
                                  <p className="text-[10px] text-red-400/60">Restore default preferences</p>
                                </div>
                            </button>
                          ) : (
                            <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
                              <div className="flex items-start gap-3">
                                <ExclamationTriangleIcon className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                                <div className="flex-1">
                                    <p className="text-sm text-red-300 font-medium">Reset all settings?</p>
                                    <p className="text-xs text-red-400/70 mt-1">This restores defaults. Your data is not affected.</p>
                                  <div className="flex gap-2 mt-3">
                                      <button onClick={handleReset}
                                        className="px-3 py-1.5 bg-red-600 text-white rounded text-sm font-medium hover:bg-red-700 transition-colors">
                                      Yes, Reset
                                    </button>
                                      <button onClick={() => setShowResetConfirm(false)}
                                        className="px-3 py-1.5 bg-dark-hover text-gray-300 rounded text-sm font-medium hover:bg-dark-border transition-colors">
                                        Cancel
                                      </button>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )}

                            {/* Clear All Data */}
                            {!showClearAllData ? (
                                    <button
                                onClick={() => setShowClearAllData(true)}
                                className="flex items-center gap-2 px-4 py-2 bg-red-500/10 text-red-400 border border-red-500/20
                                         rounded-lg hover:bg-red-500/20 transition-colors w-full"
                              >
                                <ExclamationTriangleIcon className="w-4 h-4" />
                                <div className="text-left">
                                  <span className="text-sm">Clear All Data</span>
                                  <p className="text-[10px] text-red-400/60">Delete all tasks, agents, skills, chat history, and settings</p>
                                </div>
                              </button>
                            ) : (
                              <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
                                <div className="flex items-start gap-3">
                                  <ExclamationTriangleIcon className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                                  <div className="flex-1">
                                    <p className="text-sm text-red-300 font-medium">Delete all persisted data?</p>
                                    <p className="text-xs text-red-400/70 mt-1">
                                      This will permanently delete all tasks, agents, skills, tools, chat history, and settings.
                                      This action cannot be undone. The page will reload after clearing.
                                    </p>
                                    <div className="mt-3">
                                      <label className="block text-xs text-red-400/80 mb-1.5">
                                        Type <span className="font-mono font-bold text-red-300">DELETE ALL DATA</span> to confirm:
                                      </label>
                                      <input
                                        type="text"
                                        value={clearConfirmText}
                                        onChange={e => setClearConfirmText(e.target.value)}
                                        placeholder="DELETE ALL DATA"
                                        className="w-full px-3 py-2 bg-dark-bg border border-red-500/30 rounded-lg text-gray-200 text-sm font-mono
                                                 focus:outline-none focus:border-red-500 placeholder-gray-600"
                                        autoComplete="off"
                                        spellCheck={false}
                                      />
                                    </div>
                                    <div className="flex gap-2 mt-3">
                                      <button onClick={handleClearAllData}
                                        disabled={clearConfirmText !== 'DELETE ALL DATA' || clearingData}
                                        className="px-3 py-1.5 bg-red-600 text-white rounded text-sm font-medium hover:bg-red-700
                                                 transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
                                        {clearingData ? 'Clearing...' : 'Permanently Delete Everything'}
                                      </button>
                                      <button onClick={() => { setShowClearAllData(false); setClearConfirmText('') }}
                                        className="px-3 py-1.5 bg-dark-hover text-gray-300 rounded text-sm font-medium hover:bg-dark-border transition-colors">
                                      Cancel
                                    </button>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Footer */}
                <div className="flex items-center justify-between px-6 py-4 border-t border-dark-border bg-dark-bg/50">
                  <p className="text-xs text-gray-500">
                    {hasChanges ? 'Unsaved changes' : 'All changes saved'}
                  </p>
                  <div className="flex gap-3">
                    <button
                      onClick={onClose}
                      className="px-4 py-2 text-gray-400 hover:text-gray-200 transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleSave}
                      disabled={!hasChanges || settings.isSaving}
                      className="px-4 py-2 bg-primary-600 text-white rounded-lg font-medium
                               hover:bg-primary-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed
                               flex items-center gap-2"
                    >
                      {settings.isSaving && <ArrowPathIcon className="w-4 h-4 animate-spin" />}
                      Save Changes
                    </button>
                  </div>
                </div>
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition>
  )
}
