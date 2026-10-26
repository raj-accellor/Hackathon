import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, post, useAction, useApi } from '../api/client'
import { InstrumentSearch } from '../components/InstrumentSearch'
import { Async, Button, Confirm, EmptyState, ErrorState, Missing, PageHeader, RefreshButton, StateBadge, cx } from '../components/ui'
import { fmtDate, fmtNum, fmtSigned, signClass } from '../lib/format'
import { instrumentPath } from './Overview'

type Item = {
  item_id: number
  instrument_id: number
  symbol: string
  name: string | null
  sector: string | null
  close: number | null
  return_1w: number | null
  return_1m: number | null
  overall_score: number | null
  partial_score: number | null
  stance: string | null
  risk_label: string | null
  entry_state: string | null
  freshness: string | null
  last_bar: string | null
  added_at: string
}
type WatchList = { id: number; name: string; created_at: string; items: Item[] }
type SortKey = 'symbol' | 'close' | 'return_1w' | 'return_1m' | 'score'

const scoreOf = (it: Item) => it.overall_score ?? it.partial_score ?? null
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)

/** A return as a number plus a bar diverging from zero, scaled to the largest move in the list so rows compare at a glance. */
function MoveBar({ v, max }: { v: number | null; max: number }) {
  if (v == null) return <Missing reason="Not enough price history" />
  const w = Math.min(50, (Math.abs(v) / (max || 1)) * 50)
  return (
    <span className="flex items-center justify-end gap-2">
      <span className={cx('num w-14 text-right', signClass(v))}>{fmtSigned(v)}</span>
      <span aria-hidden className="relative hidden h-[7px] w-14 rounded-sm bg-surface-2 sm:block">
        <span className="absolute inset-y-0 left-1/2 w-px bg-rule-strong" />
        <span className={cx('absolute inset-y-0 rounded-sm', v >= 0 ? 'bg-gain' : 'bg-loss')} style={v >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} />
      </span>
    </span>
  )
}

function ScoreMeter({ it }: { it: Item }) {
  const s = scoreOf(it)
  if (s == null) return <Missing reason="Insufficient data for a score" />
  const partial = it.overall_score == null
  return (
    <span className="flex items-center justify-end gap-2" title={partial ? 'Partial score: one or more pillars lack data' : 'Overall research score, 0 to 100'}>
      <span className="num w-8 text-right">
        {Math.round(s)}
        {partial && <span className="text-ink-3">*</span>}
      </span>
      <span aria-hidden className="hidden h-[5px] w-10 rounded-full bg-surface-2 2xl:block">
        <span className="block h-full rounded-full" style={{ width: `${s}%`, background: 'var(--glyph)', opacity: partial ? 0.5 : 1 }} />
      </span>
    </span>
  )
}

