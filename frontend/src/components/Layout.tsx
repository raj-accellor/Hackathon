import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useApi } from '../api/client'
import { SourcesSchema, type Sources } from '../api/schemas'
import { ago, fmtDate } from '../lib/format'
import { InstrumentSearch } from './InstrumentSearch'
import { cx, stateTone } from './ui'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/screener', label: 'Screener' },
  { to: '/themes', label: 'Themes' },
  { to: '/rotation', label: 'Sector rotation' },
  { to: '/research', label: 'Research board' },
  { to: '/concalls', label: 'Concall follow-through' },
  { to: '/watchlists', label: 'Watchlists and alerts' },
  { to: '/status', label: 'Data status' },
]

/** Persistent strip that answers "where is this data from and how fresh is it" on every page. */
export function ProvenanceStrip({ s }: { s?: Sources }) {
  if (!s) return <div className="h-9 border-b border-rule bg-surface" aria-hidden />
  const show = ['nse_bhavcopy', 'nse_cm_market', 'kite_mcp', 'llm', 'web_search']
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-rule bg-surface px-4 py-2 text-xs text-ink-2 lg:px-6" aria-label="Data sources and freshness">
      <span>
        Prices to <strong className="font-semibold text-ink">{fmtDate(s.coverage.last_bar)}</strong>
      </span>
      <span>Exchange NSE, currency INR</span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {s.connectors
          .filter((c) => show.includes(c.key))
          .map((c) => {
            const t = stateTone(c.state)
            return (
              <NavLink key={c.key} to="/status" className="inline-flex items-center gap-1 text-ink-2 no-underline hover:text-ink" title={`${c.label}: ${c.state.replace(/_/g, ' ')}`}>
                <span aria-hidden className={t.tone === 'good' ? 'text-gain' : t.tone === 'bad' ? 'text-loss' : t.tone === 'warn' ? 'text-warn' : 'text-ink-3'}>
                  {t.mark}
                </span>
                {c.label.replace(' MCP', '').replace(' (Hugging Face)', '')}
                <span className="sr-only">: {c.state.replace(/_/g, ' ')}</span>
              </NavLink>
            )
          })}
      </span>
      <span className="ml-auto">Checked {ago(s.retrieved_at)}</span>
    </div>
  )
}

export function DemoBanner() {
  return (
    <div role="status" className="bg-accent px-4 py-1.5 text-center text-[13px] font-medium text-[#14213d]">
      Demo mode: all prices, financials and transcripts shown are synthetic and for demonstration only. Symbols starting with DEMO are fictional.
    </div>
  )
}

export function Layout() {
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const src = useApi<Sources>(['sources'], '/sources/status', undefined, { schema: SourcesSchema as any, refetchInterval: 60_000, retry: 1 })
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      {src.data?.is_demo && <DemoBanner />}
      <div className="lg:grid lg:grid-cols-[232px_minmax(0,1fr)]">
        <aside className="border-b border-rule bg-surface lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between px-4 py-3">
            <NavLink to="/" className="flex items-center gap-2 text-ink no-underline">
              <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
                <rect width="32" height="32" rx="7" fill="var(--ink)" />
                <g fill="var(--accent)">
                  <rect x="6" y="17" width="3" height="9" rx="1" />
                  <rect x="11" y="12" width="3" height="14" rx="1" />
                  <rect x="16" y="15" width="3" height="11" rx="1" />
                  <rect x="21" y="7" width="3" height="19" rx="1" />
                </g>
              </svg>
              <span className="font-display text-[17px] font-semibold leading-none">Markets Intelligence</span>
            </NavLink>
            <button className="rounded border border-rule-strong px-2.5 py-1.5 text-sm lg:hidden" aria-expanded={open} aria-controls="primary-nav" onClick={() => setOpen((o) => !o)}>
              Menu
            </button>
          </div>
          <nav id="primary-nav" aria-label="Primary" className={cx('px-2 pb-3 lg:block', open ? 'block' : 'hidden')}>
            <ul className="m-0 list-none space-y-0.5 p-0">
              {NAV.map((n) => (
                <li key={n.to}>
                  <NavLink
                    to={n.to}
                    end={n.end}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      cx('block rounded-md px-3 py-2 text-[14px] no-underline', isActive ? 'bg-accent-soft font-semibold text-ink shadow-[inset_3px_0_0_var(--accent)]' : 'text-ink-2 hover:bg-surface-2 hover:text-ink')
                    }
                  >
                    {n.label}
                  </NavLink>
                </li>
              ))}
            </ul>
            <p className="mt-4 px-3 text-xs leading-relaxed text-ink-3">Research and screening only. This tool cannot place orders and its output is not investment advice.</p>
          </nav>
        </aside>
        <div className="min-w-0">
          <div className="flex items-center gap-3 border-b border-rule bg-surface px-4 py-2 lg:px-6">
            <div className="w-full max-w-md">
              <InstrumentSearch onPick={(i) => nav(`/instrument/${i.exchange}:${encodeURIComponent(i.symbol)}`)} placeholder="Find an instrument by symbol or name" />
            </div>
          </div>
          <ProvenanceStrip s={src.data} />
          <main id="main" className="mx-auto max-w-[1400px] px-4 py-5 lg:px-6">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  )
}
