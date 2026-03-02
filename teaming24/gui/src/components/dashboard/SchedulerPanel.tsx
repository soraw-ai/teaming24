import { useState, useEffect, useCallback } from 'react'
import {
  ClockIcon,
  PlusIcon,
  TrashIcon,
  PlayIcon,
  StopIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline'
import { getApiBase } from '../../utils/api'

interface ScheduledJob {
  id: string
  name: string
  prompt: string
  cron: string
  interval_seconds: number
  enabled: boolean
  last_status: string
  error_count: number
}

export default function SchedulerPanel() {
  const [jobs, setJobs] = useState<ScheduledJob[]>([])
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [newJob, setNewJob] = useState({ name: '', prompt: '', cron: '' })
  const [addError, setAddError] = useState('')

  const fetchJobs = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch(`${getApiBase()}/api/scheduler/jobs`)
      if (res.ok) setJobs(await res.json())
    } catch (e) { console.warn('Failed to fetch jobs:', e); }
    finally { setLoading(false) }
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/scheduler/status`)
      if (res.ok) {
        const data = await res.json()
        setRunning(data.running ?? false)
      }
    } catch (e) { console.warn('Failed to fetch scheduler status:', e); }
  }, [])

  useEffect(() => { fetchJobs(); fetchStatus() }, [fetchJobs, fetchStatus])

  const addJob = async () => {
    if (!newJob.name || !newJob.prompt) {
      setAddError('Name and prompt are required')
      return
    }
    if (!newJob.cron) {
      setAddError('Cron expression is required')
      return
    }
    setAddError('')
    try {
      const res = await fetch(`${getApiBase()}/api/scheduler/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newJob),
      })
      if (!res.ok) {
        const data = await res.json().catch((e) => { console.warn('Failed to add job:', e); return { error: 'Failed to add job' }; })
        setAddError(data.error || 'Failed to add job')
        return
      }
      setNewJob({ name: '', prompt: '', cron: '' })
      setShowAdd(false)
      fetchJobs()
    } catch (e) {
      console.warn('Failed to add scheduled job:', e);
      setAddError('Network error')
    }
  }

  const removeJob = async (id: string) => {
    try {
      await fetch(`${getApiBase()}/api/scheduler/jobs/${id}`, { method: 'DELETE' })
      fetchJobs()
    } catch (e) { console.warn('Failed to remove job:', e); }
  }

  const toggleScheduler = async () => {
    const endpoint = running ? 'stop' : 'start'
    try {
      const res = await fetch(`${getApiBase()}/api/scheduler/${endpoint}`, { method: 'POST' })
      if (res.ok) setRunning(!running)
    } catch (e) { console.warn('Failed to toggle scheduler:', e); }
  }

  return (
    <div className="bg-dark-surface border border-dark-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ClockIcon className="w-5 h-5 text-amber-400" />
          <h3 className="text-sm font-semibold text-white">Scheduled Tasks</h3>
          <span className="text-xs text-gray-500">({jobs.length} jobs)</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={toggleScheduler}
            className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors
              ${running
                ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'}`}
          >
            {running ? <StopIcon className="w-3.5 h-3.5" /> : <PlayIcon className="w-3.5 h-3.5" />}
            {running ? 'Stop' : 'Start'}
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1 px-3 py-1.5 bg-primary-500/20 text-primary-400 rounded-lg text-xs font-medium hover:bg-primary-500/30 transition-colors"
          >
            <PlusIcon className="w-3.5 h-3.5" />
            Add
          </button>
          <button onClick={fetchJobs} className="p-1.5 text-gray-400 hover:text-gray-200 transition-colors">
            <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {showAdd && (
        <div className="mb-4 p-3 bg-dark-bg border border-dark-border rounded-lg space-y-3">
          <input
            type="text"
            value={newJob.name}
            onChange={e => setNewJob(j => ({ ...j, name: e.target.value }))}
            placeholder="Job name"
            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm focus:outline-none focus:border-primary-500"
          />
          <textarea
            value={newJob.prompt}
            onChange={e => setNewJob(j => ({ ...j, prompt: e.target.value }))}
            placeholder="Task prompt (what the agent should do)"
            rows={2}
            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm focus:outline-none focus:border-primary-500 resize-none"
          />
          <input
            type="text"
            value={newJob.cron}
            onChange={e => setNewJob(j => ({ ...j, cron: e.target.value }))}
            placeholder="Cron expression (e.g. 0 9 * * *)"
            className="w-full px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-gray-200 text-sm font-mono focus:outline-none focus:border-primary-500"
          />
          {addError && <p className="text-xs text-red-400">{addError}</p>}
          <div className="flex gap-2 justify-end">
            <button onClick={() => { setShowAdd(false); setAddError('') }} className="px-3 py-1.5 text-gray-400 text-sm hover:text-gray-200 transition-colors">Cancel</button>
            <button onClick={addJob} className="px-3 py-1.5 bg-primary-600 text-white text-sm rounded-lg hover:bg-primary-700 transition-colors">Add Job</button>
          </div>
        </div>
      )}

      {jobs.length === 0 ? (
        <p className="text-xs text-gray-500 text-center py-6">No scheduled jobs configured</p>
      ) : (
        <div className="space-y-2">
          {jobs.map(job => (
            <div key={job.id} className="flex items-center justify-between p-3 bg-dark-bg rounded-lg border border-dark-border">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${job.enabled ? 'bg-green-400' : 'bg-gray-500'}`} />
                  <span className="text-sm text-gray-200 font-medium truncate">{job.name}</span>
                  {job.cron && <span className="text-xs text-gray-500 font-mono">{job.cron}</span>}
                </div>
                <p className="text-xs text-gray-500 mt-1 truncate">{job.prompt}</p>
                {job.last_status && (
                  <span className={`text-xs mt-1 inline-block ${job.last_status === 'completed' ? 'text-green-400' : job.last_status === 'failed' ? 'text-red-400' : 'text-gray-400'}`}>
                    Last: {job.last_status}{job.error_count > 0 ? ` (${job.error_count} errors)` : ''}
                  </span>
                )}
              </div>
              <button onClick={() => removeJob(job.id)} className="p-1.5 text-gray-500 hover:text-red-400 transition-colors ml-2">
                <TrashIcon className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