/** One line on how the list moved this week, with a breadth bar. Sentences, not tiles. */
function Pulse({ items }: { items: Item[] }) {
  const wk = items.filter((i) => i.return_1w != null)
  const up = wk.filter((i) => (i.return_1w as number) > 0).length
  const down = wk.filter((i) => (i.return_1w as number) < 0).length
  const flat = wk.length - up - down
  const avg1m = mean(items.map((i) => i.return_1m).filter((v): v is number => v != null))
  const sorted = [...wk].sort((a, b) => (b.return_1w as number) - (a.return_1w as number))
  const best = sorted[0]
  const worst = sorted[sorted.length - 1]
  const stale = items.filter((i) => i.freshness === 'stale').length
  const lastBar = items.map((i) => i.last_bar).filter(Boolean).sort().at(-1)
  if (!wk.length) return null
  return (
    <div className="grid gap-x-8 gap-y-3 border-b border-rule bg-surface-2/60 px-5 py-4 text-[13.5px] md:grid-cols-[minmax(0,1fr)_auto]" data-testid="watchlist-pulse">
      <div className="min-w-0">
        <p className="m-0 text-ink">
          This week <strong className="text-gain">{up} up</strong>, <strong className="text-loss">{down} down</strong>
          {flat > 0 && <>, {flat} flat</>}
          {avg1m != null && (
            <span className="text-ink-2">
              {' '}
              · average month <span className={cx('num font-medium', signClass(avg1m))}>{fmtSigned(avg1m)}</span>
            </span>
          )}
        </p>
        <div className="mt-2 flex h-2 w-full max-w-md overflow-hidden rounded-full bg-surface" role="img" aria-label={`${up} of ${wk.length} instruments rose this week`}>
          <span className="bg-gain" style={{ width: `${(up / wk.length) * 100}%` }} />
          <span className="bg-ink-3/40" style={{ width: `${(flat / wk.length) * 100}%` }} />
          <span className="bg-loss" style={{ width: `${(down / wk.length) * 100}%` }} />
        </div>
      </div>
      <dl className="m-0 flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
        {best && (
          <div>
            <dt className="text-ink-2">Best week</dt>
            <dd className="m-0">
              <Link to={instrumentPath(best.symbol)} className="font-semibold">{best.symbol}</Link> <span className={cx('num', signClass(best.return_1w))}>{fmtSigned(best.return_1w)}</span>
            </dd>
          </div>
        )}
        {worst && worst !== best && (
          <div>
            <dt className="text-ink-2">Worst week</dt>
            <dd className="m-0">
              <Link to={instrumentPath(worst.symbol)} className="font-semibold">{worst.symbol}</Link> <span className={cx('num', signClass(worst.return_1w))}>{fmtSigned(worst.return_1w)}</span>
            </dd>
          </div>
        )}
        <div>
          <dt className="text-ink-2">Prices</dt>
          <dd className="m-0">
            {lastBar ? `to ${fmtDate(lastBar)}` : '—'}
            {stale > 0 && <span className="text-warn">, {stale} stale</span>}
          </dd>
        </div>
      </dl>
    </div>
  )
}

