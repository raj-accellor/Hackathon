import { CandlestickSeries, HistogramSeries, LineSeries, createChart, type IChartApi } from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtDate, fmtNum } from '../lib/format'
import { Button, cx } from './ui'

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export type Bar = { date: string; open: number | null; high: number | null; low: number | null; close: number; volume: number | null; provider: string }

/** Daily price chart. Gain/loss bodies are also distinguishable by the hollow/filled convention in the legend row. */
export function PriceChart({ bars, height = 340 }: { bars: Bar[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null)
  const [mode, setMode] = useState<'candles' | 'line'>('candles')
  const [hover, setHover] = useState<Bar | null>(null)
  const last = bars[bars.length - 1]
  useEffect(() => {
    if (!el.current || bars.length === 0) return
    const ink2 = cssVar('--ink-2')
    const rule = cssVar('--rule')
    const chart: IChartApi = createChart(el.current, {
      height,
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: ink2, fontFamily: cssVar('--font-body'), attributionLogo: true },
      grid: { vertLines: { visible: false }, horzLines: { color: rule } },
      rightPriceScale: { borderColor: rule },
      timeScale: { borderColor: rule, rightOffset: 4 },
      crosshair: { mode: 1 },
    })
    const gain = cssVar('--gain')
    const loss = cssVar('--loss')
    const usable = bars.filter((b) => b.open != null && b.high != null && b.low != null)
    const main =
      mode === 'candles' && usable.length === bars.length
        ? chart.addSeries(CandlestickSeries, { upColor: gain, downColor: loss, borderUpColor: gain, borderDownColor: loss, wickUpColor: gain, wickDownColor: loss })
        : chart.addSeries(LineSeries, { color: cssVar('--glyph'), lineWidth: 2, priceLineVisible: false })
    main.setData(
      (mode === 'candles' && usable.length === bars.length
        ? bars.map((b) => ({ time: b.date, open: b.open as number, high: b.high as number, low: b.low as number, close: b.close }))
        : bars.map((b) => ({ time: b.date, value: b.close }))) as any,
    )
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: cssVar('--glyph-empty') })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } })
    vol.setData(bars.filter((b) => b.volume != null).map((b) => ({ time: b.date, value: b.volume as number })) as any)
    const byDate = new Map(bars.map((b) => [b.date, b]))
    chart.subscribeCrosshairMove((p) => setHover(p.time ? (byDate.get(String(p.time)) ?? null) : null))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [bars, mode, height])
  const b = hover ?? last
  if (!b) return null
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3 text-[13px]">
        <div className="num flex flex-wrap gap-x-4 gap-y-0.5 text-ink-2" aria-live="off">
          <span className="font-medium text-ink">{fmtDate(b.date)}</span>
          <span>O {fmtNum(b.open)}</span>
          <span>H {fmtNum(b.high)}</span>
          <span>L {fmtNum(b.low)}</span>
          <span>
            C <strong className="text-ink">{fmtNum(b.close)}</strong>
          </span>
          <span>Vol {b.volume == null ? '—' : fmtNum(b.volume, 0)}</span>
          <span>Source {b.provider === 'nse_mcp' ? 'NSE MCP' : b.provider === 'kite_mcp' ? 'Kite MCP' : b.provider}</span>
        </div>
        <div className="flex gap-1" role="group" aria-label="Chart type">
          {(['candles', 'line'] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m} className={cx('rounded border px-2 py-1 text-xs', mode === m ? 'border-accent bg-accent-soft font-semibold' : 'border-rule-strong')}>
              {m === 'candles' ? 'Candles' : 'Line'}
            </button>
          ))}
        </div>
      </div>
      <div ref={el} style={{ height }} className="px-2" role="img" aria-label={`Daily price chart, ${bars.length} bars from ${bars[0].date} to ${last.date}. Latest close ${fmtNum(last.close)} rupees.`} />
    </div>
  )
}

