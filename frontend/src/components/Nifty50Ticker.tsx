import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../api/client'
import { fmtDateTime, fmtNum } from '../lib/format'
import { cx } from './ui'

type Item = { symbol: string; last_price: number | null; change_pct: number | null; source: string | null; as_of?: string | null }

function Change({ v, dark }: { v: number | null; dark?: boolean }) {
  if (v == null) return <span className={dark ? 'text-rail-ink-2' : 'text-ink-3'}>—</span>
  const up = v > 0
  const down = v < 0
  const tone = dark ? (up ? 'text-[var(--gain-on-dark)]' : down ? 'text-[var(--loss-on-dark)]' : 'text-rail-ink-2') : up ? 'text-gain' : down ? 'text-loss' : 'text-ink-2'
  return (
    <span className={cx('num', tone)}>
      <span aria-hidden>{up ? '▲' : down ? '▼' : '■'}</span> {fmtNum(Math.abs(v), 2)}%
      <span className="sr-only">{up ? ' up' : down ? ' down' : ' unchanged'}</span>
    </span>
  )
}

function Entry({ to, label, price, change, title, dark }: { to?: string; label: string; price: number | null; change: number | null; title?: string; dark?: boolean }) {
  const body = (
    <>
      <span className={cx('font-semibold', dark ? 'text-white' : 'text-ink')}>{label}</span>
      <span className={cx('num', dark ? 'text-rail-ink' : 'text-ink')}>{price == null ? '—' : fmtNum(price, 2)}</span>
      <Change v={change} dark={dark} />
    </>
  )
  const cls = 'inline-flex shrink-0 items-baseline gap-2 whitespace-nowrap px-3 text-[13px] no-underline'
  return to ? (
    <Link to={to} className={cls} title={title}>
      {body}
    </Link>
  ) : (
    <span className={cls} title={title}>
      {body}
    </span>
  )
}

/** Nifty 50 constituents and the index, scrolling slowly; pauses on hover or keyboard focus and for reduced motion. */
export function Nifty50Ticker({ tone = 'light' }: { tone?: 'light' | 'dark' }) {
  const dark = tone === 'dark'
  const q = useApi<any>(['nifty50-ticker'], '/markets/nifty50', undefined, { refetchInterval: 60_000 })
  const track = useRef<HTMLDivElement>(null)
  const [paused, setPaused] = useState(false)
  const [reduced] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
  const items: Item[] = q.data?.items ?? []

  useEffect(() => {
    if (reduced || paused || items.length === 0) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const el = track.current
      if (el) {
        el.scrollLeft += ((now - last) / 1000) * 40 // 40 px per second
        if (el.scrollLeft >= el.scrollWidth / 2) el.scrollLeft -= el.scrollWidth / 2 // the list is drawn twice, so this loops seamlessly
      }
      last = now
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [reduced, paused, items.length])

  const nudge = (dir: number) => track.current?.scrollBy({ left: dir * 320, behavior: 'smooth' })
  if (q.isLoading)
    return dark ? <div className="h-10 border-y border-rail-2 bg-rail-2/40" aria-label="Loading Nifty 50 prices" role="status" /> : <div className="mb-4 h-10 rounded-[10px] border border-rule bg-surface skeleton" aria-label="Loading Nifty 50 prices" role="status" />
  if (q.error || !q.data || q.data.state === 'not_configured' || items.length === 0) {
    return q.data?.reason ? <p className={cx('text-xs', dark ? 'border-y border-rail-2 px-5 py-2.5 text-rail-ink-2 lg:px-8' : 'mb-4 text-ink-2')}>Nifty 50 ticker: {q.data.reason}</p> : null
  }
  const d = q.data
  const idx = d.index ?? {}
  const sources = [d.sources?.nse?.priced ? 'NSE' : null, d.sources?.kite?.priced ? 'Zerodha Kite' : null].filter(Boolean).join(' and ')
  const row = (copy: number) => (
    <div className="flex shrink-0 items-center" aria-hidden={copy === 1 || undefined}>
      {items.map((i) => (
        <Entry key={`${copy}-${i.symbol}`} to={copy === 0 ? `/instrument/NSE:${encodeURIComponent(i.symbol)}` : undefined} label={i.symbol}
          price={i.last_price} change={i.change_pct} dark={dark} title={i.source ? `${i.symbol} · ${i.source}${i.as_of ? ` · ${fmtDateTime(i.as_of)}` : ''}` : `${i.symbol}: no price from NSE or Kite`} />
      ))}
      <Entry label="NIFTY 50" price={idx.value ?? null} change={idx.change_pct ?? null} dark={dark} title={idx.source ? `Nifty 50 · ${idx.source}` : idx.reason} />
    </div>
  )
  return (
    <section
      aria-label="Nifty 50 constituents"
      className={cx('flex items-center', dark ? 'border-y border-rail-2 bg-rail-2/40' : 'mb-4 rounded-[10px] border border-rule bg-surface')}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      data-testid="nifty50-ticker"
    >
      <button type="button" onClick={() => nudge(-1)} aria-label="Scroll Nifty 50 ticker left" className={cx('shrink-0 px-2.5 py-2', dark ? 'text-rail-ink-2 hover:text-white' : 'text-ink-2 hover:text-ink')}>
        ‹
      </button>
      <div ref={track} className="relative flex min-w-0 flex-1 overflow-x-auto py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" tabIndex={0}
        aria-label={`Nifty 50 prices, ${d.priced} of ${d.total} priced from ${sources || 'no source'}`}>
        {row(0)}
        {!reduced && row(1)}
      </div>
      <button type="button" onClick={() => nudge(1)} aria-label="Scroll Nifty 50 ticker right" className={cx('shrink-0 px-2.5 py-2', dark ? 'text-rail-ink-2 hover:text-white' : 'text-ink-2 hover:text-ink')}>
        ›
      </button>
    </section>
  )
}