function SortHead({ k, label, sort, setSort, className }: { k: SortKey; label: string; sort: { k: SortKey; dir: 1 | -1 }; setSort: (s: { k: SortKey; dir: 1 | -1 }) => void; className?: string }) {
  const on = sort.k === k
  return (
    <th className={className} aria-sort={on ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className={cx('inline-flex items-center gap-1 font-[inherit] hover:text-ink', on && 'text-ink')} onClick={() => setSort({ k, dir: on ? (sort.dir === 1 ? -1 : 1) : k === 'symbol' ? 1 : -1 })}>
        {label}
        <span aria-hidden className={cx('text-[10px]', !on && 'opacity-0')}>
          {sort.dir === 1 ? '▲' : '▼'}
        </span>
      </button>
    </th>
  )
}

function ListDetail({ w, onDelete }: { w: WatchList; onDelete: () => void }) {
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: 'return_1w', dir: -1 })
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(w.name)
  const addItem = useAction<number, any>((id) => post(`/watchlists/${w.id}/items`, { instrument: String(id) }), [['watchlists']])
  const removeItem = useAction<number, any>((item) => api(`/watchlists/${w.id}/items/${item}`, { method: 'DELETE' }), [['watchlists']])
  const rename = useAction<string, any>((name) => api(`/watchlists/${w.id}`, { method: 'PATCH', body: JSON.stringify({ name }) }).then((r) => (setRenaming(false), r)), [['watchlists']])
  // each column on its own scale: a week's move is not drowned out by a month's
  const max1w = Math.max(0.5, ...w.items.map((i) => Math.abs(i.return_1w ?? 0)))
  const max1m = Math.max(0.5, ...w.items.map((i) => Math.abs(i.return_1m ?? 0)))
  const rows = useMemo(() => {
    const val = (i: Item): number | string | null => (sort.k === 'symbol' ? i.symbol : sort.k === 'score' ? scoreOf(i) : i[sort.k])
    return [...w.items].sort((a, b) => {
      const x = val(a)
      const y = val(b)
      if (x == null) return y == null ? 0 : 1 // missing values always sort last, never as zero
      if (y == null) return -1
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
    })
  }, [w.items, sort])
  return (
    <section className="panel min-w-0 rounded-[10px] border border-rule bg-surface" aria-labelledby={`wl-${w.id}`}>
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule px-5 py-3.5">
        {renaming ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (draft.trim()) rename.mutate(draft.trim())
            }}
          >
            <input className="field w-64" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={80} aria-label="Watchlist name" autoFocus />
            <Button type="submit" variant="primary" busy={rename.isPending} disabled={!draft.trim()}>
              Save name
            </Button>
            <Button type="button" variant="quiet" onClick={() => (setRenaming(false), setDraft(w.name))}>
              Cancel
            </Button>
            {rename.error && <span className="text-[13px] text-loss">{rename.error.message}</span>}
          </form>
        ) : (
          <>
            <h2 id={`wl-${w.id}`} className="m-0 text-[20px] font-semibold tracking-[-0.015em]">
              {w.name}
            </h2>
            <span className="text-[13px] text-ink-2">
              {w.items.length} {w.items.length === 1 ? 'instrument' : 'instruments'}, prices in INR
            </span>
            <span className="ml-auto flex gap-1">
              <Button variant="quiet" onClick={() => (setDraft(w.name), setRenaming(true))}>
                Rename
              </Button>
              <Button variant="danger" onClick={onDelete}>
                Delete list
              </Button>
            </span>
          </>
        )}
      </header>
      <div className="border-b border-rule px-5 py-3">
        <div className="max-w-md">
          <InstrumentSearch equitiesOnly placeholder={`Add an instrument to ${w.name}`} onPick={(inst) => addItem.mutate(inst.id)} />
        </div>
        {addItem.error && <p className="m-0 mt-2 text-[13px] text-loss">{addItem.error.message}</p>}
      </div>
      <Pulse items={w.items} />
      {w.items.length === 0 ? (
        <EmptyState title="This list is empty">Search above to add the first instrument. Its price moves, score and data freshness appear here.</EmptyState>
      ) : (
        <div className="scroll-x">
          <table className="text-[13px]">
            <caption className="sr-only">Instruments in {w.name}. Select a column heading to sort.</caption>
            <thead>
              <tr>
                <SortHead k="symbol" label="Instrument" sort={sort} setSort={setSort} />
                <SortHead k="close" label="Close (₹)" sort={sort} setSort={setSort} className="r" />
                <SortHead k="return_1w" label="1 week" sort={sort} setSort={setSort} className="r" />
                <SortHead k="return_1m" label="1 month" sort={sort} setSort={setSort} className="r" />
                <SortHead k="score" label="Score" sort={sort} setSort={setSort} className="r" />
                <th>Signals</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((it) => (
                <tr key={it.item_id}>
                  <td className="max-w-[200px]">
                    <Link to={instrumentPath(it.symbol)} className="font-semibold">
                      {it.symbol}
                    </Link>
                    <div className="truncate text-xs text-ink-2" title={it.name ?? ''}>
                      {it.name ?? it.sector ?? '—'}
                    </div>
                    {it.freshness === 'stale' && <span className="text-[11.5px] font-medium text-warn">Prices stale since {fmtDate(it.last_bar)}</span>}
                  </td>
                  <td className="r num">{it.close == null ? <Missing reason="No price history ingested" /> : fmtNum(it.close)}</td>
                  <td className="r">
                    <MoveBar v={it.return_1w} max={max1w} />
                  </td>
                  <td className="r">
                    <MoveBar v={it.return_1m} max={max1m} />
                  </td>
                  <td className="r">
                    <ScoreMeter it={it} />
                  </td>
                  <td>
                    {it.stance ? <StateBadge state={it.stance} label={it.stance} /> : <Missing reason="No research stance yet" />}
                    {(it.risk_label || it.entry_state) && (
                      <div className="mt-0.5 max-w-[170px] text-[11.5px] leading-snug text-ink-2">
                        {[it.risk_label && `${it.risk_label} risk`, it.entry_state && `entry ${it.entry_state.toLowerCase()}`].filter(Boolean).join(', ')}
                      </div>
                    )}
                  </td>
                  <td className="r whitespace-nowrap text-[12.5px] leading-[1.7]">
                    <Link to={`/alerts?instrument=${encodeURIComponent(it.symbol)}&id=${it.instrument_id}`} className="block font-medium" aria-label={`Set an alert on ${it.symbol}`}>
                      Set alert
                    </Link>
                    <button type="button" className="text-ink-2 hover:text-loss" onClick={() => removeItem.mutate(it.item_id)} aria-label={`Remove ${it.symbol} from ${w.name}`}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="m-0 border-t border-rule px-5 py-2 text-xs text-ink-2">* Partial score: at least one pillar lacks data. Each bar is scaled to the largest move in its column.</p>
        </div>
      )}
    </section>
  )
}

