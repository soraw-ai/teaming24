import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  AcademicCapIcon,
  PlusIcon,
  PencilIcon,
  TrashIcon,
  TagIcon,
  WrenchScrewdriverIcon,
  MagnifyingGlassIcon,
  SparklesIcon,
  DocumentDuplicateIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useSkillStore, type Skill } from '../../store/skillStore'
import SkillEditorDialog from './SkillEditorDialog'

const categoryColors: Record<string, string> = {
  general: 'bg-gray-500/20 text-gray-400',
  coding: 'bg-blue-500/20 text-blue-400',
  automation: 'bg-green-500/20 text-green-400',
  research: 'bg-purple-500/20 text-purple-400',
  data: 'bg-orange-500/20 text-orange-400',
  devops: 'bg-cyan-500/20 text-cyan-400',
}

const sourceLabels: Record<string, string> = {
  bundled: 'Built-in',
  managed: 'Managed',
  workspace: 'Workspace',
  user: 'Custom',
}

const sourcePriority: Record<string, number> = { user: 0, workspace: 1, managed: 2, bundled: 3 }

export default function SkillsPanel() {
  const { skills, loading, loadSkills, deleteSkill, createSkill } = useSkillStore()
  const [showEditor, setShowEditor] = useState(false)
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null)
  const [viewingSkill, setViewingSkill] = useState<Skill | null>(null)
  const [search, setSearch] = useState('')
  const [filterCategory, setFilterCategory] = useState<string>('')
  const [showQuickAdd, setShowQuickAdd] = useState(false)
  const [quickName, setQuickName] = useState('')
  const [quickDesc, setQuickDesc] = useState('')
  const [quickSaving, setQuickSaving] = useState(false)

  useEffect(() => { loadSkills() }, [loadSkills])

  const filtered = skills.filter(s => {
    if (search) {
      const q = search.toLowerCase()
      if (!s.name.toLowerCase().includes(q) && !s.description.toLowerCase().includes(q) &&
          !s.tags.some(t => t.toLowerCase().includes(q))) {
        return false
      }
    }
    if (filterCategory && s.category !== filterCategory) return false
    return true
  })

  const sortedFiltered = [...filtered].sort((a, b) => {
    const pa = sourcePriority[a.source] ?? 99
    const pb = sourcePriority[b.source] ?? 99
    if (pa !== pb) return pa - pb
    return a.name.localeCompare(b.name)
  })

  const categories = Array.from(new Set(skills.map(s => s.category))).sort()
  const userSkills = skills.filter(s => s.source === 'user')
  const builtinSkills = skills.filter(s => s.source !== 'user')

  const handleDelete = async (id: string, name: string) => {
    if (confirm(`Delete skill "${name}"? This will also remove it from all agents.`)) {
      await deleteSkill(id)
    }
  }

  const handleQuickAdd = async () => {
    if (!quickName.trim()) return
    setQuickSaving(true)
    const id = await createSkill({
      name: quickName.trim(),
      description: quickDesc.trim(),
      category: 'general',
      tags: [],
      enabled: true,
    })
    setQuickSaving(false)
    if (id) {
      setQuickName('')
      setQuickDesc('')
      setShowQuickAdd(false)
      const freshSkills = useSkillStore.getState().skills
      const created = freshSkills.find(s => s.id === id) || null
      if (created) {
        setEditingSkill(created)
        setShowEditor(true)
      }
    }
  }

  const handleDuplicate = async (skill: Skill) => {
    await createSkill({
      name: `${skill.name} (copy)`,
      description: skill.description,
      instructions: skill.instructions,
      category: skill.category,
      tags: [...skill.tags],
      author: skill.author,
      version: skill.version,
      requires: skill.requires,
      enabled: true,
    })
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AcademicCapIcon className="w-5 h-5 text-yellow-400" />
          <h3 className="text-base font-semibold text-white">Skills</h3>
          <div className="flex items-center gap-1.5">
            {userSkills.length > 0 && (
              <span className="text-xs text-yellow-400 bg-yellow-500/10 px-2 py-0.5 rounded-full">
                {userSkills.length} custom
              </span>
            )}
            {builtinSkills.length > 0 && (
              <span className="text-xs text-gray-500 bg-dark-bg px-2 py-0.5 rounded-full">
                {builtinSkills.length} built-in
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowQuickAdd(!showQuickAdd)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors text-xs"
            title="Quick add"
          >
            <SparklesIcon className="w-3.5 h-3.5" />
            Quick Add
          </button>
          <button
            onClick={() => { setEditingSkill(null); setShowEditor(true) }}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-yellow-600 hover:bg-yellow-700 text-white text-sm rounded-lg transition-colors"
          >
            <PlusIcon className="w-4 h-4" />
            New Skill
          </button>
        </div>
      </div>

      {/* Quick Add inline form */}
      {showQuickAdd && (
        <div className="p-3 bg-yellow-500/5 border border-yellow-500/20 rounded-xl space-y-2">
          <p className="text-xs text-yellow-400/80">Quickly create a skill — you can add instructions later.</p>
          <div className="flex gap-2">
            <input type="text" value={quickName} onChange={e => setQuickName(e.target.value)}
              placeholder="Skill name (e.g. code-review)"
              onKeyDown={e => {
                if (e.key === 'Enter') handleQuickAdd()
                if (e.key === 'Escape') { setShowQuickAdd(false); setQuickName(''); setQuickDesc('') }
              }}
              className="flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm placeholder-gray-500 focus:outline-none focus:border-yellow-500"
              autoFocus />
            <input type="text" value={quickDesc} onChange={e => setQuickDesc(e.target.value)}
              placeholder="Brief description (optional)"
              onKeyDown={e => {
                if (e.key === 'Enter') handleQuickAdd()
                if (e.key === 'Escape') { setShowQuickAdd(false); setQuickName(''); setQuickDesc('') }
              }}
              className="flex-1 px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm placeholder-gray-500 focus:outline-none focus:border-yellow-500" />
            <button onClick={() => { setShowQuickAdd(false); setQuickName(''); setQuickDesc('') }}
              className="px-3 py-2 text-gray-400 hover:text-gray-200 text-sm rounded-lg transition-colors hover:bg-dark-hover">
              Cancel
            </button>
            <button onClick={handleQuickAdd} disabled={!quickName.trim() || quickSaving}
              className="px-4 py-2 bg-yellow-600 hover:bg-yellow-700 disabled:bg-gray-700 text-white text-sm rounded-lg transition-colors disabled:cursor-not-allowed">
              {quickSaving ? '...' : 'Create'}
            </button>
          </div>
        </div>
      )}

      {/* Search & Category Filter */}
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <MagnifyingGlassIcon className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search skills..."
            className="w-full pl-9 pr-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm placeholder-gray-500 focus:outline-none focus:border-yellow-500"
          />
        </div>
        {categories.length > 1 && (
          <div className="flex gap-1">
            <button onClick={() => setFilterCategory('')}
              className={clsx('px-2.5 py-1.5 rounded-lg text-xs transition-colors',
                !filterCategory ? 'bg-yellow-500/20 text-yellow-400' : 'text-gray-500 hover:bg-dark-hover')}>
              All
            </button>
            {categories.map(c => (
              <button key={c} onClick={() => setFilterCategory(filterCategory === c ? '' : c)}
                className={clsx('px-2.5 py-1.5 rounded-lg text-xs transition-colors',
                  filterCategory === c
                    ? (categoryColors[c] || 'bg-gray-500/20 text-gray-400')
                    : 'text-gray-500 hover:bg-dark-hover'
                )}>
                {c.charAt(0).toUpperCase() + c.slice(1)}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Skills List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading skills...</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12">
          <AcademicCapIcon className="w-12 h-12 mx-auto text-gray-600 mb-3" />
          <p className="text-gray-500 text-sm mb-4">
            {search || filterCategory ? 'No matching skills found' : 'No skills yet.'}
          </p>
          {!search && !filterCategory && (
            <div className="space-y-2">
              <button onClick={() => { setEditingSkill(null); setShowEditor(true) }}
                className="inline-flex items-center gap-2 px-4 py-2 bg-yellow-600 hover:bg-yellow-700 text-white text-sm rounded-lg transition-colors">
                <PlusIcon className="w-4 h-4" />
                Create Your First Skill
              </button>
              <p className="text-xs text-gray-600">
                Skills provide agents with domain expertise, workflows, and guidelines.
              </p>
            </div>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
          {sortedFiltered.map(skill => (
            <div
              key={skill.id}
              onClick={() => setViewingSkill(skill)}
              className={clsx(
                'border rounded-lg transition-colors group cursor-pointer',
                skill.enabled
                  ? 'border-dark-border bg-dark-surface hover:border-yellow-500/40'
                  : 'border-dark-border/50 bg-dark-surface/50 opacity-60'
              )}
            >
              <div className="flex items-start gap-3 p-3">
                <div className={clsx(
                  'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5',
                  skill.source === 'user' ? 'bg-yellow-500/15' : 'bg-gray-500/10'
                )}>
                  <AcademicCapIcon className={clsx('w-4 h-4', skill.source === 'user' ? 'text-yellow-400' : 'text-gray-500')} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-white">{skill.name}</span>
                    <span className={clsx('px-1.5 py-0.5 rounded text-[10px] font-medium', categoryColors[skill.category] || categoryColors.general)}>
                      {skill.category}
                    </span>
                    {skill.source && (
                      <span className={clsx(
                        'text-[10px] px-1.5 py-0.5 rounded',
                        skill.source === 'user' ? 'text-yellow-400/70 bg-yellow-500/10' : 'text-gray-600 bg-dark-bg'
                      )}>
                        {sourceLabels[skill.source] || skill.source}
                      </span>
                    )}
                    {!skill.enabled && (
                      <span className="text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">Disabled</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{skill.description}</p>
                  {skill.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {skill.tags.slice(0, 3).map((tag, i) => (
                        <span key={i} className="inline-flex items-center gap-0.5 text-[10px] text-gray-400 bg-dark-bg px-1.5 py-0.5 rounded-full">
                          <TagIcon className="w-2.5 h-2.5" />{tag}
                        </span>
                      ))}
                      {skill.tags.length > 3 && (
                        <span className="text-[10px] text-gray-600">+{skill.tags.length - 3}</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Persistence info */}
      {skills.length > 0 && (
        <p className="text-[10px] text-gray-600 pt-1">
          Custom skills are persisted locally. Assign skills to agents in the Agent Editor.
        </p>
      )}

      {/* Skill Detail Dialog */}
      {viewingSkill && createPortal(
        <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={(e) => { if (e.target === e.currentTarget) setViewingSkill(null) }}>
          <div className="bg-dark-surface border border-dark-border rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[80vh] flex flex-col animate-fade-in">
            {/* Header */}
            <div className="flex items-start justify-between p-4 border-b border-dark-border shrink-0">
              <div className="flex items-start gap-3 min-w-0">
                <div className={clsx(
                  'w-10 h-10 rounded-lg flex items-center justify-center shrink-0',
                  viewingSkill.source === 'user' ? 'bg-yellow-500/15' : 'bg-gray-500/10'
                )}>
                  <AcademicCapIcon className={clsx('w-5 h-5', viewingSkill.source === 'user' ? 'text-yellow-400' : 'text-gray-500')} />
                </div>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-white">{viewingSkill.name}</h3>
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    <span className={clsx('px-1.5 py-0.5 rounded text-[10px] font-medium', categoryColors[viewingSkill.category] || categoryColors.general)}>
                      {viewingSkill.category}
                    </span>
                    {viewingSkill.source && (
                      <span className={clsx(
                        'text-[10px] px-1.5 py-0.5 rounded',
                        viewingSkill.source === 'user' ? 'text-yellow-400/70 bg-yellow-500/10' : 'text-gray-600 bg-dark-bg'
                      )}>
                        {sourceLabels[viewingSkill.source] || viewingSkill.source}
                      </span>
                    )}
                    {!viewingSkill.enabled && (
                      <span className="text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">Disabled</span>
                    )}
                  </div>
                </div>
              </div>
              <button onClick={() => setViewingSkill(null)}
                className="p-1.5 hover:bg-dark-hover rounded-lg transition-colors shrink-0">
                <XMarkIcon className="w-5 h-5 text-gray-400" />
              </button>
            </div>

            {/* Body */}
            <div className="p-4 overflow-y-auto flex-1 space-y-4">
              {viewingSkill.description && (
                <p className="text-sm text-gray-300">{viewingSkill.description}</p>
              )}

              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                <div><span className="text-gray-500">Author:</span> <span className="text-gray-300">{viewingSkill.author || '—'}</span></div>
                <div><span className="text-gray-500">Version:</span> <span className="text-gray-300">{viewingSkill.version}</span></div>
                {viewingSkill.license && (
                  <div><span className="text-gray-500">License:</span> <span className="text-gray-300">{viewingSkill.license}</span></div>
                )}
              </div>

              {viewingSkill.tags.length > 0 && (
                <div>
                  <p className="text-[10px] text-gray-500 mb-1.5 uppercase tracking-wider">Tags</p>
                  <div className="flex flex-wrap gap-1.5">
                    {viewingSkill.tags.map((tag, i) => (
                      <span key={i} className="inline-flex items-center gap-1 text-xs text-gray-400 bg-dark-bg px-2 py-1 rounded-full">
                        <TagIcon className="w-3 h-3" />{tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {(viewingSkill.requires?.tools?.length > 0 || viewingSkill.requires?.env?.length > 0) && (
                <div className="space-y-2">
                  <p className="text-[10px] text-gray-500 uppercase tracking-wider">Requirements</p>
                  {viewingSkill.requires.tools.length > 0 && (
                    <div className="flex items-center gap-2">
                      <WrenchScrewdriverIcon className="w-3.5 h-3.5 text-green-400 shrink-0" />
                      <span className="text-xs text-gray-500">Tools:</span>
                      <div className="flex flex-wrap gap-1">
                        {viewingSkill.requires.tools.map((t, i) => (
                          <span key={i} className="text-xs font-mono text-green-400 bg-green-500/10 px-2 py-0.5 rounded">{t}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {viewingSkill.requires.env.length > 0 && (
                    <div className="flex items-center gap-2">
                      <span className="w-3.5" />
                      <span className="text-xs text-gray-500">Env:</span>
                      <div className="flex flex-wrap gap-1">
                        {viewingSkill.requires.env.map((e, i) => (
                          <span key={i} className="text-xs font-mono text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded">{e}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {viewingSkill.instructions && (
                <div>
                  <p className="text-[10px] text-gray-500 mb-1.5 uppercase tracking-wider">Instructions</p>
                  <pre className="text-xs text-gray-400 bg-dark-bg rounded-lg p-3 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono leading-relaxed">
                    {viewingSkill.instructions}
                  </pre>
                </div>
              )}
            </div>

            {/* Footer actions */}
            <div className="flex items-center gap-2 p-3 border-t border-dark-border shrink-0">
              <button onClick={() => { setEditingSkill(viewingSkill); setShowEditor(true); setViewingSkill(null) }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors">
                <PencilIcon className="w-3.5 h-3.5" /> Edit
              </button>
              <button onClick={() => { handleDuplicate(viewingSkill); setViewingSkill(null) }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-dark-hover rounded-lg transition-colors">
                <DocumentDuplicateIcon className="w-3.5 h-3.5" /> Duplicate
              </button>
              {viewingSkill.source === 'user' && (
                <button onClick={() => { handleDelete(viewingSkill.id, viewingSkill.name); setViewingSkill(null) }}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-400 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors ml-auto">
                  <TrashIcon className="w-3.5 h-3.5" /> Delete
                </button>
              )}
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Editor Dialog */}
      {showEditor && (
        <SkillEditorDialog
          onClose={() => { setShowEditor(false); setEditingSkill(null) }}
          editSkill={editingSkill}
        />
      )}
    </div>
  )
}
