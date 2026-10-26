import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, post, useAction, useApi } from '../api/client'
import { InstrumentSearch, type InstrumentLite } from '../components/InstrumentSearch'
import { Async, Button, Confirm, EmptyState, ErrorState, PageHeader, RefreshButton, cx } from '../components/ui'
import { fmtDate } from '../lib/format'
import { instrumentPath } from './Overview'

type Alert = { id: number; kind: string; instrument_id: number | null; symbol: string | null; params: Record<string, any>; status: string; created_at: string; last_triggered_at: string | null }
type Event = { id: number; alert_id: number; kind: string; message: string; triggered_at: string; delivery_status: string }

const KINDS: Array<{ id: string; title: string; hint: string; needs: 'instrument' | 'optional' | 'none' }> = [
  { id: 'price_move', title: 'A price moves sharply', hint: 'Up or down by a percentage over a week, month or quarter', needs: 'instrument' },
  { id: 'indicator_cross', title: 'A metric crosses a level', hint: 'RSI, P/E, ROE or any screener metric above or below a value', needs: 'instrument' },
  { id: 'sector_quadrant', title: 'A sector changes quadrant', hint: 'On the rotation graph, e.g. Improving to Leading', needs: 'none' },
  { id: 'theme_membership', title: 'A theme changes members', hint: 'A stock enters or leaves a theme at its refresh', needs: 'optional' },
  { id: 'filing', title: 'New results are filed', hint: 'Fresh financial statements are ingested for a stock', needs: 'instrument' },
  { id: 'data_quality', title: 'Price data goes stale', hint: "A stock's prices stop updating or go missing", needs: 'instrument' },
]
const KIND_TITLE = Object.fromEntries(KINDS.map((k) => [k.id, k.title]))
const WINDOW: Record<string, string> = { '1W': '1 week', '1M': '1 month', '3M': '3 months' }

function describe(a: { kind: string; symbol: string | null; params: Record<string, any> }, metrics: any[], themes: any[]): string {
  const p = a.params
  const metric = metrics.find((m) => m.key === p.metric)?.label ?? p.metric
  const theme = themes.find((t) => t.id === Number(p.theme_id))?.name ?? `theme ${p.theme_id}`
  switch (a.kind) {
    case 'price_move':
      return `${a.symbol} moves at least ${p.pct}% over ${WINDOW[p.window] ?? p.window}`
    case 'indicator_cross':
      return `${a.symbol}'s ${metric} crosses ${p.op === 'gt' ? 'above' : 'below'} ${p.value}`
    case 'theme_membership':
      return a.symbol ? `${a.symbol} enters or leaves ${theme}` : `${theme} gains or loses a member`
    case 'sector_quadrant':
      return `${p.sector} moves to another rotation quadrant`
    case 'data_quality':
      return `${a.symbol}'s price data goes stale or missing`
    case 'filing':
      return `${a.symbol} files new financial statements`
    default:
      return a.kind
  }
}

function Switch({ on, onChange, label, busy }: { on: boolean; onChange: () => void; label: string; busy?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={busy}
      onClick={onChange}
      className={cx('relative inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full border transition-colors disabled:opacity-50', on ? 'border-gain bg-gain' : 'border-rule-strong bg-surface-2')}
    >
      <span aria-hidden className={cx('inline-block h-4 w-4 rounded-full bg-surface shadow transition-transform', on ? 'translate-x-[18px]' : 'translate-x-[2px]')} />
    </button>
  )
}

/** Inline sentence field: sits in the running text of the rule, so the rule reads as it will fire. */
const inlineField = 'field inline-block h-[34px] align-baseline'

