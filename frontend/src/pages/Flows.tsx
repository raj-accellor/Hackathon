import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { post, useApi } from '../api/client'
import { Async, Button, EmptyState, Missing, Notice, PageHeader, Panel, RefreshButton, StateBadge, Tabs } from '../components/ui'
import { fmtDate, fmtDateTime, fmtNum, signClass } from '../lib/format'

type Side = { buy: number | null; sell: number | null; net: number | null } | null
type Day = { date: string; fii: Side; dii: Side; combined_net: number | null; retrieved_at: string | null }

const cr = (v: number | null | undefined) => (v == null ? null : `${v > 0 ? '+' : v < 0 ? '−' : ''}₹${fmtNum(Math.abs(v), 2)} cr`)

function Net({ v }: { v: number | null | undefined }) {
  if (v == null) return <Missing />
  return <span className={`num font-semibold ${signClass(v)}`}>{cr(v)}</span>
}

function SideCard({ title, s }: { title: string; s: Side }) {
  return (
    <div className="rounded-[10px] border border-rule bg-surface px-4 py-3">
      <h3 className="text-[13px] font-semibold text-ink-2">{title}</h3>
      <p className="mt-1 font-display text-[24px] leading-tight">
        <Net v={s?.net} />
      </p>
      <p className="mt-1 text-xs text-ink-2">
        {s?.net == null ? 'Not available' : s.net >= 0 ? 'Net buyer' : 'Net seller'} · bought ₹{fmtNum(s?.buy, 2)} cr · sold ₹{fmtNum(s?.sell, 2)} cr
      </p>
    </div>
  )
}

/** Daily net flow as bars from a zero baseline: up = net buying, down = net selling. Oldest on the left. */
/** `points` newest first, as the tables list them; drawn oldest on the left. */
function NetBars({ points, label }: { points: Array<{ date: string; v: number | null | undefined }>; label: string }) {
  const pts = [...points].reverse()
  const vals = pts.map((p) => p.v).filter((v): v is number => v != null)
  if (vals.length === 0) return <p className="px-4 py-3 text-sm text-ink-2">No recorded days for {label}.</p>
  const max = Math.max(...vals.map(Math.abs)) || 1
  const W = 640
  const H = 150
  const pad = 22
  const mid = H / 2
  const slot = (W - 8) / Math.max(pts.length, 1)
  const bw = Math.max(3, Math.min(28, slot - 2))
  return (
    <figure className="m-0 px-3 py-2">
      <figcaption className="text-[13px] font-semibold">{label}: daily net (₹ crore)</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 h-[150px] w-full" role="img" aria-label={`${label} daily net flows, ${pts.length} days; see the table below for values`}>
        <line x1={0} x2={W} y1={mid} y2={mid} stroke="var(--rule-strong)" strokeWidth={1} />
        <text x={2} y={12} fontSize={10} fill="var(--ink-3)">
          +{fmtNum(max, 0)}
        </text>
        <text x={2} y={H - 3} fontSize={10} fill="var(--ink-3)">
          −{fmtNum(max, 0)}
        </text>
        {pts.map((p, i) => {
          if (p.v == null) return null
          const h = (Math.abs(p.v) / max) * (mid - pad / 2)
          const x = 4 + i * slot + (slot - bw) / 2
          const up = p.v >= 0
          const r = Math.min(4, bw / 2, h)
          // rounded at the data end, square at the baseline
          const d = up
            ? `M${x},${mid} V${mid - h + r} Q${x},${mid - h} ${x + r},${mid - h} H${x + bw - r} Q${x + bw},${mid - h} ${x + bw},${mid - h + r} V${mid} Z`
            : `M${x},${mid} V${mid + h - r} Q${x},${mid + h} ${x + r},${mid + h} H${x + bw - r} Q${x + bw},${mid + h} ${x + bw},${mid + h - r} V${mid} Z`
          return (
            <g key={p.date} tabIndex={0} aria-label={`${fmtDate(p.date)}: ${cr(p.v)}`}>
              <title>{`${fmtDate(p.date)}: ${cr(p.v)}`}</title>
              <rect x={x - 1} y={pad / 2} width={bw + 2} height={H - pad} fill="transparent" />
              <path d={d} fill={up ? 'var(--gain)' : 'var(--loss)'} />
            </g>
          )
        })}
      </svg>
      <p className="text-[11px] text-ink-3">Above the line: net buying. Below: net selling. Hover or focus a bar for its value.</p>
    </figure>
  )
}

