import React, { useState, useCallback } from 'react'
import {
  MagnifyingGlassIcon,
  CircleStackIcon,
  ArrowPathIcon,
  TagIcon,
  PlusIcon,
} from '@heroicons/react/24/outline'
import { getApiBase } from '../../utils/api'
import { formatNumberNoTrailingZeros } from '../../utils/format'

interface MemoryEntry {
  id: string
  content: string
  tags: string[]
  source: string
  score: number
  created_at: string
}

export default function MemoryPanel() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<MemoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [showSave, setShowSave] = useState(false)
  const [saveContent, setSaveContent] = useState('')
  const [saveTags, setSaveTags] = useState('')
  const [saveStatus, setSaveStatus] = useState('')

  const search = useCallback(async () => {
    if (!query.trim()) return
    setLoading(true)
    setSearched(true)
    try {
      const res = await fetch(`${getApiBase()}/api/memory/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim(), top_k: 20 }),
      })
      if (res.ok) {
        const data = await res.json()
        setResults(data.results || [])
      }
    } catch (e) { console.warn('MemoryPanel error:', e); }
    finally { setLoading(false) }
  }, [query])

  const loadRecent = useCallback(async () => {
    setLoading(true)
    setSearched(true)
    setQuery('')
    try {
      const res = await fetch(`${getApiBase()}/api/memory/recent?limit=20`)
      if (res.ok) {
        const data = await res.json()
        setResults(data.entries || [])
      }
    } catch (e) { console.warn('MemoryPanel error:', e); }
    finally { setLoading(false) }
  }, [])

  const saveMemory = useCallback(async () => {
    if (!saveContent.trim()) return
    try {
      const tags = saveTags.split(',').map(t => t.trim()).filter(Boolean)
      const res = await fetch(`${getApiBase()}/api/memory/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: saveContent.trim(), tags, source: 'manual' }),
      })
      if (res.ok) {
        setSaveContent('')
        setSaveTags('')
        setShowSave(false)
        setSaveStatus('saved')
        setTimeout(() => setSaveStatus(''), 2000)
        loadRecent()
      } else {
        setSaveStatus('error')
      }
    } catch (e) { console.warn('MemoryPanel error:', e); setSaveStatus('error'); }
  }, [saveContent, saveTags, loadRecent])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') search()
  }

  return (
    <div className="bg-dark-surface border border-dark-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <CircleStackIcon className="w-5 h-5 text-purple-400" />
          <h3 className="text-sm font-semibold text-white">Agent Memory</h3>
        </div>
        <div className="flex items-center gap-1">
          {saveStatus === 'saved' && <span className="text-xs text-green-400">Saved</span>}
          <button
            onClick={() => setShowSave(s => !s)}
            className="flex items-center gap-1 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors"
          >
            <PlusIcon className="w-3.5 h-3.5" />
            Add
          </button>
          <button
            onClick={loadRecent}
            className="flex items-center gap-1 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors"
          >
            <ArrowPathIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Recent
          </button>
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        <div className="flex-1 relative">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search agent memory..."
            className="w-full pl-9 pr-3 py-2 bg-dark-bg border border-dark-border rounded-lg text-gray-200 text-sm focus:outline-none focus:border-primary-500"
          />
        </div>
        <button
          onClick={search}
          disabled={loading || !query.trim()}
          className="px-4 py-2 bg-primary-600 text-white text-sm rounded-lg hover:bg-primary-700 transition-colors disabled:opacity-50"
        >
          Search
        </button>
      </div>

      {showSave && (
        <div className="mb-4 p-3 bg-dark-bg border border-dark-border rounded-lg space-y-2">
          <textarea
            value={saveContent}
            onChange={e => setSaveContent(e.target.value)}
            placeholder="Memory content to save..."
            rows={2}
            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm focus:outline-none focus:border-primary-500 resize-none"
          />
          <input
            type="text"
            value={saveTags}
            onChange={e => setSaveTags(e.target.value)}
            placeholder="Tags (comma-separated)"
            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm focus:outline-none focus:border-primary-500"
          />
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowSave(false)} className="px-3 py-1.5 text-gray-400 text-sm hover:text-gray-200 transition-colors">Cancel</button>
            <button onClick={saveMemory} disabled={!saveContent.trim()} className="px-3 py-1.5 bg-primary-600 text-white text-sm rounded-lg hover:bg-primary-700 transition-colors disabled:opacity-50">Save</button>
          </div>
        </div>
      )}

      {!searched ? (
        <p className="text-xs text-gray-500 text-center py-8">
          Search agent memory or click "Recent" to browse entries
        </p>
      ) : results.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-8">No memory entries found</p>
      ) : (
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {results.map(entry => (
            <div key={entry.id} className="p-3 bg-dark-bg rounded-lg border border-dark-border">
              <p className="text-sm text-gray-200 whitespace-pre-wrap break-words">{entry.content}</p>
              <div className="flex items-center gap-3 mt-2">
                {entry.tags.length > 0 && (
                  <div className="flex items-center gap-1">
                    <TagIcon className="w-3 h-3 text-gray-500" />
                    {entry.tags.map(tag => (
                      <span key={tag} className="text-xs px-1.5 py-0.5 bg-primary-500/10 text-primary-400 rounded">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <span className="text-xs text-gray-500">{entry.source}</span>
                {entry.score > 0 && (
                  <span className="text-xs text-gray-500">score: {formatNumberNoTrailingZeros(entry.score, 2)}</span>
                )}
                {entry.created_at && (
                  <span className="text-xs text-gray-500">{entry.created_at}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
