import { useApi } from '../api/client'
import { fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'
import { Missing, Skeleton } from './ui'

type Idx = {
  key: string
  label: string
  exchange: string
  state: string
  value: number | null
  prev_close?: number | null
  change?: number | null
  change_pct?: number | null
  high?: number | null
  low?: number | null
  year_high?: number | null
  year_low?: number | null
  as_of?: string | null
  source?: string
  reason?: string
  note?: string
}

/** Where the value sits inside a low-high range, drawn as a marker on a track. */
function RangeBar({ low, high, value, label }: { low?: number | null; high?: number | null; value: number; label: string }) {
  if (low == null || high == null || high <= low) return null
  const pos = Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100))
  return (
    <div className="mt-2" title={`${label}: ${fmtNum(low)} to ${fmtNum(high)}`}>
      <div className="flex justify-between text-[11px] text-ink-3">
        <span className="num">{fmtNum(low, 0)}</span>
        <span>{label}</span>
        <span className="num">{fmtNum(high, 0)}</span>
      </div>
      <div className="relative mt-0.5 h-1.5 rounded-full bg-surface-2" role="img" aria-label={`${label}: value at ${Math.round(pos)}% of the range`}>
        <span className="absolute top-1/2 h-3 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--glyph)]" style={{ left: `${pos}%` }} />
      </div>
    </div>
  )
}

export function IndexStrip() {
  const q = useApi<{ indices: Idx[]; retrieved_at: string }>(['indices'], '/markets/indices', undefined, { refetchInterval: 60_000 })
  if (q.isLoading) return <Skeleton rows={2} className="rounded-[10px] border border-rule bg-surface" />
  if (q.error || !q.data) return <p className="text-sm text-loss">Index values could not be loaded: {(q.error as Error)?.message}</p>
  return (
    <section aria-label="Headline indices" className="grid gap-3 sm:grid-cols-3">
      {q.data.indices.map((i) => (
        <article key={i.key} className="rounded-[10px] border border-rule bg-surface px-4 py-3" data-testid={`index-${i.key}`}>
          <header className="flex items-baseline justify-between gap-2">
            <h2 className="text-[14px] font-semibold">{i.label}</h2>
            <span className="text-[11px] text-ink-3">{i.exchange}</span>
          </header>
          {i.value == null ? (
            <p className="mt-2 text-sm text-ink-2">
              <Missing reason={i.reason} status={i.state} /> <span className="text-xs">{i.reason}</span>
            </p>
          ) : (
            <>
              <div className="mt-1 flex flex-wrap items-baseline gap-x-3">
                <span className="num font-display text-[26px] font-semibold leading-tight">{fmtNum(i.value)}</span>
                <span className={`num text-sm font-medium ${signClass(i.change)}`}>
                  {i.change == null ? <Missing reason="No previous close" /> : `${i.change > 0 ? '+' : ''}${fmtNum(i.change)}`} ({fmtSigned(i.change_pct, 2)})
                </span>
              </div>
              {i.low != null && i.high != null ? (
                <RangeBar low={i.low} high={i.high} value={i.value} label="Day range" />
              ) : (
                <RangeBar low={i.year_low} high={i.year_high} value={i.value} label="52-week range" />
              )}
              <p className="mt-2 text-[11px] text-ink-3">
                {i.as_of ? `As of ${fmtDateTime(i.as_of)}` : 'Time not supplied'} · {i.source} · {i.state === 'delayed' ? 'delayed' : i.state}
                {i.note && <span title={i.note}> · fallback source</span>}
              </p>
            </>
          )}
        </article>
      ))}
    </section>
  )
}