export function Watchlists() {
  const { id } = useParams()
  const nav = useNavigate()
  const wl = useApi<{ items: WatchList[]; note: string }>(['watchlists'], '/watchlists')
  const [name, setName] = useState('')
  const [del, setDel] = useState<WatchList | null>(null)
  const createList = useAction<void, any>(() => post('/watchlists', { name: name.trim() }).then((r) => (setName(''), nav(`/watchlists/${r.id}`), r)), [['watchlists']])
  const doDelete = useAction<void, any>(() => api(`/watchlists/${del!.id}`, { method: 'DELETE', params: { confirm: true } }).then((r) => (setDel(null), nav('/watchlists'), r)), [['watchlists']])
  return (
    <>
      <PageHeader
        title="Watchlists"
        lede="Keep the instruments you follow in one place, with this week's moves, research scores and how fresh each price is. Nothing here can place an order."
        actions={<Link to="/alerts" className="text-[14px] font-medium">Go to alerts</Link>}
        refresh={<RefreshButton keys={[['watchlists']]} hint="Reload watchlist prices and scores" />}
      />
      <Async q={wl} rows={8}>
        {(d) => {
          const current = d.items.find((w) => String(w.id) === id) ?? d.items[0]
          return (
            <div className="grid items-start gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
              <aside className="lg:sticky lg:top-[112px]">
                <nav aria-label="Your watchlists" className="rounded-[10px] border border-rule bg-surface">
                  {d.items.length > 0 && (
                    <ul className="m-0 list-none p-1.5">
                      {d.items.map((w) => {
                        const avg = mean(w.items.map((i) => i.return_1w).filter((v): v is number => v != null))
                        const on = current?.id === w.id
                        return (
                          <li key={w.id}>
                            <Link
                              to={`/watchlists/${w.id}`}
                              aria-current={on ? 'page' : undefined}
                              className={cx('flex items-center gap-2 rounded-[7px] px-3 py-2 text-[14px] text-ink no-underline', on ? 'bg-accent-soft font-semibold' : 'hover:bg-surface-2')}
                            >
                              <span className="min-w-0 flex-1 truncate">{w.name}</span>
                              {avg != null && <span className={cx('num text-[12px]', signClass(avg))} title="Average 1-week move">{fmtSigned(avg)}</span>}
                              <span className="num min-w-[1.5rem] rounded bg-surface-2 px-1.5 text-center text-[12px] text-ink-2" title="Instruments">
                                {w.items.length}
                              </span>
                            </Link>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                  <form
                    className={cx('space-y-2 p-3', d.items.length > 0 && 'border-t border-rule')}
                    onSubmit={(e) => {
                      e.preventDefault()
                      if (name.trim()) createList.mutate()
                    }}
                  >
                    <label className="block text-[13px]">
                      <span className="mb-1 block font-medium">New watchlist</span>
                      <input className="field w-full" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="For example, Private banks" />
                    </label>
                    <Button type="submit" variant="primary" className="w-full" disabled={!name.trim()} busy={createList.isPending}>
                      Create watchlist
                    </Button>
                    {createList.error && <p className="m-0 text-[13px] text-loss">{createList.error.message}</p>}
                  </form>
                </nav>
                <p className="mt-3 px-1 text-xs leading-relaxed text-ink-2">{d.note}</p>
              </aside>
              {current ? (
                <ListDetail key={current.id} w={current} onDelete={() => setDel(current)} />
              ) : (
                <section className="panel rounded-[10px] border border-rule bg-surface">
                  <EmptyState title="Start your first watchlist">Name a list on the left, then add the instruments you want to follow. You can set alerts on any of them from the list.</EmptyState>
                </section>
              )}
            </div>
          )
        }}
      </Async>
      {doDelete.error && <ErrorState error={doDelete.error} />}
      <Confirm
        open={del !== null}
        title="Delete this watchlist?"
        body={<>“{del?.name}” and its {del?.items.length ?? 0} instruments will be removed from your lists. Alerts you set on those instruments stay. This cannot be undone.</>}
        confirmLabel="Delete watchlist"
        danger
        busy={doDelete.isPending}
        onConfirm={() => doDelete.mutate()}
        onClose={() => setDel(null)}
      />
    </>
  )
}
