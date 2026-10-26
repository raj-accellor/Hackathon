import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, useApi } from '../api/client'
import { Async, Badge, Button, EmptyState, Missing, Notice, PageHeader, Panel, StateBadge, Tabs, cx } from '../components/ui'
import { ago, fmtDate, fmtDateTime, fmtNum, fmtSigned, signClass } from '../lib/format'
import { instrumentPath } from './Overview'

type NewsItem = {
  id: string
  kind: 'exchange_filing' | 'news'
  source: string
  source_type: string
  headline: string
  category: string
  summary: string | null
  url: string | null
  published_at: string | null
  symbols: string[]
  tone?: { tone: number; reason: string | null }
}

type Stock = {
  symbol: string
  name: string | null
  sector: string | null
  in_universe: boolean
  news_ids: string[]
  categories: string[]
  first_news_at: string | null
  latest_news_at: string | null
  last_price: number | null
  day_change_pct: number | null
  quote_as_of: string | null
  quote_basis: string | null
  ref_price: number | null
  ref_date: string | null
  ref_basis?: string
  move_pct: number | null
  move_reason?: string
  sentiment?: Sentiment
}

type Sentiment = { label: 'Bullish' | 'Bearish' | 'Neutral'; score: number; confidence: string; basis: string[] }

const SENT: Record<string, { tone: string; mark: string }> = {
  Bullish: { tone: 'good', mark: '▲' },
  Bearish: { tone: 'bad', mark: '▼' },
  Neutral: { tone: 'neutral', mark: '■' },
}

function SentimentBadge({ s }: { s?: Sentiment }) {
  if (!s) return <Missing />
  const t = SENT[s.label]
  return (
    <span title={`${s.label} (${s.confidence} confidence): ${s.basis.join('; ')}`} className="inline-flex cursor-help items-center gap-1" data-testid="stock-sentiment">
      <Badge tone={t.tone}>
        <span aria-hidden>{t.mark}</span> {s.label}
      </Badge>
      <span className="text-[10.5px] text-ink-3">{s.confidence}</span>
      <span className="sr-only">{s.basis.join('; ')}</span>
    </span>
  )
}

const WINDOWS = [
  { id: '1', label: 'Today' },
  { id: '3', label: 'Last 3 days' },
  { id: '7', label: 'Last 7 days' },
]

function when(iso: string | null) {
  if (!iso) return 'Time not supplied'
  return iso.length <= 10 ? fmtDate(iso) : fmtDateTime(iso)
}

function NewsRow({ n, open, onToggle, onPick, picked }: { n: NewsItem; open: boolean; onToggle: () => void; onPick: (s: string) => void; picked: string | null }) {
  const panelId = `news-${n.id}`
  return (
    <li className="border-b border-rule px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
        <Badge tone={n.kind === 'exchange_filing' ? 'info' : 'neutral'}>{n.kind === 'exchange_filing' ? 'NSE filing' : 'News'}</Badge>
        <span>{n.category}</span>
        {n.tone && n.tone.tone !== 0 && (
          <span className={n.tone.tone > 0 ? 'text-gain' : 'text-loss'} title={`Reads ${n.tone.tone > 0 ? 'positive' : 'negative'}: ${n.tone.reason}`}>
            {n.tone.tone > 0 ? '▲ positive' : '▼ negative'}
          </span>
        )}
        <span aria-hidden>·</span>
        <time dateTime={n.published_at ?? undefined}>{when(n.published_at)}</time>
      </div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
        className="mt-1 flex w-full items-start justify-between gap-3 text-left text-[14.5px] font-medium text-ink hover:text-link"
      >
        <span>{n.headline}</span>
        <span aria-hidden className="mt-0.5 shrink-0 text-xs text-ink-3">{open ? 'Hide' : 'Summary'} {open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div id={panelId} className="mt-2 rounded-[7px] bg-surface-2 px-3 py-2 text-[13.5px] leading-relaxed text-ink">
          {n.summary ? <p>{n.summary}</p> : <p className="text-ink-2">The source gave no short description for this item.</p>}
          <p className="mt-1.5 text-xs text-ink-2">
            {n.source} ({n.source_type} source)
            {n.url && (
              <>
                {' · '}
                <a href={n.url} target="_blank" rel="noopener noreferrer">
                  Open {n.kind === 'exchange_filing' ? 'the filing' : 'the article'}
                </a>
              </>
            )}
          </p>
        </div>
      )}
      {n.symbols.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {n.symbols.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onPick(s)}
              aria-pressed={picked === s}
              className={cx('rounded border px-1.5 py-0.5 text-xs font-semibold', picked === s ? 'border-accent bg-accent-soft' : 'border-rule bg-surface hover:border-ink-3')}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </li>
  )
}

