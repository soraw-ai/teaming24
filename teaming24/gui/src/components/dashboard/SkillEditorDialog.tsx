import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  XMarkIcon,
  PlusIcon,
  TagIcon,
  AcademicCapIcon,
  DocumentTextIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { useSkillStore, type Skill } from '../../store/skillStore'
import { getApiBase } from '../../utils/api'

const DEFAULT_SKILL_TEMPLATE = `# Skill Name

[Describe what this skill does. The agent will follow these instructions when this skill is active.]

## When to Use

- Trigger condition 1 (e.g., user asks to "review code", "debug errors")
- Trigger condition 2
- Trigger condition 3

## Instructions

1. **Step one**: Describe the first action the agent should take.
2. **Step two**: Describe the second action.
3. **Step three**: Describe the third action.
4. **Verify**: Confirm the output meets expectations.

## Examples

- Example usage 1: describe a concrete input and expected output
- Example usage 2: describe another scenario

## Guidelines

- Guideline 1: an important principle to follow
- Guideline 2: a common pitfall to avoid
- Guideline 3: a quality standard to maintain
`

interface SkillEditorDialogProps {
  onClose: () => void
  editSkill?: Skill | null
}

const categoryOptions = [
  { value: 'general', label: 'General', color: 'bg-gray-500/20 text-gray-400' },
  { value: 'coding', label: 'Coding', color: 'bg-blue-500/20 text-blue-400' },
  { value: 'automation', label: 'Automation', color: 'bg-green-500/20 text-green-400' },
  { value: 'research', label: 'Research', color: 'bg-purple-500/20 text-purple-400' },
  { value: 'data', label: 'Data', color: 'bg-orange-500/20 text-orange-400' },
  { value: 'devops', label: 'DevOps', color: 'bg-cyan-500/20 text-cyan-400' },
]

type Tab = 'general' | 'instructions' | 'requirements'