export function Sparkline({ values, width = 160, height = 40, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length < 2) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * (width - 6) + 3},${height - 4 - ((v - min) / (max - min || 1)) * (height - 8)}`)
  const [lx, ly] = pts[pts.length - 1].split(',').map(Number)
  return (
    <svg width={width} height={height} role="img" aria-label={label}>
      <polyline points={pts.join(' ')} fill="none" stroke="var(--glyph)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={4} fill="var(--glyph)" stroke="var(--surface)" strokeWidth={2} />
    </svg>
  )
}

// ------------------------------------------------------------------ Relative Rotation Graph
type Pt = { date: string; rs_ratio: number; rs_momentum: number }
export type RRGSector = { status: string; quadrant?: string | null; trail?: Pt[]; direction?: string | null }
type Quad = 'Leading' | 'Weakening' | 'Lagging' | 'Improving'
const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)']
/** A tail takes the ink of the quadrant its head sits in: colour carries meaning, the label carries identity. */
const QUAD_INK: Record<Quad, string> = { Leading: 'var(--gain)', Weakening: 'var(--warn)', Lagging: 'var(--loss)', Improving: 'var(--link)' }
const QUADS = [
  { name: 'Improving', x: 0, y: 0, fill: 'var(--quad-improving)', ax: 'start', ay: 'top' },
  { name: 'Leading', x: 1, y: 0, fill: 'var(--quad-leading)', ax: 'end', ay: 'top' },
  { name: 'Lagging', x: 0, y: 1, fill: 'var(--quad-lagging)', ax: 'start', ay: 'bottom' },
  { name: 'Weakening', x: 1, y: 1, fill: 'var(--quad-weakening)', ax: 'end', ay: 'bottom' },
] as const
const SHORT: Record<string, string> = {
  'Media Entertainment & Publication': 'Media & Entertainment',
  'Information Technology': 'Info. Technology',
  'Construction Materials': 'Constr. Materials',
  'Consumer Durables': 'Cons. Durables',
  'Consumer Services': 'Cons. Services',
  'Financial Services': 'Financials',
}
const shortName = (n: string) => SHORT[n] ?? n
const quadOf = (p: Pt): Quad => (p.rs_ratio >= 100 ? (p.rs_momentum >= 100 ? 'Leading' : 'Weakening') : p.rs_momentum >= 100 ? 'Improving' : 'Lagging')
const ARROWS: Record<string, string> = { 'north-east': '↗', 'north-west': '↖', 'south-east': '↘', 'south-west': '↙', north: '↑', south: '↓', east: '→', west: '←' }
function heading(t: Pt[]): string {
  if (t.length < 2) return ''
  const [a, b] = t.slice(-2)
  const dx = b.rs_ratio - a.rs_ratio
  const dy = b.rs_momentum - a.rs_momentum
  return ARROWS[`${dy > 0 ? 'north' : dy < 0 ? 'south' : ''}-${dx > 0 ? 'east' : dx < 0 ? 'west' : ''}`.replace(/^-|-$/g, '')] ?? '·'
}

/** Cubic Bézier segment i of a Catmull-Rom spline through `p` (screen coordinates). Passes through every weekly point. */
function segment(p: Array<[number, number]>, i: number): string {
  const p0 = p[Math.max(0, i - 1)]
  const p1 = p[i]
  const p2 = p[i + 1]
  const p3 = p[Math.min(p.length - 1, i + 2)]
  const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]
  const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
  return `M${p1[0]},${p1[1]} C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`
}

type Box = { x: number; y: number; w: number; h: number }
const hits = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
const DIRS: Array<[number, number]> = [[1, 0], [-1, 0], [0.75, -0.75], [0.75, 0.75], [-0.75, -0.75], [-0.75, 0.75], [0, -1], [0, 1]]

/**
 * Greedy label placement: each label tries eight directions around its dot at growing distances and takes the first
 * spot clear of the plot edge, every dot and every label already placed. Labels pushed away get a leader line.
 */
function placeLabels(heads: Array<{ n: string; x: number; y: number; text: string; bold: boolean }>, plot: Box) {
  const dots: Box[] = heads.map((h) => ({ x: h.x - 7, y: h.y - 7, w: 14, h: 14 }))
  const placed: Box[] = []
  const out = new Map<string, { x: number; y: number; anchor: 'start' | 'middle' | 'end'; leader: boolean }>()
  const order = [...heads].sort((a, b) => Number(b.bold) - Number(a.bold) || a.y - b.y)
  for (const h of order) {
    const w = h.text.length * (h.bold ? 6.9 : 6.4) + 6
    const ht = 15
    let best: Box | null = null
    let leader = false
    for (const r of [10, 22, 36, 52, 70]) {
      for (const [dx, dy] of DIRS) {
        const cx = h.x + dx * r
        const cy = h.y + dy * r
        const box = { x: dx > 0.1 ? cx : dx < -0.1 ? cx - w : cx - w / 2, y: dy > 0.1 ? cy : dy < -0.1 ? cy - ht : cy - ht / 2, w, h: ht }
        const inside = box.x >= plot.x + 2 && box.y >= plot.y + 2 && box.x + w <= plot.x + plot.w - 2 && box.y + ht <= plot.y + plot.h - 2
        if (inside && !placed.some((b) => hits(b, box)) && !dots.some((d, j) => heads[j].n !== h.n && hits(d, box))) {
          best = box
          leader = r > 10
          break
        }
      }
      if (best) break
    }
    best ??= { x: Math.min(h.x + 10, plot.x + plot.w - w), y: h.y - ht / 2, w, h: ht }
    placed.push(best)
    out.set(h.n, { x: best.x + 3, y: best.y + ht - 4, anchor: 'start', leader })
  }
  return out
}

export const DEFAULT_TAIL_WEEKS = 2

export function RRGChart({ sectors, selected, onToggle, visible, onNeedMoreHistory }: { sectors: Record<string, RRGSector>; selected: string[]; onToggle: (name: string) => void; visible: Set<string>; onNeedMoreHistory?: () => void }) {
  const [zoom, setZoom] = useState(1)
  const [hover, setHover] = useState<{ name: string; pt: Pt; prev?: Pt; x: number; y: number } | null>(null)
  const [focusName, setFocusName] = useState<string | null>(null)
  const maxTail = useMemo(() => Math.max(1, ...Object.values(sectors).map((s) => s.trail?.length ?? 0)), [sectors])
  const [tail, setTail] = useState(DEFAULT_TAIL_WEEKS)
  const [back, setBack] = useState(0) // weeks stepped back from the latest observation
  const [playing, setPlaying] = useState(false)
  const maxBack = Math.max(0, maxTail - 1)
  const offset = Math.min(back, maxBack)
  // The tail is measured in weeks of movement: N weeks draws N + 1 weekly points. Points end `offset` weeks before the latest.
  const maxWeeks = Math.max(1, maxTail - offset - 1)
  const weeks = Math.min(tail, maxWeeks)
  const shown = Math.min(weeks + 1, maxTail - offset)
  const trailOf = (n: string) => {
    const all = (sectors[n].trail ?? []) as Pt[]
    const end = Math.max(1, all.length - offset)
    return all.slice(Math.max(0, end - shown), end)
  }
  // Replay: step from the earliest computed week to the latest, one week per beat. Stops on its own at the latest week.
  useEffect(() => {
    if (!playing) return
    if (offset === 0) {
      setPlaying(false)
      return
    }
    const t = setTimeout(() => setBack(offset - 1), 650)
    return () => clearTimeout(t)
  }, [playing, offset])
  const play = () => {
    if (playing) return setPlaying(false)
    if (offset === 0) setBack(Math.max(0, maxBack - weeks))
    setPlaying(true)
  }
  const anyName = Object.keys(sectors).find((n) => (sectors[n].trail?.length ?? 0) === maxTail)
  const headDate = anyName ? trailOf(anyName).at(-1)?.date : undefined
  const W = 760
  const H = 560
  const M = { l: 50, r: 14, t: 14, b: 46 }
  const plot = { x: M.l, y: M.t, w: W - M.l - M.r, h: H - M.t - M.b }
  const names = useMemo(() => Object.keys(sectors).filter((n) => sectors[n].status === 'ok' && (sectors[n].trail?.length ?? 0) > 0 && visible.has(n)).sort(), [sectors, visible])
  // Axes fit what is drawn (each axis on its own), always keep the 100 centre lines in view, and zoom about the data centre.
  const dom = useMemo(() => {
    let x0 = 100, x1 = 100, y0 = 100, y1 = 100
    for (const n of names)
      for (const p of trailOf(n)) {
        x0 = Math.min(x0, p.rs_ratio)
        x1 = Math.max(x1, p.rs_ratio)
        y0 = Math.min(y0, p.rs_momentum)
        y1 = Math.max(y1, p.rs_momentum)
      }
    const hx = Math.max(1.5, ((x1 - x0) / 2) * 1.14) / zoom
    const hy = Math.max(1.5, ((y1 - y0) / 2) * 1.14) / zoom
    const mx = (x0 + x1) / 2
    const my = (y0 + y1) / 2
    return { x0: mx - hx, x1: mx + hx, y0: my - hy, y1: my + hy }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names, sectors, zoom, shown, offset])
  const sx = (v: number) => M.l + ((v - dom.x0) / (dom.x1 - dom.x0)) * plot.w
  const sy = (v: number) => M.t + (1 - (v - dom.y0) / (dom.y1 - dom.y0)) * plot.h
  const cx0 = Math.min(Math.max(sx(100), M.l), W - M.r)
  const cy0 = Math.min(Math.max(sy(100), M.t), H - M.b)
  const ticksOf = (a: number, b: number) => {
    const step = [0.5, 1, 2, 2.5, 5, 10, 20].find((s) => (b - a) / s <= 7) ?? 50
    const out: number[] = []
    for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(Math.round(v * 10) / 10)
    return out
  }
  const focus = hover?.name ?? focusName
  const isDim = (n: string) => (focus ? focus !== n : selected.length > 0 && !selected.includes(n))
  const heads = names.map((n) => {
    const t = trailOf(n)
    const p = t[t.length - 1]
    return { n, x: sx(p.rs_ratio), y: sy(p.rs_momentum), text: shortName(n), bold: selected.includes(n) || focus === n, q: quadOf(p), t }
  })
  const labels = placeLabels(heads, plot)
  const inkOf = (n: string, q: Quad) => {
    const i = selected.indexOf(n)
    return i >= 0 ? SERIES[i] : QUAD_INK[q]
  }
  const roster = useMemo(() => {
    const g: Record<Quad, typeof heads> = { Improving: [], Leading: [], Lagging: [], Weakening: [] }
    for (const h of heads) g[h.q].push(h)
    for (const q of Object.keys(g) as Quad[]) g[q].sort((a, b) => b.t[b.t.length - 1].rs_ratio - a.t[a.t.length - 1].rs_ratio)
    return g
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names, sectors, shown, offset, selected, focus])
  const step = (d: number) => {
    setPlaying(false)
    setBack(Math.min(maxBack, Math.max(0, offset + d)))
  }
  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-rule px-4 py-2.5 text-[13px] text-ink-2">
        <div className="flex items-center gap-1.5" role="group" aria-label="Tail length">
          <span className="mr-1">Tail</span>
          <Button onClick={() => setTail(Math.max(1, weeks - 1))} disabled={weeks <= 1} aria-label="Shorter tail" data-testid="rrg-tail-less" className="min-h-[30px] px-2">
            −
          </Button>
          <input type="range" min={1} max={maxWeeks} step={1} value={weeks} onChange={(e) => setTail(Number(e.target.value))} aria-label="Tail length in weeks" data-testid="rrg-tail" className="w-24 accent-[var(--accent)]" />
          <Button onClick={() => setTail(Math.min(maxWeeks, weeks + 1))} disabled={weeks >= maxWeeks} aria-label="Longer tail" data-testid="rrg-tail-more" className="min-h-[30px] px-2">
            +
          </Button>
          <span className="num w-16 text-ink" aria-live="polite">
            {weeks} {weeks === 1 ? 'week' : 'weeks'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Step through weeks">
          <Button onClick={() => step(1)} disabled={offset >= maxBack} aria-label="One week earlier" data-testid="rrg-back" className="min-h-[30px]">
            ◀ Week
          </Button>
          <Button onClick={play} disabled={maxBack === 0} aria-pressed={playing} data-testid="rrg-play" className="min-h-[30px]" title="Replay the rotation week by week up to the latest week">
            {playing ? '❚❚ Pause' : '▶ Replay'}
          </Button>
          <Button onClick={() => step(-1)} disabled={offset === 0} aria-label="One week later" data-testid="rrg-forward" className="min-h-[30px]">
            Week ▶
          </Button>
          <Button variant="quiet" onClick={() => (setPlaying(false), setBack(0))} disabled={offset === 0} className="min-h-[30px]">
            Latest
          </Button>
          <span className="ml-1 text-ink" aria-live="polite" data-testid="rrg-as-of">
            {offset === 0 ? 'Latest week' : `${offset} ${offset === 1 ? 'week' : 'weeks'} back`}
            {headDate && ` · week ending ${headDate}`}
          </span>
          {offset >= maxBack && maxBack > 0 && onNeedMoreHistory && (
            <button type="button" className="text-link underline-offset-2 hover:underline" onClick={onNeedMoreHistory} data-testid="rrg-more-history">
              Earliest computed week · load more weeks
            </button>
          )}
        </div>
        <div className="ml-auto flex items-center gap-1" role="group" aria-label="Zoom">
          <Button onClick={() => setZoom((z) => Math.max(z / 1.35, 0.6))} aria-label="Zoom out" className="min-h-[30px] px-2.5">
            −
          </Button>
          <Button onClick={() => setZoom((z) => Math.min(z * 1.35, 5))} aria-label="Zoom in" className="min-h-[30px] px-2.5">
            +
          </Button>
          <Button variant="quiet" onClick={() => setZoom(1)} disabled={zoom === 1} className="min-h-[30px]">
            Fit
          </Button>
        </div>
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_272px]">
        <div className="relative min-w-0">
          <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full select-none" role="img" aria-label={`Relative rotation graph of ${names.length} sectors. A table of the same values follows.`} onMouseLeave={() => setHover(null)}>
            <defs>
              <clipPath id="rrg-clip">
                <rect x={plot.x} y={plot.y} width={plot.w} height={plot.h} />
              </clipPath>
            </defs>
            {QUADS.map((q) => (
              <g key={q.name}>
                <rect x={q.x ? cx0 : M.l} y={q.y ? cy0 : M.t} width={Math.max(0, q.x ? W - M.r - cx0 : cx0 - M.l)} height={Math.max(0, q.y ? H - M.b - cy0 : cy0 - M.t)} fill={q.fill} />
                <text x={q.ax === 'start' ? M.l + 12 : W - M.r - 12} y={q.ay === 'top' ? M.t + 22 : H - M.b - 12} textAnchor={q.ax} fontSize={14} fontWeight={600} fill={QUAD_INK[q.name]} opacity={0.85}>
                  {q.name}
                </text>
              </g>
            ))}
            {ticksOf(dom.x0, dom.x1).map((t) => (
              <g key={`x${t}`}>
                <line x1={sx(t)} x2={sx(t)} y1={M.t} y2={H - M.b} stroke="var(--ink)" strokeOpacity={0.05} />
                <text x={sx(t)} y={H - M.b + 17} textAnchor="middle" fontSize={11} fill="var(--ink-2)" className="num">
                  {t}
                </text>
              </g>
            ))}
            {ticksOf(dom.y0, dom.y1).map((t) => (
              <g key={`y${t}`}>
                <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} stroke="var(--ink)" strokeOpacity={0.05} />
                <text x={M.l - 8} y={sy(t) + 4} textAnchor="end" fontSize={11} fill="var(--ink-2)" className="num">
                  {t}
                </text>
              </g>
            ))}
            <line x1={cx0} x2={cx0} y1={M.t} y2={H - M.b} stroke="var(--ink-3)" strokeDasharray="3 3" />
            <line x1={M.l} x2={W - M.r} y1={cy0} y2={cy0} stroke="var(--ink-3)" strokeDasharray="3 3" />
            <text x={(M.l + W - M.r) / 2} y={H - 8} textAnchor="middle" fontSize={12} fill="var(--ink-2)">
              RS-Ratio: strength against the benchmark (100 = in line) →
            </text>
            <text transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" fontSize={12} fill="var(--ink-2)">
              RS-Momentum: is that strength rising? (100 = flat) →
            </text>
            <g clipPath="url(#rrg-clip)">
              {heads.map(({ n, t, q }) => {
                const ink = inkOf(n, q)
                const pts = t.map((p) => [sx(p.rs_ratio), sy(p.rs_momentum)] as [number, number])
                const sel = selected.includes(n) || focus === n
                const quiet = !sel && (selected.length > 0 || focus != null)
                return (
                  <g key={n} opacity={isDim(n) ? 0.14 : 1} style={{ transition: 'opacity 160ms' }} data-testid="rrg-tail-path" data-sector={n} data-points={t.length}>
                    {pts.slice(0, -1).map((_, i) => {
                      const k = (i + 1) / (pts.length - 1)
                      return <path key={i} d={segment(pts, i)} fill="none" stroke={ink} strokeWidth={sel ? 1.6 + 2.6 * k : 0.8 + 1.4 * k} strokeOpacity={quiet ? 0.3 : sel ? 0.3 + 0.7 * k : 0.15 + 0.55 * k} strokeLinecap="round" />
                    })}
                    {pts.slice(0, -1).map(([x, y], i) => (
                      <circle key={`w${i}`} cx={x} cy={y} r={1.7} fill={ink} fillOpacity={0.3 + (0.5 * (i + 1)) / pts.length} />
                    ))}
                  </g>
                )
              })}
              {/* Heads and hover targets sit above every tail, so no tail ever covers another sector's dot. */}
              {heads.map(({ n, t, q }) => {
                const ink = inkOf(n, q)
                const sel = selected.includes(n) || focus === n
                const head = t[t.length - 1]
                return (
                  <g key={`d${n}`} opacity={isDim(n) ? 0.25 : 1} style={{ transition: 'opacity 160ms' }}>
                    <circle cx={sx(head.rs_ratio)} cy={sy(head.rs_momentum)} r={sel ? 6.5 : 5} fill={ink} stroke="var(--surface)" strokeWidth={2} />
                    {t.map((p, i) => (
                      <circle
                        key={`h${p.date}`}
                        cx={sx(p.rs_ratio)}
                        cy={sy(p.rs_momentum)}
                        r={10}
                        fill="transparent"
                        style={{ cursor: 'pointer' }}
                        onMouseEnter={() => setHover({ name: n, pt: p, prev: t[i - 1], x: sx(p.rs_ratio), y: sy(p.rs_momentum) })}
                        onClick={() => onToggle(n)}
                      />
                    ))}
                  </g>
                )
              })}
            </g>
            {heads.map(({ n, x, y, text, bold }) => {
              const l = labels.get(n)
              if (!l) return null
              return (
                <g
                  key={`l${n}`}
                  opacity={isDim(n) ? 0.3 : 1}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selected.includes(n)}
                  aria-label={`${n}: ${selected.includes(n) ? 'remove highlight' : 'highlight'}`}
                  onClick={() => onToggle(n)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onToggle(n))}
                  onMouseEnter={() => setFocusName(n)}
                  onMouseLeave={() => setFocusName(null)}
                  onFocus={() => setFocusName(n)}
                  onBlur={() => setFocusName(null)}
                  style={{ cursor: 'pointer', outline: 'none' }}
                  className="rrg-label"
                >
                  {l.leader && <line x1={x} y1={y} x2={l.x - 2} y2={l.y - 4} stroke="var(--ink-3)" strokeWidth={0.8} />}
                  <text x={l.x} y={l.y} textAnchor={l.anchor} fontSize={11.5} fontWeight={bold ? 700 : 500} fill="var(--ink)" stroke="var(--surface)" strokeWidth={3.5} paintOrder="stroke" strokeLinejoin="round">
                    {text}
                  </text>
                </g>
              )
            })}
          </svg>
          {hover && (
            <div className="pointer-events-none absolute z-10 w-[200px] rounded-md border border-rule-strong bg-surface px-3 py-2 text-[12px] shadow-md" style={{ left: `min(calc(${(hover.x / W) * 100}% + 12px), calc(100% - 208px))`, top: `calc(${(hover.y / H) * 100}% + 12px)` }}>
              <div className="font-semibold">{hover.name}</div>
              <div className="text-ink-2">
                {quadOf(hover.pt)}, week ending {fmtDate(hover.pt.date)}
              </div>
              <div className="num mt-1 grid grid-cols-[1fr_auto_auto] gap-x-2">
                <span>RS-Ratio</span>
                <span>{fmtNum(hover.pt.rs_ratio)}</span>
                <span className="text-ink-2">{hover.prev ? `${hover.pt.rs_ratio >= hover.prev.rs_ratio ? '+' : '−'}${fmtNum(Math.abs(hover.pt.rs_ratio - hover.prev.rs_ratio))}` : ''}</span>
                <span>RS-Momentum</span>
                <span>{fmtNum(hover.pt.rs_momentum)}</span>
                <span className="text-ink-2">{hover.prev ? `${hover.pt.rs_momentum >= hover.prev.rs_momentum ? '+' : '−'}${fmtNum(Math.abs(hover.pt.rs_momentum - hover.prev.rs_momentum))}` : ''}</span>
              </div>
            </div>
          )}
        </div>
        <aside className="border-t border-rule lg:border-l lg:border-t-0" aria-label="Sectors by quadrant">
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:block">
            {(['Leading', 'Weakening', 'Improving', 'Lagging'] as Quad[]).map((q) => (
              <section key={q} className="min-w-0 border-b border-r border-rule px-3 py-2.5 last:border-b-0 lg:border-r-0">
                <h3 className="m-0 mb-1.5 flex items-baseline justify-between text-[13px] font-semibold" style={{ color: QUAD_INK[q] }}>
                  {q}
                  <span className="num text-[12px] font-medium text-ink-2">{roster[q].length}</span>
                </h3>
                {roster[q].length === 0 ? (
                  <p className="m-0 text-[12px] text-ink-3">None this week</p>
                ) : (
                  <ul className="m-0 list-none space-y-px p-0">
                    {roster[q].map((h) => (
                      <li key={h.n}>
                        <button
                          type="button"
                          onClick={() => onToggle(h.n)}
                          onMouseEnter={() => setFocusName(h.n)}
                          onMouseLeave={() => setFocusName(null)}
                          onFocus={() => setFocusName(h.n)}
                          onBlur={() => setFocusName(null)}
                          aria-pressed={selected.includes(h.n)}
                          title={`${h.n}: RS-Ratio ${fmtNum(h.t[h.t.length - 1].rs_ratio)}, RS-Momentum ${fmtNum(h.t[h.t.length - 1].rs_momentum)}`}
                          className={cx('flex w-full items-center gap-1.5 rounded px-1 py-[3px] text-left text-[12px] hover:bg-surface-2', (selected.includes(h.n) || focus === h.n) && 'bg-surface-2 font-semibold')}
                        >
                          <span aria-hidden className="w-3 text-center text-ink-2">
                            {heading(h.t)}
                          </span>
                          <span className="min-w-0 flex-1 truncate">{shortName(h.n)}</span>
                          <span className="num text-ink-2">{fmtNum(h.t[h.t.length - 1].rs_ratio, 1)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </aside>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-rule px-4 py-2 text-[12px] text-ink-2">
        <span>Tails run from older weeks (thin, faint) to the latest week (dot). Colour shows the quadrant each sector is in now. Hover a sector to isolate it; click to highlight up to three.</span>
        {selected.length > 0 && (
          <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[13px] text-ink" aria-label="Highlighted sectors">
            {selected.map((n, i) => (
              <li key={n} className="flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: SERIES[i] }} aria-hidden />
                {n}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
