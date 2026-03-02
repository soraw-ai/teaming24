import { useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  WrenchScrewdriverIcon,
  MagnifyingGlassIcon,
  CommandLineIcon,
  GlobeAltIcon,
  CircleStackIcon,
  ArrowPathIcon,
  XMarkIcon,
  InformationCircleIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
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

interface ToolProfiles {
  [profileId: string]: { allow?: string[] }
}

interface ToolGroups {
  [groupId: string]: string[]
}

type ProfileId = 'minimal' | 'coding' | 'networking' | 'full'

const PROFILE_OPTIONS: { id: ProfileId; label: string; description: string }[] = [
  { id: 'minimal', label: 'Minimal', description: 'No tools — lightweight agent' },
  { id: 'coding', label: 'Coding', description: 'Sandbox + Memory tools' },
  { id: 'networking', label: 'Networking', description: 'Sandbox + Network + Memory' },
  { id: 'full', label: 'Full', description: 'All tools enabled' },
]

const sectionMeta: Record<string, { icon: typeof WrenchScrewdriverIcon; color: string }> = {
  sandbox: { icon: CommandLineIcon, color: 'text-green-400' },
  network: { icon: GlobeAltIcon, color: 'text-orange-400' },
  memory: { icon: CircleStackIcon, color: 'text-purple-400' },
}

function expandGroups(entries: string[], groups: ToolGroups): Set<string> {
  const result = new Set<string>()
  for (const entry of entries) {
    if (entry.startsWith('group:') && groups[entry]) {
      groups[entry].forEach(id => result.add(id))
    } else {
      result.add(entry)
    }
  }
  return result
}

export default function ToolsPanel() {
  const [sections, setSections] = useState<ToolSection[]>([])
  const [profiles, setProfiles] = useState<ToolProfiles>({})
  const [groups, setGroups] = useState<ToolGroups>({})
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')

  const [activeProfile, setActiveProfile] = useState<ProfileId>('full')
  const [alsoAllow, setAlsoAllow] = useState<Set<string>>(new Set())
  const [deny, setDeny] = useState<Set<string>>(new Set())

  const [viewingTool, setViewingTool] = useState<ToolDef | null>(null)
  const [viewingSection, setViewingSection] = useState<string>('')

  const apiBase = getApiBase()

  const fetchTools = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/agent/available-tools`)
      if (res.ok) {
        const data = await res.json()
        setSections(data.sections || [])
        setProfiles(data.profiles || {})
        setGroups(data.groups || {})
      }
    } catch (e) { console.warn('ToolsPanel error:', e); }
    setLoading(false)
  }, [apiBase])

  useEffect(() => { fetchTools() }, [fetchTools])

  const allToolIds = sections.flatMap(s => s.tools.map(t => t.id))

  const resolveEnabled = useCallback((toolId: string): { allowed: boolean; baseAllowed: boolean; denied: boolean } => {
    const profileDef = profiles[activeProfile] || profiles['full'] || {}
    const baseAllowed = profileDef.allow === undefined
      ? true
      : expandGroups(profileDef.allow, groups).has(toolId)
    const extraAllowed = alsoAllow.has(toolId)
    const isDenied = deny.has(toolId)
    const allowed = (baseAllowed || extraAllowed) && !isDenied
    return { allowed, baseAllowed, denied: isDenied }
  }, [activeProfile, profiles, groups, alsoAllow, deny, allToolIds])

  const enabledCount = allToolIds.filter(id => resolveEnabled(id).allowed).length

  const toggleTool = (toolId: string) => {
    const { baseAllowed } = resolveEnabled(toolId)
    const currentlyAllowed = resolveEnabled(toolId).allowed

    if (currentlyAllowed) {
      // Disable: add to deny, remove from alsoAllow
      setAlsoAllow(prev => { const n = new Set(prev); n.delete(toolId); return n })
      setDeny(prev => new Set(prev).add(toolId))
    } else {
      // Enable: remove from deny, add to alsoAllow if not base-allowed
      setDeny(prev => { const n = new Set(prev); n.delete(toolId); return n })
      if (!baseAllowed) {
        setAlsoAllow(prev => new Set(prev).add(toolId))
      }
    }
  }

  const toggleAll = (enable: boolean) => {
    if (enable) {
      setDeny(new Set())
      const profileDef = profiles[activeProfile] || {}
      const base = profileDef.allow === undefined
        ? new Set(allToolIds)
        : expandGroups(profileDef.allow, groups)
      const missing = allToolIds.filter(id => !base.has(id))
      setAlsoAllow(new Set(missing))
    } else {
      setAlsoAllow(new Set())
      setDeny(new Set(allToolIds))
    }
  }

  const handleProfileChange = (pid: ProfileId) => {
    setActiveProfile(pid)
    setAlsoAllow(new Set())
    setDeny(new Set())
  }

  const filteredSections = sections.map(sec => ({
    ...sec,
    tools: sec.tools.filter(t => {
      if (!search) return true
      const q = search.toLowerCase()
      return t.label.toLowerCase().includes(q) || t.description.toLowerCase().includes(q)
    }),
  })).filter(sec => sec.tools.length > 0)

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <WrenchScrewdriverIcon className="w-5 h-5 text-green-400" />
          <h3 className="text-base font-semibold text-white">Tool Access</h3>
          <span className="text-xs text-gray-500 bg-dark-bg px-2 py-0.5 rounded-full">
            {enabledCount}/{allToolIds.length} enabled
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => toggleAll(true)}
            className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors">
            Enable All
          </button>
          <button onClick={() => toggleAll(false)}
            className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors">
            Disable All
          </button>
          <button onClick={fetchTools} disabled={loading}
            className="p-1.5 hover:bg-dark-hover rounded-lg transition-colors text-gray-400 hover:text-gray-200 disabled:opacity-50"
            title="Refresh">
            <ArrowPathIcon className={clsx('w-4 h-4', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Profile presets */}
      <div>
        <p className="text-[10px] text-gray-500 mb-2 uppercase tracking-wider">Profile Presets</p>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          {PROFILE_OPTIONS.map(opt => (
            <button key={opt.id}
              onClick={() => handleProfileChange(opt.id)}
              className={clsx(
                'p-3 rounded-lg border text-left transition-all',
                activeProfile === opt.id
                  ? 'border-green-500/50 bg-green-500/10'
                  : 'border-dark-border bg-dark-surface hover:border-dark-hover'
              )}>
              <div className={clsx('text-sm font-medium', activeProfile === opt.id ? 'text-green-400' : 'text-gray-200')}>
                {opt.label}
              </div>
              <div className="text-[10px] text-gray-500 mt-0.5">{opt.description}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Search */}
      <div className="relative">
        <MagnifyingGlassIcon className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
        <input type="text" value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search tools..."
          className="w-full pl-9 pr-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm placeholder-gray-500 focus:outline-none focus:border-green-500" />
      </div>

      {/* Tool sections with toggles */}
      {loading && sections.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <ArrowPathIcon className="w-6 h-6 mx-auto mb-2 animate-spin" />
          Loading tools...
        </div>
      ) : (
        <div className="space-y-5">
          {filteredSections.map(section => {
            const meta = sectionMeta[section.id] || { icon: WrenchScrewdriverIcon, color: 'text-gray-400' }
            const SectionIcon = meta.icon
            const sectionAllEnabled = section.tools.every(t => resolveEnabled(t.id).allowed)
            return (
              <div key={section.id}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <SectionIcon className={clsx('w-4 h-4', meta.color)} />
                    <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{section.label}</span>
                    <span className="text-[10px] text-gray-600">
                      {section.tools.filter(t => resolveEnabled(t.id).allowed).length}/{section.tools.length}
                    </span>
                  </div>
                  <button
                    onClick={() => {
                      const ids = section.tools.map(t => t.id)
                      if (sectionAllEnabled) {
                        ids.forEach(id => {
                          setAlsoAllow(p => { const n = new Set(p); n.delete(id); return n })
                          setDeny(p => new Set(p).add(id))
                        })
                      } else {
                        ids.forEach(id => {
                          setDeny(p => { const n = new Set(p); n.delete(id); return n })
                          const { baseAllowed } = resolveEnabled(id)
                          if (!baseAllowed) setAlsoAllow(p => new Set(p).add(id))
                        })
                      }
                    }}
                    className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors">
                    {sectionAllEnabled ? 'Disable all' : 'Enable all'}
                  </button>
                </div>
                <div className="space-y-1">
                  {section.tools.map(tool => {
                    const { allowed } = resolveEnabled(tool.id)
                    return (
                      <div key={tool.id}
                        className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-dark-border bg-dark-surface hover:border-dark-hover transition-colors group">
                        <button onClick={() => { setViewingTool(tool); setViewingSection(section.id) }}
                          className="flex-1 flex items-center gap-3 text-left min-w-0">
                          <div className="min-w-0 flex-1">
                            <div className="text-sm font-mono font-medium text-gray-200">{tool.label}</div>
                            <div className="text-xs text-gray-500 truncate">{tool.description}</div>
                          </div>
                        </button>
                        {/* Toggle switch */}
                        <button onClick={() => toggleTool(tool.id)}
                          className={clsx(
                            'w-9 h-5 rounded-full transition-colors relative shrink-0',
                            allowed ? 'bg-green-600' : 'bg-gray-600'
                          )}>
                          <span className={clsx(
                            'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                            allowed ? 'left-[18px]' : 'left-0.5'
                          )} />
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Info footer */}
      <p className="text-[10px] text-gray-600 pt-1">
        Tool access is configured per-profile. Overrides (enable/disable) are applied on top of the active profile.
      </p>

      {/* Tool Detail Dialog */}
      {viewingTool && createPortal(
        <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={(e) => { if (e.target === e.currentTarget) setViewingTool(null) }}>
          {(() => {
            const meta = sectionMeta[viewingSection] || { icon: WrenchScrewdriverIcon, color: 'text-gray-400' }
            const SectionIcon = meta.icon
            const { allowed } = resolveEnabled(viewingTool.id)
            return (
              <div className="bg-dark-surface border border-dark-border rounded-xl shadow-2xl w-full max-w-md mx-4 animate-fade-in">
                <div className="flex items-start justify-between p-4 border-b border-dark-border">
                  <div className="flex items-start gap-3 min-w-0">
                    <div className={clsx('w-10 h-10 rounded-lg flex items-center justify-center shrink-0',
                      `${meta.color} bg-${meta.color.split('-')[1]}-500/10`
                    )}>
                      <SectionIcon className="w-5 h-5" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-base font-mono font-semibold text-white">{viewingTool.label}</h3>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={clsx(
                          'text-[10px] px-2 py-0.5 rounded font-medium',
                          allowed ? 'text-green-400 bg-green-500/10' : 'text-gray-500 bg-dark-bg'
                        )}>
                          {allowed ? 'Enabled' : 'Disabled'}
                        </span>
                        <span className="text-[10px] text-gray-600 capitalize">{viewingSection}</span>
                      </div>
                    </div>
                  </div>
                  <button onClick={() => setViewingTool(null)}
                    className="p-1.5 hover:bg-dark-hover rounded-lg transition-colors shrink-0">
                    <XMarkIcon className="w-5 h-5 text-gray-400" />
                  </button>
                </div>

                <div className="p-4 space-y-4">
                  <div>
                    <p className="text-[10px] text-gray-500 mb-1.5 uppercase tracking-wider">Description</p>
                    <p className="text-sm text-gray-300 leading-relaxed">{viewingTool.description}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                    <div><span className="text-gray-500">Category:</span> <span className="text-gray-300 capitalize">{viewingSection}</span></div>
                    <div><span className="text-gray-500">Type:</span> <span className="text-gray-300">Built-in</span></div>
                    <div><span className="text-gray-500">Profile:</span> <span className="text-gray-300 capitalize">{activeProfile}</span></div>
                  </div>
                  <div className="flex items-start gap-2 p-2.5 bg-dark-bg rounded-lg">
                    <InformationCircleIcon className="w-4 h-4 text-gray-500 shrink-0 mt-0.5" />
                    <p className="text-[11px] text-gray-500 leading-relaxed">
                      Tools are assigned to agents through the Agent Editor.
                      Use profiles to quickly configure common tool sets, then fine-tune with per-tool toggles.
                    </p>
                  </div>
                </div>

                <div className="flex items-center justify-between p-3 border-t border-dark-border">
                  <span className="text-xs text-gray-500">Toggle access:</span>
                  <button onClick={() => { toggleTool(viewingTool.id) }}
                    className={clsx(
                      'w-9 h-5 rounded-full transition-colors relative',
                      allowed ? 'bg-green-600' : 'bg-gray-600'
                    )}>
                    <span className={clsx(
                      'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                      allowed ? 'left-[18px]' : 'left-0.5'
                    )} />
                  </button>
                </div>
              </div>
            )
          })()}
        </div>,
        document.body
      )}
    </div>
  )
}
