import { useApi } from '../api/client'
import { Sparkline } from '../components/Charts'
import { Async, Badge, Missing, Notice, PageHeader, Panel, RefreshButton } from '../components/ui'
import { fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'

type Indicator = {
  key: string
  label: string
  group: string
  unit: string
  impact: string
  source: string
  state: string
  reason?: string
  value: number | null
  prev_value?: number | null
  change_unit?: 'pct' | 'bps'
  change_pct?: number | null
  change_bps?: number | null
  as_of?: string | null
  freshness?: string
  period?: string
  note?: string
  year_high?: number | null
  year_low?: number | null
  history?: Array<{ date: string; value: number }>
}

const FRESH: Record<string, { tone: string; label: string }> = {
  fresh: { tone: 'good', label: 'Current' },
  latest_release: { tone: 'good', label: 'Latest release' },
  stale: { tone: 'warn', label: 'Stale' },
  unknown: { tone: 'neutral', label: 'Time unknown' },
}

function digits(v: number) {
  return Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 10 ? 2 : 3
}

function Card({ i }: { i: Indicator }) {
  const chg = i.change_unit === 'bps' ? i.change_bps : i.change_pct
  const f = FRESH[i.freshness ?? 'unknown'] ?? FRESH.unknown
  const monthly = i.period === 'monthly'
  return (
    <article className="flex flex-col rounded-[10px] border border-rule bg-surface px-4 py-3" data-testid={`macro-${i.key}`}>
      <header className="flex items-start justify-between gap-2">
        <h3 className="text-[14px] font-semibold leading-snug">{i.label}</h3>
        {i.value != null && <Badge tone={f.tone}>{f.label}</Badge>}
      </header>
      {i.value == null ? (
        <p className="mt-2 text-sm text-ink-2">
          <Missing reason={i.reason} status={i.state} /> <span className="text-xs">{i.reason}</span>
        </p>
      ) : (
        <>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
            <span className="num font-display text-[24px] font-semibold leading-tight">{fmtNum(i.value, digits(i.value))}</span>
            <span className="text-xs text-ink-2">{i.unit}</span>
          </div>
          <p className={`num text-[13px] font-medium ${signClass(chg)}`}>
            {chg == null ? (
              <Missing reason="No previous value" />
            ) : i.change_unit === 'bps' ? (
              `${chg > 0 ? '▲' : chg < 0 ? '▼' : '■'} ${fmtNum(Math.abs(chg), 0)} bps`
            ) : (
              fmtSigned(chg, 2)
            )}
            <span className="ml-1 font-normal text-ink-3">vs {monthly ? 'previous print' : 'previous close'}</span>
          </p>
          {(i.history?.length ?? 0) > 2 && (
            <div className="mt-1">
              <Sparkline values={i.history!.map((h) => h.value)} width={200} height={34} label={`${i.label}, ${i.history!.length} observations from ${i.history![0].date}`} />
            </div>
          )}
          <p className="mt-1 text-[11px] text-ink-3">
            {monthly ? `Period ${fmtDate(i.as_of)}` : `As of ${fmtDateTime(i.as_of)}`} · {i.source}
          </p>
          {i.note && <p className="mt-1 text-[11px] text-ink-2">{i.note}</p>}
        </>
      )}
      <p className="mt-2 border-t border-rule pt-2 text-[12px] leading-relaxed text-ink-2">
        <span className="font-medium text-ink">Why it matters: </span>
        {i.impact}
      </p>
    </article>
  )
}

const STANCE: Record<string, { tone: string; mark: string; box: string }> = {
  Risky: { tone: 'bad', mark: '▲', box: 'border-loss/40 bg-danger-soft' },
  Neutral: { tone: 'neutral', mark: '■', box: 'border-rule bg-surface' },
  Safe: { tone: 'good', mark: '●', box: 'border-gain/40 bg-good-soft' },
  'Insufficient data': { tone: 'warn', mark: '○', box: 'border-rule bg-surface' },
}

function Interpretation({ i }: { i: any }) {
  const st = STANCE[i.stance] ?? STANCE.Neutral
  return (
    <section aria-label="Macro interpretation" className={`rounded-[10px] border px-4 py-4 ${st.box}`} data-testid="macro-interpretation">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-2">Macro reading for Indian equities</h2>
        <Badge tone={st.tone}>
          <span aria-hidden>{st.mark}</span> {i.stance}
        </Badge>
        <span className="num text-sm text-ink-2">
          Net score {i.net_score > 0 ? '+' : ''}
          {i.net_score} from {i.signals_read} signals ({i.risk_off} risky, {i.risk_on} supportive, {i.neutral} neutral)
        </span>
      </div>
      <p className="mt-2 font-display text-[20px] font-semibold leading-snug">{i.summary}</p>
      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold text-loss">Pointing to risk</h3>
          {i.drivers.risky.length ? (
            <ul className="m-0 mt-1 list-disc pl-5 text-[13px]">{i.drivers.risky.map((d: string) => <li key={d}>{d}</li>)}</ul>
          ) : (
            <p className="mt-1 text-[13px] text-ink-2">None</p>
          )}
        </div>
        <div>
          <h3 className="text-xs font-semibold text-gain">Pointing to safety</h3>
          {i.drivers.safe.length ? (
            <ul className="m-0 mt-1 list-disc pl-5 text-[13px]">{i.drivers.safe.map((d: string) => <li key={d}>{d}</li>)}</ul>
          ) : (
            <p className="mt-1 text-[13px] text-ink-2">None</p>
          )}
        </div>
      </div>
      <details className="mt-3 text-[13px]">
        <summary className="cursor-pointer font-medium text-link">How this was decided: every signal and its rule</summary>
        <div className="scroll-x mt-2">
          <table>
            <thead>
              <tr>
                <th>Signal</th>
                <th className="r">Reading</th>
                <th>Effect</th>
                <th>Rule</th>
              </tr>
            </thead>
            <tbody>
              {i.signals.map((g: any) => (
                <tr key={g.label}>
                  <td className="font-medium">{g.label}</td>
                  <td className="r num whitespace-nowrap">{fmtNum(g.value, Math.abs(g.value) >= 100 ? 0 : 2)}{g.unit && ` ${g.unit}`}</td>
                  <td>
                    <Badge tone={g.score > 0 ? 'bad' : g.score < 0 ? 'good' : 'neutral'}>{g.score > 0 ? 'Risky' : g.score < 0 ? 'Supportive' : 'Neutral'}</Badge>
                  </td>
                  <td className="text-ink-2">{g.rule}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {i.skipped.length > 0 && <p className="mt-2 text-ink-2">Not used: {i.skipped.join('; ')}.</p>}
        <p className="mt-1 text-ink-2">{i.method}</p>
      </details>
    </section>
  )
}

export function Macro() {
  const q = useApi<any>(['macro'], '/macro', undefined, { refetchInterval: 300_000 })
  return (
    <>
      <PageHeader
        title="Macro"
        lede="Crude oil, the dollar, the rupee, yields, inflation, volatility and global markets: the outside forces that move Indian equities, each with its source and time."
        refresh={<RefreshButton keys={[['macro']]} hint="Reload every indicator from its source" />}
      />
      <Async q={q} rows={10}>
        {(d) => (
          <div className="space-y-5">
            {d.interpretation && <Interpretation i={d.interpretation} />}
            <p className="text-xs text-ink-2">
              {d.coverage.available} of {d.coverage.total} indicators available. {d.notes.join(' ')}
            </p>
            {d.coverage.available === 0 && <Notice tone="warn" title="No macro data">{d.groups[0]?.indicators[0]?.reason}</Notice>}
            {d.groups.map((g: any) => (
              <Panel key={g.group} title={g.group}>
                <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3">
                  {g.indicators.map((i: Indicator) => (
                    <Card key={i.key} i={i} />
                  ))}
                </div>
              </Panel>
            ))}
            <Panel title="Sources">
              <table>
                <tbody>
                  {d.sources.map((s: any) => (
                    <tr key={s.name}>
                      <td className="font-medium">{s.name}</td>
                      <td>{s.state === 'ok' ? 'Configured' : <Missing reason={s.reason} status={s.state} />}</td>
                      <td className="text-ink-2">{s.reason ?? s.base_url}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          </div>
        )}
      </Async>
    </>
  )
}
