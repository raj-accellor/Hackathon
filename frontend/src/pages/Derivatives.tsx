import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../api/client'
import { Async, Badge, EmptyState, Missing, Notice, PageHeader, Panel, RefreshButton, Tabs } from '../components/ui'
import { ago, fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'
import { instrumentPath } from './Overview'

const VIEWS = [
  { id: 'gainers', label: 'Top gainers' },
  { id: 'losers', label: 'Top losers' },
  { id: 'oi_gainers', label: 'OI gainers' },
  { id: 'oi_losers', label: 'OI losers' },
  { id: 'long_buildup', label: 'Long buildup' },
  { id: 'short_covering', label: 'Short covering' },
  { id: 'short_buildup', label: 'Short buildup' },
  { id: 'long_unwinding', label: 'Long unwinding' },
]

const BUILDUP_HELP: Record<string, string> = {
  long_buildup: 'Price up and open interest up: new long positions.',
  short_covering: 'Price up and open interest down: shorts closing positions.',
  short_buildup: 'Price down and open interest up: new short positions.',
  long_unwinding: 'Price down and open interest down: longs closing positions.',
}
const BUILDUP_TONE: Record<string, string> = { long_buildup: 'good', short_covering: 'info', short_buildup: 'bad', long_unwinding: 'warn' }

const SEGMENTS = [
  { id: 'all', label: 'All stocks' },
  { id: 'nifty', label: 'Nifty 50' },
  { id: 'banknifty', label: 'Nifty Bank' },
  { id: 'niftynext50', label: 'Nifty Next 50' },
]

function Sym({ s }: { s: string }) {
  return (
    <Link to={instrumentPath(s)} className="font-semibold">
      {s}
    </Link>
  )
}

function CashMovers({ kind }: { kind: 'gainers' | 'losers' }) {
  const [segment, setSegment] = useState('all')
  const q = useApi<any>(['movers'], '/derivatives/movers', { segment, limit: 25 }, { refetchInterval: 120_000 })
  return (
    <Panel
      title={kind === 'gainers' ? 'Top gainers today (cash market)' : 'Top losers today (cash market)'}
      aside={
        <label className="flex items-center gap-2">
          Segment
          <select value={segment} onChange={(e) => setSegment(e.target.value)} className="rounded border border-rule-strong bg-surface px-2 py-1 text-sm">
            {SEGMENTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      }
    >
      <Async q={q} rows={10}>
        {(d) =>
          d.state !== 'delayed' && d.state !== 'live' ? (
            <div className="space-y-3 p-4">
              <Notice tone="warn" title="Live movers are not available">
                {d.reason} Showing the end-of-day F&O stock futures {kind} instead.
              </Notice>
              <EodMovers kind={kind} />
            </div>
          ) : (
            <>
              {d.state === 'live' && (
                <div className="px-4 pt-3">
                  <Notice tone="info" title="Live prices from Zerodha Kite">
                    NSE's live movers could not be fetched ({d.fallback_reason}), so these are ranked from Zerodha Kite quotes over {d.universe}.
                  </Notice>
                </div>
              )}
              <p className="px-4 pt-3 text-xs text-ink-2">
                {d.state === 'live' ? 'Quoted' : 'Updated by NSE'} {d.as_of ? fmtDateTime(d.as_of) : 'at an unstated time'}
                {d.state === 'live' ? ' (live)' : ' (delayed)'} · retrieved {ago(d.retrieved_at)} · {d.source}
              </p>
              <div className="scroll-x">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Stock</th>
                      <th className="r">Price (₹)</th>
                      <th className="r">Previous close (₹)</th>
                      <th className="r">Change</th>
                      <th className="r">Volume</th>
                      <th className="r">Turnover (₹ lakh)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d[kind] as any[]).map((r, i) => (
                      <tr key={r.symbol}>
                        <td className="text-ink-3">{i + 1}</td>
                        <td>
                          <Sym s={r.symbol} />
                        </td>
                        <td className="r num">{fmtNum(r.ltp)}</td>
                        <td className="r num">{fmtNum(r.prev_close)}</td>
                        <td className={`r num font-medium ${signClass(r.change_pct)}`}>{fmtSigned(r.change_pct, 2)}</td>
                        <td className="r num">{fmtNum(r.volume, 0)}</td>
                        <td className="r num">{fmtNum(r.turnover_lakh, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </Async>
    </Panel>
  )
}

/** End-of-day fallback: stock futures ranked by the day's price change, from the F&O bhavcopy. */
function EodMovers({ kind }: { kind: 'gainers' | 'losers' }) {
  const q = useApi<any>(['oi'], '/derivatives/oi', { limit: 25 })
  return (
    <Async q={q} rows={6}>
      {(d) =>
        d.state !== 'ok' ? (
          <p className="text-sm text-ink-2">End-of-day F&O data is not available either: {d.reason}</p>
        ) : (
          <>
            <p className="text-xs text-ink-2">{d.mode === 'live' ? `Live ${fmtDate(d.date)}` : `End of day ${fmtDate(d.date)}`} · {d.source}</p>
            <FuturesTable rows={kind === 'gainers' ? d.price_gainers : d.price_losers} empty={`No ${kind}`} />
          </>
        )
      }
    </Async>
  )
}

function FuturesTable({ rows, empty }: { rows: any[]; empty: string }) {
  if (rows.length === 0) return <EmptyState title={empty} />
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Stock</th>
            <th className="r">Futures price (₹)</th>
            <th className="r">Price change</th>
            <th className="r">Open interest</th>
            <th className="r">OI change</th>
            <th className="r">OI change %</th>
            <th className="r">Contracts traded</th>
            <th>Reading</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.symbol}>
              <td className="text-ink-3">{i + 1}</td>
              <td>
                <Sym s={r.symbol} />
                <div className="text-[11px] text-ink-3">near expiry {fmtDate(r.near_expiry)}</div>
              </td>
              <td className="r num">{fmtNum(r.close)}</td>
              <td className={`r num ${signClass(r.price_change_pct)}`}>{fmtSigned(r.price_change_pct, 2)}</td>
              <td className="r num">{fmtNum(r.open_interest, 0)}</td>
              <td className={`r num ${signClass(r.oi_change)}`}>
                {r.oi_change > 0 ? '+' : ''}
                {fmtNum(r.oi_change, 0)}
              </td>
              <td className={`r num font-medium ${signClass(r.oi_change_pct)}`}>{fmtSigned(r.oi_change_pct, 2)}</td>
              <td className="r num">{fmtNum(r.volume_contracts, 0)}</td>
              <td>{r.buildup ? <Badge tone={BUILDUP_TONE[r.buildup]}>{VIEWS.find((v) => v.id === r.buildup)?.label}</Badge> : <Missing reason="Price or OI unchanged" />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OiView({ view }: { view: string }) {
  const [on, setOn] = useState('')
  const [today] = useState(() => new Date().toISOString().slice(0, 10))
  const q = useApi<any>(['oi'], '/derivatives/oi', { on: on || undefined, limit: 25 })
  return (
    <Async q={q} rows={10}>
      {(d) =>
        d.state !== 'ok' ? (
          <Notice tone="warn" title="F&O data is not available">
            {d.reason}
          </Notice>
        ) : (
          <div className="space-y-4">
            {d.mode === 'live' ? (
              <Notice tone="info" title={`Live today from Zerodha Kite (as of ${fmtDateTime(d.as_of)})`}>
                NSE has not published today's F&O file yet, so open interest and prices are live Kite quotes for {d.live_state?.quoted} futures
                contracts, compared with NSE's end-of-day file of {fmtDate(d.previous_file)}.
              </Notice>
            ) : d.live_state && d.live_state.state !== 'ok' ? (
              <Notice tone="warn" title="Showing the last published end-of-day file">
                Live figures from Zerodha Kite were not available: {d.live_state.reason}
              </Notice>
            ) : null}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-ink-2">
              <span>
                {d.mode === 'live' ? `Live ${fmtDate(d.date)}` : `End of day ${fmtDate(d.date)}`}
                {d.mode !== 'live' && d.requested !== d.date && ` (latest published on or before ${fmtDate(d.requested)})`} · {d.source} ·{' '}
                {d.underlyings} stock futures
              </span>
              <label className="flex items-center gap-2">
                Trading day
                <input type="date" value={on} max={today} onChange={(e) => setOn(e.target.value)} className="rounded border border-rule-strong bg-surface px-2 py-1 text-sm" />
                {on && (
                  <button type="button" className="text-link" onClick={() => setOn('')}>
                    Latest
                  </button>
                )}
              </label>
            </div>
            <ul className="m-0 grid list-none gap-2 p-0 sm:grid-cols-2 lg:grid-cols-4" aria-label="Buildup counts">
              {Object.entries(d.buildup_counts as Record<string, number>).map(([k, n]) => (
                <li key={k} className="rounded-[8px] border border-rule bg-surface px-3 py-2">
                  <div className="flex items-center justify-between">
                    <Badge tone={BUILDUP_TONE[k]}>{d.buildup_labels[k]}</Badge>
                    <span className="num text-lg font-semibold">{n}</span>
                  </div>
                  <p className="mt-1 text-[11.5px] text-ink-2">{BUILDUP_HELP[k]}</p>
                </li>
              ))}
            </ul>
            <Panel title={VIEWS.find((v) => v.id === view)?.label}>
              {view === 'oi_gainers' && <FuturesTable rows={d.oi_gainers} empty="No open-interest gainers" />}
              {view === 'oi_losers' && <FuturesTable rows={d.oi_losers} empty="No open-interest losers" />}
              {d.buildups[view] && (
                <>
                  <p className="px-4 pt-3 text-xs text-ink-2">{BUILDUP_HELP[view]} Strongest open-interest change first.</p>
                  <FuturesTable rows={d.buildups[view]} empty={`No stocks in ${d.buildup_labels[view].toLowerCase()} on this day`} />
                </>
              )}
            </Panel>
            <Panel title="Index futures">
              <FuturesTable rows={d.index_futures} empty="No index futures" />
            </Panel>
            <details className="rounded-[10px] border border-rule bg-surface px-4 py-3 text-[12.5px] text-ink-2">
              <summary className="cursor-pointer font-medium text-ink">How this is calculated</summary>
              <pre className="mt-2 whitespace-pre-wrap font-sans">{d.method}</pre>
            </details>
          </div>
        )
      }
    </Async>
  )
}

export function Derivatives() {
  const [view, setView] = useState('gainers')
  return (
    <>
      <PageHeader
        title="F&O and open interest"
        lede="Today's top gainers and losers, and from NSE's end-of-day F&O file: open-interest gainers and losers, long and short buildup, short covering and long unwinding."
        refresh={<RefreshButton keys={[['movers'], ['oi']]} hint="Reload movers and the latest F&O file" />}
      />
      <div className="mb-4">
        <Tabs tabs={VIEWS} value={view} onChange={setView} label="Movers and open-interest views" />
      </div>
      {view === 'gainers' || view === 'losers' ? <CashMovers kind={view} /> : <OiView view={view} />}
      <p className="mt-4 text-[11.5px] text-ink-2">
        Readings follow the usual price and open-interest convention. They describe positioning on one day and do not predict what happens next.
      </p>
    </>
  )
}