export default function SkillEditorDialog({ onClose, editSkill }: SkillEditorDialogProps) {
  const { createSkill, updateSkill } = useSkillStore()
  const isEditing = !!editSkill

  const [activeTab, setActiveTab] = useState<Tab>('general')
  const [loading, setLoading] = useState(false)

  const [name, setName] = useState('')
  const [nameError, setNameError] = useState('')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState('general')
  const [tags, setTags] = useState<string[]>([])
  const [newTag, setNewTag] = useState('')
  const [author, setAuthor] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [skillLicense, setSkillLicense] = useState('')
  const [instructions, setInstructions] = useState(isEditing ? '' : DEFAULT_SKILL_TEMPLATE)
  const [requiredTools, setRequiredTools] = useState<string[]>([])
  const [newTool, setNewTool] = useState('')
  const [requiredEnv, setRequiredEnv] = useState<string[]>([])
  const [newEnv, setNewEnv] = useState('')
  const [enabled, setEnabled] = useState(true)

  const [availableTools, setAvailableTools] = useState<{ id: string; description: string }[]>([])
  const [showToolSuggestions, setShowToolSuggestions] = useState(false)
  const toolInputRef = useRef<HTMLInputElement>(null)
  const suggestionsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const apiBase = getApiBase()
    fetch(`${apiBase}/api/agent/available-tools`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.sections) {
          const tools = (data.sections as { tools: { id: string; description: string }[] }[])
            .flatMap(s => s.tools)
          setAvailableTools(tools)
        }
      })
      .catch((e) => console.warn('Failed to fetch available tools:', e))
  }, [])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        suggestionsRef.current && !suggestionsRef.current.contains(e.target as Node) &&
        toolInputRef.current && !toolInputRef.current.contains(e.target as Node)
      ) {
        setShowToolSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const validateName = (n: string) => {
    if (!n) { setNameError(''); return }
    if (n.length > 64) { setNameError('Max 64 characters'); return }
    if (/--/.test(n)) { setNameError('No consecutive hyphens'); return }
    if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(n)) {
      setNameError('Lowercase letters, numbers, hyphens only')
      return
    }
    setNameError('')
  }

  const handleNameChange = (v: string) => {
    const normalized = v.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')
    setName(normalized)
    validateName(normalized)
  }

  useEffect(() => {
    if (editSkill) {
      setName(editSkill.name)
      setDescription(editSkill.description)
      setCategory(editSkill.category || 'general')
      setTags(editSkill.tags || [])
      setAuthor(editSkill.author || '')
      setVersion(editSkill.version || '1.0.0')
      setSkillLicense((editSkill as Skill & { license?: string }).license || '')
      setInstructions(editSkill.instructions || DEFAULT_SKILL_TEMPLATE)
      setRequiredTools(editSkill.requires?.tools || [])
      setRequiredEnv(editSkill.requires?.env || [])
      setEnabled(editSkill.enabled !== false)
    }
  }, [editSkill])

  const addTag = () => {
    const t = newTag.trim()
    if (t && !tags.includes(t)) {
      setTags(prev => [...prev, t])
      setNewTag('')
    }
  }

  const filteredToolSuggestions = availableTools.filter(t =>
    !requiredTools.includes(t.id) &&
    (newTool === '' || t.id.toLowerCase().includes(newTool.toLowerCase()))
  )

  const addTool = (toolId?: string) => {
    const t = (toolId || newTool).trim()
    if (t && !requiredTools.includes(t)) {
      setRequiredTools(prev => [...prev, t])
      setNewTool('')
      setShowToolSuggestions(false)
    }
  }

  const addEnv = () => {
    const e = newEnv.trim()
    if (e && !requiredEnv.includes(e)) {
      setRequiredEnv(prev => [...prev, e])
      setNewEnv('')
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || nameError) return
    setLoading(true)
    const data: Partial<Skill> & { license?: string } = {
      name: name.trim(),
      description: description.trim(),
      instructions: instructions.trim(),
      category,
      tags,
      author: author.trim(),
      version: version.trim(),
      license: skillLicense.trim(),
      requires: { tools: requiredTools, env: requiredEnv, bins: [] },
      enabled,
    }
    try {
      if (isEditing && editSkill) {
        await updateSkill(editSkill.id, data)
      } else {
        await createSkill(data)
      }
      onClose()
    } finally {
      setLoading(false)
    }
  }

  const tabs: { id: Tab; label: string; icon: typeof AcademicCapIcon }[] = [
    { id: 'general', label: 'General', icon: AcademicCapIcon },
    { id: 'instructions', label: 'Instructions', icon: DocumentTextIcon },
    { id: 'requirements', label: 'Requirements', icon: WrenchScrewdriverIcon },
  ]

  return createPortal(
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-[99999]">
      <div className="bg-dark-surface border border-dark-border rounded-xl w-full max-w-2xl mx-4 max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-dark-border shrink-0">
          <div className="flex items-center gap-2">
            <AcademicCapIcon className="w-5 h-5 text-yellow-400" />
            <h2 className="text-lg font-semibold text-white">
              {isEditing ? `Edit Skill: ${editSkill?.name}` : 'Create Skill'}
            </h2>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-dark-hover rounded-lg transition-colors">
            <XMarkIcon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Tab nav */}
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
                    ? 'border-yellow-500 text-yellow-400'
                    : 'border-transparent text-gray-500 hover:text-gray-300'
                )}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* === General Tab === */}
          {activeTab === 'general' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Name</label>
                <input type="text" value={name} onChange={e => handleNameChange(e.target.value)}
                  placeholder="e.g., code-review, debugging"
                  className={clsx(
                    'w-full px-3 py-2 bg-dark-bg border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none',
                    nameError ? 'border-red-500/50 focus:border-red-500' : 'border-dark-border focus:border-yellow-500'
                  )} />
                {nameError ? (
                  <p className="text-[10px] text-red-400 mt-1">{nameError}</p>
                ) : (
                  <p className="text-[10px] text-gray-600 mt-1">Lowercase letters, numbers, and hyphens only</p>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Description</label>
                <textarea value={description} onChange={e => setDescription(e.target.value)}
                  placeholder="Describe what this skill does AND when to use it. Include trigger keywords."
                  rows={3}
                  className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-yellow-500 resize-none" />
                <p className="text-[10px] text-gray-600 mt-1">Include specific keywords that help agents identify when to activate this skill</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">Category</label>
                <div className="flex flex-wrap gap-2">
                  {categoryOptions.map(opt => (
                    <button key={opt.value} type="button"
                      onClick={() => setCategory(opt.value)}
                      className={clsx(
                        'px-3 py-1.5 rounded-full text-sm transition-colors border',
                        category === opt.value
                          ? `${opt.color} border-current`
                          : 'border-dark-border text-gray-500 hover:border-dark-hover'
                      )}>
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-2">Tags</label>
                {tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {tags.map((tag, i) => (
                      <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-dark-bg text-xs text-gray-300">
                        <TagIcon className="w-3 h-3" />
                        {tag}
                        <button type="button" onClick={() => setTags(prev => prev.filter((_, idx) => idx !== i))}
                          className="hover:text-red-400"><XMarkIcon className="w-3 h-3" /></button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <input type="text" value={newTag} onChange={e => setNewTag(e.target.value)}
                    placeholder="Add tag..."
                    onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addTag())}
                    className="flex-1 px-3 py-1.5 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm placeholder-gray-500 focus:outline-none focus:border-yellow-500" />
                  <button type="button" onClick={addTag} className="px-2 py-1.5 bg-dark-hover hover:bg-dark-border rounded-lg">
                    <PlusIcon className="w-4 h-4 text-gray-400" />
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Author</label>
                  <input type="text" value={author} onChange={e => setAuthor(e.target.value)}
                    placeholder="Author name"
                    className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-yellow-500" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">Version</label>
                  <input type="text" value={version} onChange={e => setVersion(e.target.value)}
                    placeholder="1.0.0"
                    className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-yellow-500" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-400 mb-1">License</label>
                  <input type="text" value={skillLicense} onChange={e => setSkillLicense(e.target.value)}
                    placeholder="e.g., Apache-2.0"
                    className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-yellow-500" />
                </div>
              </div>

              <div className="flex items-center justify-between p-3 bg-dark-bg rounded-lg">
                <div>
                  <span className="text-sm text-gray-300">Enabled</span>
                  <p className="text-xs text-gray-500">Disabled skills won't be injected into agent prompts</p>
                </div>
                <button type="button" onClick={() => setEnabled(v => !v)}
                  className={clsx('w-10 h-6 rounded-full transition-colors relative', enabled ? 'bg-yellow-600' : 'bg-gray-600')}>
                  <span className={clsx('absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform', enabled ? 'left-[18px]' : 'left-0.5')} />
                </button>
              </div>
            </>
          )}

          {/* === Instructions Tab === */}
          {activeTab === 'instructions' && (
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Instructions (Markdown)</label>
              <p className="text-xs text-gray-500 mb-2">
                Detailed procedural knowledge, workflows, and guidelines. This content is injected
                into the agent's prompt when the skill is activated.
              </p>
              <textarea value={instructions} onChange={e => setInstructions(e.target.value)}
                placeholder="Write your skill instructions in Markdown..."
                rows={22}
                className="w-full px-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 placeholder-gray-500 text-sm focus:outline-none focus:border-yellow-500 resize-none font-mono leading-relaxed" />
              <div className="flex items-center justify-between mt-2">
                <div className="flex items-center gap-3">
                  <span className="text-xs text-gray-500">Supports Markdown formatting</span>
                  {instructions !== DEFAULT_SKILL_TEMPLATE && (
                    <button type="button" onClick={() => setInstructions(DEFAULT_SKILL_TEMPLATE)}
                      className="text-[10px] text-yellow-400/60 hover:text-yellow-400 transition-colors">
                      Reset to template
                    </button>
                  )}
                </div>
                <span className="text-xs text-gray-500">{instructions.length} chars</span>
              </div>
            </div>
          )}

          {/* === Requirements Tab === */}
          {activeTab === 'requirements' && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Required Tools</label>
                <p className="text-xs text-gray-500 mb-2">
                  Tools that this skill needs. Agents without these tools won't be able to use this skill effectively.
                </p>
                {requiredTools.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {requiredTools.map((t, i) => (
                      <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/10 text-xs text-green-400 font-mono">
                        {t}
                        <button type="button" onClick={() => setRequiredTools(prev => prev.filter((_, idx) => idx !== i))}
                          className="hover:text-red-400"><XMarkIcon className="w-3 h-3" /></button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="relative flex gap-2">
                  <div className="relative flex-1">
                    <input type="text" ref={toolInputRef} value={newTool}
                      onChange={e => { setNewTool(e.target.value); setShowToolSuggestions(true) }}
                      onFocus={() => setShowToolSuggestions(true)}
                      placeholder="Type to search tools..."
                      onKeyDown={e => {
                        if (e.key === 'Enter') { e.preventDefault(); addTool() }
                        if (e.key === 'Escape') setShowToolSuggestions(false)
                      }}
                      className="w-full px-3 py-1.5 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm font-mono placeholder-gray-500 focus:outline-none focus:border-yellow-500" />
                    {showToolSuggestions && filteredToolSuggestions.length > 0 && (
                      <div ref={suggestionsRef}
                        className="absolute left-0 right-0 top-full mt-1 bg-dark-surface border border-dark-border rounded-lg shadow-xl z-50 max-h-48 overflow-y-auto">
                        {filteredToolSuggestions.map(t => (
                          <button key={t.id} type="button"
                            onClick={() => addTool(t.id)}
                            className="w-full text-left px-3 py-2 hover:bg-dark-hover transition-colors first:rounded-t-lg last:rounded-b-lg">
                            <div className="text-sm font-mono text-gray-200">{t.id}</div>
                            <div className="text-[10px] text-gray-500 truncate">{t.description}</div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <button type="button" onClick={() => addTool()} className="px-2 py-1.5 bg-dark-hover hover:bg-dark-border rounded-lg shrink-0">
                    <PlusIcon className="w-4 h-4 text-gray-400" />
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">Required Environment Variables</label>
                <p className="text-xs text-gray-500 mb-2">
                  Environment variables that must be set for this skill to function.
                </p>
                {requiredEnv.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {requiredEnv.map((e, i) => (
                      <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-500/10 text-xs text-purple-400 font-mono">
                        {e}
                        <button type="button" onClick={() => setRequiredEnv(prev => prev.filter((_, idx) => idx !== i))}
                          className="hover:text-red-400"><XMarkIcon className="w-3 h-3" /></button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <input type="text" value={newEnv} onChange={e => setNewEnv(e.target.value)}
                    placeholder="e.g., OPENAI_API_KEY"
                    onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addEnv())}
                    className="flex-1 px-3 py-1.5 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm font-mono placeholder-gray-500 focus:outline-none focus:border-yellow-500" />
                  <button type="button" onClick={addEnv} className="px-2 py-1.5 bg-dark-hover hover:bg-dark-border rounded-lg">
                    <PlusIcon className="w-4 h-4 text-gray-400" />
                  </button>
                </div>
              </div>
            </>
          )}

          {/* Actions */}
          <div className="flex items-center justify-between pt-4 border-t border-dark-border">
            <span className="text-[10px] text-gray-600">Skills are saved locally and persist across sessions.</span>
            <div className="flex gap-3">
              <button type="button" onClick={onClose} disabled={loading}
                className="px-4 py-2 text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50">
                Cancel
              </button>
              <button type="submit" disabled={loading || !name.trim() || !!nameError}
                className="px-5 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-700 disabled:bg-gray-700 text-white font-medium transition-colors disabled:cursor-not-allowed">
                {loading ? 'Saving...' : isEditing ? 'Save Changes' : 'Create Skill'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}
