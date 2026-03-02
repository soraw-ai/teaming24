import clsx from 'clsx'
import type { AgentMemoryStatus } from '../api/memory'

function formatContextChars(value: number): string {
  const safe = Math.max(0, Number.isFinite(value) ? value : 0)
  if (safe >= 1_000_000) return `${(safe / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (safe >= 1_000) return `${(safe / 1_000).toFixed(1).replace(/\.0$/, '')}k`
  return `${Math.round(safe)}`
}

/**
 * Reusable memory budget indicator.
 *
 * Keeping this out of ChatView makes the memory UX swappable without changing
 * task streaming or request orchestration logic.
 */
export default function AgentMemoryIndicator({ status }: { status: AgentMemoryStatus | null }) {
  const usageRatio = Math.max(0, Math.min(1, status?.usageRatio || 0))
  const radius = 14
  const circumference = 2 * Math.PI * radius
  const dashOffset = circumference * (1 - usageRatio)
  const accentClass = usageRatio >= 0.95
    ? 'text-red-400'
    : usageRatio >= 0.8
      ? 'text-amber-300'
      : 'text-primary-300'
  const ringStroke = usageRatio >= 0.95 ? '#f87171' : usageRatio >= 0.8 ? '#fbbf24' : '#60a5fa'
  const tooltipLines = status
    ? [
        `Context window: ${Math.round(usageRatio * 100)}% full`,
        `${formatContextChars(status.totalChars)} / ${formatContextChars(status.maxChars)} used`,
        'Teaming24 automatically compacts its context',
      ]
    : ['Loading memory status…']

  return (
    <div
      className={clsx(
        'group relative flex items-center gap-2 rounded-2xl border px-2.5 py-1.5 bg-dark-bg/70',
        status?.isCompacting ? 'border-amber-500/40' : 'border-dark-border',
      )}
    >
      <div className={clsx('relative', status?.isCompacting && 'animate-pulse')}>
        <svg width="34" height="34" viewBox="0 0 34 34" className="block">
          <circle cx="17" cy="17" r={radius} fill="none" stroke="rgba(148,163,184,0.18)" strokeWidth="3" />
          <circle
            cx="17"
            cy="17"
            r={radius}
            fill="none"
            stroke={ringStroke}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            transform="rotate(-90 17 17)"
          />
        </svg>
        <span className={clsx('absolute inset-0 flex items-center justify-center text-[10px] font-semibold', accentClass)}>
          {Math.round(usageRatio * 100)}%
        </span>
      </div>
      <div className="min-w-0">
        <div className="text-[11px] font-medium text-gray-300">Agent Memory</div>
        <div className={clsx('text-[11px] leading-tight', accentClass)}>
          {status
            ? `${formatContextChars(status.totalChars)} / ${formatContextChars(status.maxChars)} used`
            : 'Loading…'}
        </div>
      </div>
      <div className="pointer-events-none absolute right-0 top-full z-20 mt-2 hidden w-64 rounded-xl border border-dark-border bg-dark-card/95 px-3 py-2 text-[11px] leading-relaxed text-gray-200 shadow-xl backdrop-blur group-hover:block">
        {tooltipLines.map((line) => (
          <div key={line}>{line}</div>
        ))}
      </div>
    </div>
  )
}