const POLL_MS = 120_000

/** Re-fetch every source now, bypassing the server caches, and show when the page last updated and will update next. */
function RefetchAll({ params, updatedAt, polling }: { params: Record<string, unknown>; updatedAt: number; polling: boolean }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      const d = await api('/markets/news', { params: { ...params, refresh: true } })
      qc.setQueryData(['market-news', params], d)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Re-fetch failed')
    } finally {
      setBusy(false)
    }
  }
  const next = updatedAt ? Math.max(0, Math.round((updatedAt + POLL_MS - now) / 1000)) : null
  return (
    <div className="flex items-center gap-2">
      <span className="text-right text-xs text-ink-2" role="status" aria-live="polite">
        {err ? (
          <span className="text-loss">{err}</span>
        ) : busy ? (
          'Fetching announcements, headlines and prices'
        ) : (
          <>
            {updatedAt > 0 && `Updated ${new Date(updatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}
            <span className="block text-ink-3" data-testid="news-autoupdate">
              {polling ? 'Updating…' : next !== null ? `Auto-updates every 2 min · next in ${Math.floor(next / 60)}:${String(next % 60).padStart(2, '0')}` : 'Auto-updates every 2 min'}
            </span>
          </>
        )}
      </span>
      <Button onClick={run} busy={busy} title="Ask NSE, web search and the price sources again now, skipping all caches" data-testid="news-refetch">
        {!busy && (
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" />
            <path d="M13.8 2.5v3.2h-3.2" />
          </svg>
        )}
        Re-fetch all news
      </Button>
    </div>
  )
}

export function News() {
  const [days, setDays] = useState('1')
  const [routine, setRoutine] = useState(false)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [picked, setPicked] = useState<string | null>(null)
  const [source, setSource] = useState<'all' | 'exchange_filing' | 'news'>('all')
  const [sentiment, setSentiment] = useState<'all' | 'Bullish' | 'Bearish' | 'Neutral'>('all')
  const params = { days, include_routine: routine || undefined }
  // Polls every 2 minutes at all hours (news and filings keep arriving after the close), also while the tab is hidden.
  const q = useApi<any>(['market-news'], '/markets/news', params, {
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: true,
    staleTime: 0,
    placeholderData: (prev: any) => prev,
  })
  const toggle = (id: string) =>
    setOpen((o) => {
      const n = new Set(o)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  const pick = (s: string) => setPicked((p) => (p === s ? null : s))
  return (
    <>
      <PageHeader
        title="Market news"
        lede="Stocks named in today's NSE announcements and market headlines, and how far each has moved since the news. Open a headline to read its short description."
        refresh={<RefetchAll params={params} updatedAt={q.dataUpdatedAt} polling={q.isFetching} />}
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs tabs={WINDOWS} value={days} onChange={setDays} label="News window" />
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={routine} onChange={(e) => setRoutine(e.target.checked)} />
          Include routine filings
        </label>
        <label className="flex items-center gap-2 text-sm text-ink-2">
          Source
          <select value={source} onChange={(e) => setSource(e.target.value as typeof source)} className="rounded-[7px] border border-rule-strong bg-surface px-2 py-1 text-sm">
            <option value="all">All</option>
            <option value="exchange_filing">NSE filings</option>
            <option value="news">News headlines</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-ink-2">
          Sentiment
          <select value={sentiment} onChange={(e) => setSentiment(e.target.value as typeof sentiment)} className="rounded-[7px] border border-rule-strong bg-surface px-2 py-1 text-sm" data-testid="sentiment-filter">
            <option value="all">All</option>
            <option value="Bullish">Bullish</option>
            <option value="Bearish">Bearish</option>
            <option value="Neutral">Neutral</option>
          </select>
        </label>
      </div>
      <Async q={q} rows={12}>
        {(d) => <NewsBody d={d} open={open} toggle={toggle} picked={picked} pick={pick} source={source} sentiment={sentiment} />}
      </Async>
    </>
  )
}

function NewsBody({ d, open, toggle, picked, pick, source, sentiment }: { d: any; open: Set<string>; toggle: (id: string) => void; picked: string | null; pick: (s: string) => void; source: string; sentiment: string }) {
  const news: NewsItem[] = d.news ?? []
  const allStocks: Stock[] = d.stocks ?? []
  const stocks = sentiment === 'all' ? allStocks : allStocks.filter((s) => s.sentiment?.label === sentiment)
  const counts = d.sentiment_counts ?? {}
  const shown = news.filter((n) => (source === 'all' || n.kind === source) && (!picked || n.symbols.includes(picked)))
  if (d.state === 'not_configured') return <Notice tone="warn" title="Market news is not available">{d.reason}</Notice>
  const src = d.sources ?? {}
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-2">
        <span>
          {fmtDate(d.window.from)}
          {d.window.from !== d.window.to && ` to ${fmtDate(d.window.to)}`} (IST)
        </span>
        <span>
          NSE announcements: <StateBadge state={src.nse_announcements?.state} />
          {src.nse_announcements?.state !== 'ok' && src.nse_announcements?.reason && <span className="ml-1">{src.nse_announcements.reason}</span>}
        </span>
        <span>
          Web headlines: <StateBadge state={src.web_search?.state} />
          {src.web_search?.reason && <span className="ml-1">{src.web_search.reason}</span>}
        </span>
        <span>
          Prices: <StateBadge state={src.quotes?.state} /> {src.quotes?.live ?? 0} NSE live (delayed)
          {src.quotes?.broker ? `, ${src.quotes.broker} Zerodha Kite live` : ''}
          {src.quotes?.end_of_day ? `, ${src.quotes.end_of_day} end of day` : ''}
        </span>
        <span>Computed {ago(d.computed_at)}</span>
        {['nse_announcements', 'web_search'].some((k) => src[k]?.state === 'stale') && (
          <span className="w-full text-warn" data-testid="news-stale">
            A source is not answering right now, so its last retrieved items are shown
            {['nse_announcements', 'web_search']
              .filter((k) => src[k]?.state === 'stale' && src[k]?.retrieved_at)
              .map((k) => ` (${k === 'nse_announcements' ? 'NSE' : 'web'} as of ${fmtDateTime(src[k].retrieved_at)})`)
              .join('')}
            . The page keeps retrying every 2 minutes.
          </span>
        )}
        {d.routine_hidden > 0 && <span>{d.routine_hidden} routine filings hidden</span>}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)]">
        <Panel
          title="News"
          aside={
            picked ? (
              <button type="button" className="text-link" onClick={() => pick(picked)}>
                Showing {picked} only · show all
              </button>
            ) : (
              <span>{shown.length} items</span>
            )
          }
        >
          {shown.length === 0 ? (
            <EmptyState title="No news in this window">Try a longer window or include routine filings.</EmptyState>
          ) : (
            <ul className="m-0 list-none p-0">
              {shown.map((n) => (
                <NewsRow key={n.id} n={n} open={open.has(n.id)} onToggle={() => toggle(n.id)} onPick={pick} picked={picked} />
              ))}
            </ul>
          )}
        </Panel>
        <Panel
          title="Stocks in the news"
          aside={
            <span>
              {stocks.length} stocks · newest news first · <span className="text-gain">▲ {counts.Bullish ?? 0} bullish</span> ·{' '}
              <span className="text-loss">▼ {counts.Bearish ?? 0} bearish</span> · ■ {counts.Neutral ?? 0} neutral
            </span>
          } className="lg:sticky lg:top-[120px] lg:self-start">
          {stocks.length === 0 ? (
            <EmptyState title="No stocks named in this window" />
          ) : (
            <div className="scroll-x max-h-[70vh] overflow-y-auto">
              <table>
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th className="r">Price (₹)</th>
                    <th className="r" title="Latest price against the reference close before the news">
                      Since news
                    </th>
                    <th className="r">Today</th>
                  </tr>
                </thead>
                <tbody>
                  {stocks.map((s) => (
                    <tr key={s.symbol} className={cx(picked === s.symbol && 'bg-accent-soft')}>
                      <td>
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          {s.in_universe ? (
                            <Link to={instrumentPath(s.symbol)} className="font-semibold">
                              {s.symbol}
                            </Link>
                          ) : (
                            <span className="font-semibold">{s.symbol}</span>
                          )}
                          <button type="button" className="text-xs text-link" onClick={() => pick(s.symbol)} aria-pressed={picked === s.symbol}>
                            {s.news_ids.length} {s.news_ids.length === 1 ? 'item' : 'items'}
                          </button>
                          <SentimentBadge s={s.sentiment} />
                        </div>
                        <div className="text-xs text-ink-2">{s.categories.slice(0, 2).join(', ')}</div>
                        <div className="text-[11px] text-ink-3">
                          Latest news {when(s.latest_news_at)}
                          {s.first_news_at && s.first_news_at !== s.latest_news_at && ` · first ${when(s.first_news_at)}`}
                        </div>
                      </td>
                      <td className="r num">
                        {s.last_price == null ? <Missing reason={s.move_reason} /> : fmtNum(s.last_price)}
                        {s.quote_basis === 'end of day' && <div className="text-[11px] text-ink-3">end of day</div>}
                        {s.quote_basis === 'live (Zerodha Kite)' && <div className="text-[11px] text-ink-3">Kite</div>}
                      </td>
                      <td className={`r num whitespace-nowrap font-medium ${signClass(s.move_pct)}`} title={s.ref_basis ? `From ₹${fmtNum(s.ref_price)} (${s.ref_basis}${s.ref_date ? `, ${s.ref_date}` : ''})` : s.move_reason}>
                        {s.move_pct == null ? <Missing reason={s.move_reason} /> : fmtSigned(s.move_pct, 2)}
                        {s.ref_price != null && <div className="text-[11px] font-normal text-ink-3">from {fmtNum(s.ref_price)}</div>}
                      </td>
                      <td className={`r num whitespace-nowrap ${signClass(s.day_change_pct)}`}>{fmtSigned(s.day_change_pct, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="border-t border-rule px-4 py-2 text-[11.5px] leading-relaxed text-ink-2">
            Prices are NSE delayed quotes, or Zerodha Kite live quotes (marked "Kite") when NSE cannot answer. "Since news" compares with the previous close when the news came before or during the session, or with that
            day's close when it came after 15:30 IST. Hover a value for its reference. Headlines are linked to a stock only when its symbol or name
            appears in them. Sentiment is rule-based and low confidence: the news tone (NSE category or clear wording) plus the
            price reaction since the news (±1.5%). Hover a sentiment for its reasons. It is not a forecast or advice.
          </p>
        </Panel>
      </div>
    </div>
  )
}