function Builder({ metrics, sectors, themes }: { metrics: any[]; sectors: string[]; themes: any[] }) {
  const [sp] = useSearchParams()
  const preset: InstrumentLite | null = sp.get('id') && sp.get('instrument') ? { id: Number(sp.get('id')), symbol: sp.get('instrument')!, exchange: 'NSE', name: null, sector: null, instrument_type: 'EQ' } : null
  const [kind, setKind] = useState('price_move')
  const [inst, setInst] = useState<InstrumentLite | null>(preset)
  const [p, setP] = useState<Record<string, string>>({ window: '1W', pct: '5', metric: 'rsi_14', op: 'gt', value: '70', sector: '', theme_id: '' })
  const set = (k: string, v: string) => setP({ ...p, [k]: v })
  const spec = KINDS.find((k) => k.id === kind)!
  const params: Record<string, unknown> =
    kind === 'price_move' ? { window: p.window, pct: Number(p.pct) } : kind === 'indicator_cross' ? { metric: p.metric, op: p.op, value: Number(p.value) } : kind === 'sector_quadrant' ? { sector: p.sector } : kind === 'theme_membership' ? { theme_id: Number(p.theme_id) } : {}
  const numBad = (kind === 'price_move' && !(Number(p.pct) > 0)) || (kind === 'indicator_cross' && !Number.isFinite(Number(p.value)))
  const ready = !(spec.needs === 'instrument' && !inst) && !(kind === 'sector_quadrant' && !p.sector) && !(kind === 'theme_membership' && !p.theme_id) && !numBad
  const create = useAction<void, any>(() => {
    // The key makes a repeated submit of the same alert a no-op on the server.
    const key = `${kind}:${inst?.id ?? 'all'}:${JSON.stringify(params)}`.slice(0, 80).padEnd(8, '_')
    return post('/alerts', { kind, instrument_id: spec.needs === 'none' ? null : (inst?.id ?? null), params, idempotency_key: key })
  }, [['alerts']])
  const preview = ready ? describe({ kind, symbol: inst?.symbol ?? null, params }, metrics, themes) : null
  const instField = (
    <span className="inline-flex flex-wrap items-center gap-2 align-middle leading-normal">
      {inst ? (
        <>
          <span className="rounded-[7px] border border-accent/50 bg-accent-soft px-2 py-0.5 font-semibold">{inst.symbol}</span>
          <Button variant="quiet" className="min-h-[30px] px-2" onClick={() => setInst(null)}>
            Change
          </Button>
        </>
      ) : (
        <span className="inline-block w-64 align-middle">
          <InstrumentSearch equitiesOnly onPick={setInst} placeholder={spec.needs === 'optional' ? 'Any stock (optional)' : 'Choose a stock'} />
        </span>
      )}
    </span>
  )
  return (
    <section className="panel rounded-[10px] border border-rule bg-surface" aria-labelledby="new-alert">
      <header className="border-b border-rule px-5 py-3">
        <h2 id="new-alert" className="m-0 text-[15.5px] font-semibold">
          New alert
        </h2>
      </header>
      <fieldset className="m-0 border-0 px-3 pb-1 pt-3">
        <legend className="mb-1.5 px-2 text-[13px] font-medium text-ink-2">What should we watch?</legend>
        <div className="grid gap-1 sm:grid-cols-2">
          {KINDS.map((k) => (
            <label key={k.id} className={cx('flex cursor-pointer gap-2.5 rounded-[8px] border px-3 py-2', kind === k.id ? 'border-accent bg-accent-soft' : 'border-transparent hover:bg-surface-2')}>
              <input type="radio" name="alert-kind" value={k.id} checked={kind === k.id} onChange={() => setKind(k.id)} className="mt-1 accent-[var(--rail)]" />
              <span>
                <span className="block text-[13.5px] font-medium">{k.title}</span>
                <span className="block text-[12px] leading-snug text-ink-2">{k.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="border-t border-rule px-5 py-4">
        <p className="m-0 text-[15px] leading-[2.6]" data-testid="alert-sentence">
          <span className="font-medium">Tell me when </span>
          {kind === 'price_move' && (
            <>
              {instField} <span>moves at least</span>{' '}
              <label>
                <span className="sr-only">Move of at least (%)</span>
                <input className={cx(inlineField, 'num w-20')} inputMode="decimal" value={p.pct} onChange={(e) => set('pct', e.target.value)} />
              </label>{' '}
              % over{' '}
              <label>
                <span className="sr-only">Over</span>
                <select className={inlineField} value={p.window} onChange={(e) => set('window', e.target.value)}>
                  {Object.entries(WINDOW).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          {kind === 'indicator_cross' && (
            <>
              {instField} <span>has its</span>{' '}
              <label>
                <span className="sr-only">Metric</span>
                <select className={cx(inlineField, 'max-w-[220px]')} value={p.metric} onChange={(e) => set('metric', e.target.value)}>
                  {metrics.map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>{' '}
              cross{' '}
              <label>
                <span className="sr-only">Crosses</span>
                <select className={inlineField} value={p.op} onChange={(e) => set('op', e.target.value)}>
                  <option value="gt">above</option>
                  <option value="lt">below</option>
                </select>
              </label>{' '}
              <label>
                <span className="sr-only">Level</span>
                <input className={cx(inlineField, 'num w-24')} inputMode="decimal" value={p.value} onChange={(e) => set('value', e.target.value)} />
              </label>
            </>
          )}
          {kind === 'sector_quadrant' && (
            <>
              <label>
                <span className="sr-only">Sector</span>
                <select className={inlineField} value={p.sector} onChange={(e) => set('sector', e.target.value)}>
                  <option value="">choose a sector</option>
                  {sectors.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>{' '}
              moves to another quadrant on the rotation graph
            </>
          )}
          {kind === 'theme_membership' && (
            <>
              <label>
                <span className="sr-only">Theme</span>
                <select className={cx(inlineField, 'max-w-[260px]')} value={p.theme_id} onChange={(e) => set('theme_id', e.target.value)}>
                  <option value="">choose a theme</option>
                  {themes.filter((t) => t.available).map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>{' '}
              gains or loses {instField}
            </>
          )}
          {kind === 'filing' && <>{instField} files new financial statements</>}
          {kind === 'data_quality' && <>{instField}'s price data goes stale or missing</>}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button variant="primary" busy={create.isPending} disabled={!ready} onClick={() => create.mutate()}>
            Create alert
          </Button>
          <span className="text-[13px] text-ink-2" aria-live="polite">
            {create.data ? (create.data.created ? `Alert created: ${preview}.` : 'This alert already exists.') : preview ? 'Notified in this app, checked every 15 minutes.' : 'Fill in the highlighted parts of the sentence.'}
          </span>
        </div>
        {create.error && (
          <div className="mt-2">
            <ErrorState error={create.error} />
          </div>
        )}
      </div>
    </section>
  )
}

function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const y = new Date(today)
  y.setDate(today.getDate() - 1)
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  return same(d, today) ? 'Today' : same(d, y) ? 'Yesterday' : fmtDate(iso)
}
const time = (iso: string) => new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })

export function Alerts() {
  const al = useApi<{ items: Alert[]; history: Event[]; delivery_note: string }>(['alerts'], '/alerts')
  const cat = useApi<any>(['metric-catalog'], '/screener/metrics', undefined, { staleTime: 3_600_000 })
  const sectors = useApi<any>(['sectors'], '/sectors')
  const themes = useApi<any>(['themes'], '/themes')
  const metrics = cat.data?.metrics ?? []
  const themeList = themes.data?.items ?? []
  const [filter, setFilter] = useState<'all' | 'active' | 'paused'>('all')
  const [del, setDel] = useState<{ id: number; label: string } | null>(null)
  const toggle = useAction<{ id: number; status: string }, any>(({ id, status }) => api(`/alerts/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }), [['alerts']])
  const doDelete = useAction<void, any>(() => api(`/alerts/${del!.id}`, { method: 'DELETE', params: { confirm: true } }).then((r) => (setDel(null), r)), [['alerts']])
  const byDay = useMemo(() => {
    const g = new Map<string, Event[]>()
    for (const e of al.data?.history ?? []) {
      const k = new Date(e.triggered_at).toDateString()
      g.set(k, [...(g.get(k) ?? []), e])
    }
    return [...g.values()]
  }, [al.data])
  return (
    <>
      <PageHeader
        title="Alerts"
        lede="Get told in this app when a price, metric, theme or sector does something you care about. Alerts are checked every 15 minutes and after each data refresh, and never place orders."
        actions={<Link to="/watchlists" className="text-[14px] font-medium">Go to watchlists</Link>}
        refresh={<RefreshButton keys={[['alerts']]} hint="Reload alert states and notifications" />}
      />
      <Async q={al} rows={8}>
        {(d) => {
          const active = d.items.filter((a) => a.status === 'active').length
          const weekAgo = al.dataUpdatedAt - 7 * 864e5 // relative to when the history was fetched
          const recent = d.history.filter((h) => new Date(h.triggered_at).getTime() >= weekAgo).length
          const shown = d.items.filter((a) => filter === 'all' || a.status === filter)
          const metaOf = new Map(d.items.map((a) => [a.id, a]))
          return (
            <div className="space-y-5">
              <p className="m-0 text-[14.5px] text-ink-2" data-testid="alerts-summary">
                <strong className="text-ink">{active}</strong> {active === 1 ? 'alert is' : 'alerts are'} watching, <strong className="text-ink">{d.items.length - active}</strong> paused.{' '}
                <strong className="text-ink">{recent}</strong> {recent === 1 ? 'notification' : 'notifications'} in the last 7 days.
              </p>
              <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
                <Builder metrics={metrics} sectors={(sectors.data?.items ?? []).map((s: any) => s.sector)} themes={themeList} />
                <section className="panel rounded-[10px] border border-rule bg-surface" aria-labelledby="your-alerts">
                  <header className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-5 py-3">
                    <h2 id="your-alerts" className="m-0 text-[15.5px] font-semibold">
                      Your alerts
                    </h2>
                    <div className="flex rounded-[8px] border border-rule bg-surface-2 p-0.5 text-[13px]" role="radiogroup" aria-label="Show alerts">
                      {(['all', 'active', 'paused'] as const).map((f) => (
                        <button key={f} type="button" role="radio" aria-checked={filter === f} onClick={() => setFilter(f)} className={cx('rounded-[6px] px-2.5 py-1', filter === f ? 'bg-surface font-semibold text-ink shadow-sm' : 'text-ink-2 hover:text-ink')}>
                          {f === 'all' ? `All ${d.items.length}` : f === 'active' ? `Active ${active}` : `Paused ${d.items.length - active}`}
                        </button>
                      ))}
                    </div>
                  </header>
                  {shown.length === 0 ? (
                    <EmptyState title={d.items.length ? `No ${filter} alerts` : 'No alerts yet'}>
                      {d.items.length ? 'Switch the filter to see the others.' : 'Pick what to watch on the left and finish the sentence. You can also start one from any watchlist row.'}
                    </EmptyState>
                  ) : (
                    <ul className="m-0 list-none p-0">
                      {shown.map((a) => {
                        const label = describe(a, metrics, themeList)
                        const on = a.status === 'active'
                        return (
                          <li key={a.id} className={cx('flex items-start gap-3 border-b border-rule px-5 py-3 last:border-0', !on && 'bg-surface-2/50')}>
                            <Switch on={on} label={`${on ? 'Pause' : 'Resume'}: ${label}`} busy={toggle.isPending} onChange={() => toggle.mutate({ id: a.id, status: on ? 'paused' : 'active' })} />
                            <div className="min-w-0 flex-1">
                              <p className={cx('m-0 text-[14px] font-medium', !on && 'text-ink-2')}>{label}</p>
                              <p className="m-0 mt-0.5 text-[12px] text-ink-2">
                                {KIND_TITLE[a.kind] ?? a.kind}. {on ? '' : 'Paused. '}
                                {a.last_triggered_at ? `Last fired ${dayLabel(a.last_triggered_at).toLowerCase() === 'today' ? 'today' : dayLabel(a.last_triggered_at)} at ${time(a.last_triggered_at)}.` : 'Has not fired yet.'}
                                {a.symbol && (
                                  <>
                                    {' '}
                                    <Link to={instrumentPath(a.symbol)}>Open {a.symbol}</Link>
                                  </>
                                )}
                              </p>
                            </div>
                            <Button variant="quiet" className="min-h-[30px] px-2 text-loss" onClick={() => setDel({ id: a.id, label })} aria-label={`Delete alert: ${label}`}>
                              Delete
                            </Button>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>
              </div>
              <section className="panel rounded-[10px] border border-rule bg-surface" aria-labelledby="alert-history">
                <header className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-5 py-3">
                  <h2 id="alert-history" className="m-0 text-[15.5px] font-semibold">
                    Notification history
                  </h2>
                  <span className="text-xs text-ink-2">Latest 100, newest first</span>
                </header>
                {byDay.length === 0 ? (
                  <EmptyState title="Nothing has fired yet">When an alert's condition is met, it is recorded here with the values that triggered it.</EmptyState>
                ) : (
                  <ol className="m-0 list-none px-5 py-2">
                    {byDay.map((evs) => (
                      <li key={evs[0].triggered_at} className="py-2">
                        <h3 className="m-0 mb-1 text-[13px] font-semibold text-ink-2">{dayLabel(evs[0].triggered_at)}</h3>
                        <ol className="m-0 list-none border-l-2 border-rule p-0 pl-4">
                          {evs.map((h) => {
                            const a = metaOf.get(h.alert_id)
                            return (
                              <li key={h.id} className="relative py-1.5 text-[13.5px]">
                                <span aria-hidden className="absolute -left-[22px] top-[12px] h-2.5 w-2.5 rounded-full border-2 border-surface bg-accent" />
                                <span className="num mr-3 text-[12px] text-ink-2">{time(h.triggered_at)}</span>
                                {h.message}
                                {a && a.status !== 'active' && <span className="ml-2 text-[12px] text-ink-3">(alert now paused)</span>}
                              </li>
                            )
                          })}
                        </ol>
                      </li>
                    ))}
                  </ol>
                )}
                <p className="m-0 border-t border-rule px-5 py-2 text-xs text-ink-2">{d.delivery_note}</p>
              </section>
            </div>
          )
        }}
      </Async>
      <Confirm
        open={del !== null}
        title="Delete this alert?"
        body={<>“{del?.label}” will be removed along with its notification history. This cannot be undone.</>}
        confirmLabel="Delete alert"
        danger
        busy={doDelete.isPending}
        onConfirm={() => doDelete.mutate()}
        onClose={() => setDel(null)}
      />
    </>
  )
}