function ParticipantOi() {
  const [on, setOn] = useState('')
  const [today] = useState(() => new Date().toISOString().slice(0, 10))
  const q = useApi<any>(['participant-oi'], '/flows/participant-oi', { on: on || undefined })
  const cols: Array<[string, string]> = [
    ['future_index_net', 'Index futures net'],
    ['future_stock_net', 'Stock futures net'],
    ['option_index_call_net', 'Index calls net'],
    ['option_index_put_net', 'Index puts net'],
  ]
  return (
    <Panel
      title="Derivatives positioning by participant"
      aside={
        <label className="flex items-center gap-2">
          Trading day
          <input type="date" value={on} max={today} onChange={(e) => setOn(e.target.value)} className="rounded border border-rule-strong bg-surface px-2 py-1 text-sm" />
          {on && (
            <button type="button" className="text-link" onClick={() => setOn('')}>
              Latest
            </button>
          )}
        </label>
      }
    >
      <Async q={q} rows={5}>
        {(d) =>
          d.state !== 'ok' ? (
            <div className="p-4">
              <Notice tone="warn" title="Participant open interest is not available">
                {d.reason}
              </Notice>
            </div>
          ) : (
            <>
              <p className="px-4 pt-3 text-xs text-ink-2">
                Open contracts at the close of {fmtDate(d.date)}
                {d.requested !== d.date && ` (latest published on or before ${fmtDate(d.requested)})`}. Change against {fmtDate(d.previous_date)}. Net = long − short. Source: {d.source}.
              </p>
              <div className="scroll-x">
                <table>
                  <thead>
                    <tr>
                      <th>Participant</th>
                      {cols.map(([, l]) => (
                        <th key={l} className="r">
                          {l}
                        </th>
                      ))}
                      <th className="r" title="Index futures long contracts divided by short contracts">
                        Index futures long/short
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.participants
                      .filter((p: any) => p.participant !== 'TOTAL')
                      .map((p: any) => (
                        <tr key={p.participant}>
                          <td className="font-semibold">{p.participant}</td>
                          {cols.map(([k]) => (
                            <td key={k} className="r num">
                              <span className={signClass(p[k])}>{fmtNum(p[k], 0)}</span>
                              {p[`${k}_change`] != null && (
                                <div className={`text-[11px] ${signClass(p[`${k}_change`])}`}>
                                  {p[`${k}_change`] > 0 ? '+' : ''}
                                  {fmtNum(p[`${k}_change`], 0)}
                                </div>
                              )}
                            </td>
                          ))}
                          <td className="r num">{fmtNum(p.index_futures_long_short_ratio, 2)}</td>
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

const PRESETS = [
  { id: '30', label: '1 month' },
  { id: '90', label: '3 months' },
  { id: '182', label: '6 months' },
  { id: '365', label: '1 year' },
  { id: 'custom', label: 'Custom' },
]

const iso = (d: Date) => d.toISOString().slice(0, 10)

function History() {
  const qc = useQueryClient()
  const [today] = useState(() => iso(new Date()))
  const [preset, setPreset] = useState('90')
  const [from, setFrom] = useState(() => iso(new Date(Date.now() - 90 * 864e5)))
  const [to, setTo] = useState(today)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'good' | 'warn'; text: string } | null>(null)
  const pick = (id: string) => {
    setPreset(id)
    if (id !== 'custom') {
      setFrom(iso(new Date(Date.now() - Number(id) * 864e5)))
      setTo(today)
    }
  }
  const params = { start: from, end: to }
  const q = useApi<any>(['fii-dii-history'], '/flows/history', params)
  const refresh = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await post<any>('/flows/history/refresh', { start: from, end: to })
      qc.setQueryData(['fii-dii-history', params], r)
      await qc.invalidateQueries({ queryKey: ['fii-dii'] })
      const f = r.refresh
      setMsg(
        f.state === 'ok'
          ? { tone: f.failed?.length ? 'warn' : 'good', text: `Fetched ${f.fpi_days} NSDL days for ${f.months.join(', ')}${f.failed?.length ? `; failed: ${f.failed.map((x: any) => `${x.month} (${x.reason})`).join('; ')}` : ''}. NSE latest day: ${f.nse_latest?.trade_date ?? f.nse_latest?.reason ?? 'n/a'}.` }
          : { tone: 'warn', text: f.reason ?? f.failed?.[0]?.reason ?? 'The sources could not be reached.' },
      )
    } catch (e) {
      setMsg({ tone: 'warn', text: e instanceof Error ? e.message : 'Refresh failed' })
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title="Historical FPI / DII data" id="history">
      <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
        <Tabs tabs={PRESETS} value={preset} onChange={pick} label="History range" />
        <label className="flex flex-col text-xs text-ink-2">
          From
          <input type="date" value={from} max={to} onChange={(e) => { setPreset('custom'); setFrom(e.target.value) }} className="mt-0.5 rounded border border-rule-strong bg-surface px-2 py-1 text-sm text-ink" />
        </label>
        <label className="flex flex-col text-xs text-ink-2">
          To
          <input type="date" value={to} min={from} max={today} onChange={(e) => { setPreset('custom'); setTo(e.target.value) }} className="mt-0.5 rounded border border-rule-strong bg-surface px-2 py-1 text-sm text-ink" />
        </label>
        <Button variant="primary" onClick={refresh} busy={busy} data-testid="history-refresh" title="Fetch this range again from NSDL and NSE and store it">
          Refresh historical data
        </Button>
      </div>
      {msg && (
        <div className="px-4 pt-3">
          <Notice tone={msg.tone} title={msg.tone === 'good' ? 'Historical data updated' : 'Refresh finished with problems'}>
            {msg.text}
          </Notice>
        </div>
      )}
      <Async q={q} rows={8}>
        {(d) => {
          const rows: any[] = d.rows ?? []
          const fpiPts = rows.filter((r) => r.fpi).map((r) => ({ date: r.date, v: r.fpi.net }))
          const diiPts = rows.filter((r) => r.nse?.dii).map((r) => ({ date: r.date, v: r.nse.dii.net }))
          return (
            <>
              <div className="flex flex-wrap gap-x-6 gap-y-1 px-4 pt-3 text-sm">
                <span>
                  FPI equity via exchanges (NSDL), {d.coverage.fpi_days} days: <Net v={d.totals.fpi_equity_exchange_net} />
                </span>
                <span>
                  FPI equity incl. primary market: <Net v={d.totals.fpi_equity_net} />
                </span>
                <span>
                  DII (NSE, {d.coverage.nse_days} recorded days): <Net v={d.totals.nse_dii_net} />
                </span>
                <span className="text-ink-2">
                  {fmtDate(d.start)} to {fmtDate(d.end)}
                  {d.coverage.last_fetched_at && ` · last fetched ${fmtDateTime(d.coverage.last_fetched_at)}`}
                </span>
              </div>
              {rows.length === 0 ? (
                <EmptyState title="No historical data stored for this range" action={<Button onClick={refresh} busy={busy}>Refresh historical data</Button>}>
                  Click Refresh historical data to fetch it from NSDL and NSE.
                </EmptyState>
              ) : (
                <>
                  <div className="grid lg:grid-cols-2">
                    <NetBars points={fpiPts} label="FPI equity via exchanges (NSDL)" />
                    {diiPts.length > 1 ? <NetBars points={diiPts} label="DII (NSE provisional)" /> : (
                      <p className="self-center px-4 py-3 text-sm text-ink-2">DII history: {diiPts.length} recorded day{diiPts.length === 1 ? '' : 's'}. No public source publishes past DII figures, so this grows with each trading evening's recording.</p>
                    )}
                  </div>
                  <div className="scroll-x max-h-[560px] overflow-y-auto">
                    <table>
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th className="r" title="NSDL: FPI equity gross purchases through stock exchanges">FPI buy</th>
                          <th className="r">FPI sell</th>
                          <th className="r">FPI net</th>
                          <th className="r" title="Equity including primary market and others">FPI equity net</th>
                          <th className="r" title="NSE provisional FII/FPI net">FII net (NSE)</th>
                          <th className="r" title="NSE provisional DII net">DII net (NSE)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r) => (
                          <tr key={r.date}>
                            <td className="whitespace-nowrap font-medium">{fmtDate(r.date)}</td>
                            <td className="r num">{fmtNum(r.fpi?.buy, 2)}</td>
                            <td className="r num">{fmtNum(r.fpi?.sell, 2)}</td>
                            <td className="r whitespace-nowrap"><Net v={r.fpi?.net} /></td>
                            <td className="r whitespace-nowrap"><Net v={r.fpi?.equity_net} /></td>
                            <td className="r whitespace-nowrap"><Net v={r.nse?.fii?.net} /></td>
                            <td className="r whitespace-nowrap"><Net v={r.nse?.dii?.net} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              <ul className="m-0 list-disc space-y-0.5 border-t border-rule py-2 pl-8 pr-4 text-[11.5px] text-ink-2">
                {d.notes.map((n: string) => (
                  <li key={n}>{n}</li>
                ))}
                <li>Values in {d.unit}. Sources: {d.sources.fpi}; {d.sources.nse}.</li>
              </ul>
            </>
          )
        }}
      </Async>
    </Panel>
  )
}

export function Flows() {
  const q = useApi<any>(['fii-dii'], '/flows/fii-dii', { days: 30 })
  return (
    <>
      <PageHeader
        title="FII / DII flows"
        lede="What foreign (FII/FPI) and domestic (DII) institutions bought and sold in the cash market: the latest trading day, the history for any date range, and their derivatives positions."
        actions={<a href="#history" className="text-sm">Historical data ↓</a>}
        refresh={<RefreshButton keys={[['fii-dii'], ['fii-dii-history'], ['participant-oi']]} hint="Fetch NSE's latest published figures" />}
      />
      <Async q={q} rows={6}>
        {(d) => {
          const latest: Day | null = d.latest
          return (
            <div className="space-y-5">
              {d.latest_state.state !== 'ok' && (
                <Notice tone="warn" title="Latest figures could not be fetched">
                  {d.latest_state.reason} Stored history is shown below.
                </Notice>
              )}
              <Panel
                title={latest ? `Latest trading day: ${fmtDate(latest.date)}` : 'Latest trading day'}
                aside={latest?.retrieved_at && <span>Retrieved {fmtDateTime(latest.retrieved_at)} · provisional · {d.unit} · <StateBadge state={d.latest_state.state} /></span>}
              >
                {latest ? (
                  <div className="grid gap-3 p-3 md:grid-cols-3">
                    <SideCard title="FII / FPI" s={latest.fii} />
                    <SideCard title="DII" s={latest.dii} />
                    <div className="rounded-[10px] border border-rule bg-surface px-4 py-3">
                      <h3 className="text-[13px] font-semibold text-ink-2">Combined net</h3>
                      <p className="mt-1 font-display text-[24px] leading-tight">
                        <Net v={latest.combined_net} />
                      </p>
                      <p className="mt-1 text-xs text-ink-2">FII/FPI plus DII, cash market, NSE + BSE + MSEI.</p>
                    </div>
                  </div>
                ) : (
                  <EmptyState title="No FII/DII figures recorded yet">{d.latest_state.reason}</EmptyState>
                )}
              </Panel>
              <History />
              <ParticipantOi />
            </div>
          )
        }}
      </Async>
    </>
  )
}
