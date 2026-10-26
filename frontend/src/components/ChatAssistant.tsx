import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { ApiError, post } from '../api/client'
import { cx } from './ui'

// The model and its key stay on the server (LLM_* settings, OpenRouter by default); the browser only sees replies.
const ReplySchema = z.object({
  reply: z.string(),
  navigate: z.object({ path: z.string().startsWith('/'), label: z.string(), page_id: z.string() }).nullable(),
  source: z.enum(['llm', 'rules']),
  model_id: z.string().nullable(),
  llm_error: z.string().nullable(),
  disclaimer: z.string(),
})
type Reply = z.infer<typeof ReplySchema>
type Turn = { role: 'user' | 'assistant'; content: string; navigate?: Reply['navigate']; source?: Reply['source'] }

const STARTERS = ['Take me to sector rotation', 'Where do I see FII / DII flows?', 'Research RELIANCE', 'How do themes work?']
const KEY = 'chat_assistant_turns'
const GREET_KEY = 'chat_assistant_greet_dismissed'
const GREETING = "Hi, I'm MI007 bot. How can I help you?"

const POS_KEY = 'chat_assistant_position'
const ICON = 56 // px, the robot button
const GAP = 12 // px between the icon and the popup/panel
const DRAG_THRESHOLD = 5 // px of movement before a press counts as a drag, not a click

type Pt = { x: number; y: number }

const margin = (vw: number) => (vw >= 640 ? 24 : 16)
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi))

function clampPos(p: Pt, vw: number, vh: number): Pt {
  return { x: clamp(p.x, 8, vw - ICON - 8), y: clamp(p.y, 8, vh - ICON - 8) }
}

/** The icon's top-left corner. Dragged with mouse or touch (or moved with arrow keys while focused); remembered per browser. */
function useDraggableIcon() {
  const [vp, setVp] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }))
  const [saved, setSaved] = useState<Pt | null>(() => {
    try {
      const v = JSON.parse(localStorage.getItem(POS_KEY) ?? 'null')
      return v && typeof v.x === 'number' && typeof v.y === 'number' ? v : null
    } catch {
      return null
    }
  })
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ start: Pt; offset: Pt; moved: boolean; id: number } | null>(null)
  const justDragged = useRef(false)

  useEffect(() => {
    const onResize = () => setVp({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const fallback = { x: vp.w - ICON - margin(vp.w), y: vp.h - ICON - margin(vp.h) }
  const pos = clampPos(saved ?? fallback, vp.w, vp.h)

  const persist = (p: Pt | null) => {
    setSaved(p)
    try {
      if (p) localStorage.setItem(POS_KEY, JSON.stringify(p))
      else localStorage.removeItem(POS_KEY)
    } catch {
      /* storage unavailable */
    }
  }

  const onPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return
    drag.current = { start: { x: e.clientX, y: e.clientY }, offset: { x: e.clientX - pos.x, y: e.clientY - pos.y }, moved: false, id: e.pointerId }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    if (!d.moved && Math.hypot(e.clientX - d.start.x, e.clientY - d.start.y) < DRAG_THRESHOLD) return
    d.moved = true
    setDragging(true)
    setSaved(clampPos({ x: e.clientX - d.offset.x, y: e.clientY - d.offset.y }, vp.w, vp.h))
  }
  const onPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current
    drag.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (d?.moved) {
      justDragged.current = true // swallow the click that follows a drag
      setDragging(false)
      persist(clampPos({ x: e.clientX - d.offset.x, y: e.clientY - d.offset.y }, vp.w, vp.h))
    }
  }
  const onKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    const step = e.shiftKey ? 64 : 16
    const dir: Record<string, Pt> = { ArrowLeft: { x: -step, y: 0 }, ArrowRight: { x: step, y: 0 }, ArrowUp: { x: 0, y: -step }, ArrowDown: { x: 0, y: step } }
    const d = dir[e.key]
    if (!d) return
    e.preventDefault()
    persist(clampPos({ x: pos.x + d.x, y: pos.y + d.y }, vp.w, vp.h))
  }
  /** True once, right after a drag ended, so the button's click handler can ignore it. */
  const consumeDrag = () => {
    const v = justDragged.current
    justDragged.current = false
    return v
  }
  return { pos, vp, dragging, moved: saved !== null, reset: () => persist(null), consumeDrag, handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onKeyDown } }
}

/** Open the popup and panel toward the side of the screen with more room. */
function placement(pos: Pt, vp: { w: number; h: number }) {
  const right = pos.x + ICON / 2 > vp.w / 2
  const above = pos.y + ICON / 2 > vp.h / 2
  const panelW = Math.min(380, vp.w - 32)
  const panelH = Math.min(560, vp.h - 112)
  const panelLeft = clamp(right ? pos.x + ICON - panelW : pos.x, 16, vp.w - panelW - 16)
  const panelTop = clamp(above ? pos.y - GAP - panelH : pos.y + ICON + GAP, 16, vp.h - panelH - 16)
  const panel: CSSProperties = { left: panelLeft, top: panelTop, width: panelW, height: panelH }
  const bubble: CSSProperties = {
    ...(right ? { right: Math.max(8, vp.w - pos.x - ICON) } : { left: Math.max(8, pos.x) }),
    ...(above ? { bottom: vp.h - pos.y + GAP } : { top: pos.y + ICON + GAP }),
  }
  return { right, above, panel, bubble }
}

function load(): Turn[] {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? '[]')
  } catch {
    return []
  }
}

export function ChatAssistant() {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [turns, setTurns] = useState<Turn[]>(load)
  const [greet, setGreet] = useState(() => {
    try {
      return sessionStorage.getItem(GREET_KEY) !== '1'
    } catch {
      return true
    }
  })
  const dismissGreet = () => {
    setGreet(false)
    try {
      sessionStorage.setItem(GREET_KEY, '1')
    } catch {
      /* storage unavailable */
    }
  }
  const icon = useDraggableIcon()
  const place = placement(icon.pos, icon.vp)
  const nav = useNavigate()
  const loc = useLocation()
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const m = useMutation<Reply, ApiError, Turn[]>({
    mutationFn: async (history) => {
      const data = await post('/assistant/chat', {
        messages: history.slice(-12).map((t) => ({ role: t.role, content: t.content.slice(0, 1000) })),
        current_path: loc.pathname,
      })
      const parsed = ReplySchema.safeParse(data)
      if (!parsed.success) throw new ApiError(200, 'contract_mismatch', 'The assistant reply did not match the expected shape.')
      return parsed.data
    },
    onSuccess: (r) => {
      setTurns((t) => [...t, { role: 'assistant', content: r.reply, navigate: r.navigate, source: r.source }])
      if (r.navigate && r.navigate.path !== loc.pathname) nav(r.navigate.path)
    },
  })

  useEffect(() => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(turns.slice(-30)))
    } catch {
      /* storage unavailable */
    }
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [turns, m.isPending, open])

  useEffect(() => {
    if (open) inputRef.current?.focus()
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [open])

  const send = (e?: FormEvent, preset?: string) => {
    e?.preventDefault()
    const content = (preset ?? text).trim()
    if (!content || m.isPending) return
    const next = [...turns, { role: 'user' as const, content }]
    setTurns(next)
    setText('')
    m.mutate(next)
  }

  return (
    <>
      {open && (
        <section
          id="chat-assistant"
          role="dialog"
          aria-label="MI007 bot chat"
          style={place.panel}
          className="fixed z-40 flex flex-col overflow-hidden rounded-[14px] border border-rule bg-surface shadow-2xl"
        >
          <header className="flex items-center justify-between gap-2 bg-rail px-4 py-3 text-rail-ink">
            <div>
              <h2 className="m-0 font-display text-[16px] font-semibold text-white">MI007 bot</h2>
              <p className="m-0 text-[11.5px] text-rail-ink-2">Ask about any feature and I will take you there</p>
            </div>
            <div className="flex items-center gap-1">
              {icon.moved && (
                <button className="rounded px-2 py-1 text-[12px] text-rail-ink hover:text-white" onClick={icon.reset} title="Put the robot icon back in the bottom-right corner">
                  Reset position
                </button>
              )}
              {turns.length > 0 && (
                <button className="rounded px-2 py-1 text-[12px] text-rail-ink hover:text-white" onClick={() => setTurns([])}>
                  Clear
                </button>
              )}
              <button className="rounded px-2 py-1 text-lg leading-none text-rail-ink hover:text-white" aria-label="Close assistant" onClick={() => setOpen(false)}>
                ×
              </button>
            </div>
          </header>

          <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-[14px]" aria-live="polite">
            {turns.length === 0 && (
              <div className="space-y-2">
                <p className="m-0 text-ink-2">{GREETING} I can explain what this app does and open the right page for you. Try:</p>
                <div className="flex flex-wrap gap-1.5">
                  {STARTERS.map((s) => (
                    <button key={s} onClick={() => send(undefined, s)} className="rounded-full border border-rule px-2.5 py-1 text-[12.5px] text-ink hover:bg-surface-2">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t, i) => (
              <div key={i} className={cx('flex', t.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div className={cx('max-w-[85%] rounded-[12px] px-3 py-2', t.role === 'user' ? 'bg-rail text-white' : 'bg-surface-2 text-ink')}>
                  <p className="m-0 whitespace-pre-wrap">{t.content}</p>
                  {t.navigate && (
                    <Link to={t.navigate.path} className="mt-1.5 inline-block text-[13px] font-medium">
                      Go to {t.navigate.label} →
                    </Link>
                  )}
                  {t.source === 'rules' && <p className="m-0 mt-1 text-[11px] text-ink-3">Keyword match; the language model was not used.</p>}
                </div>
              </div>
            ))}
            {m.isPending && <p className="m-0 text-[13px] text-ink-3">Thinking…</p>}
            {m.isError && (
              <p role="alert" className="m-0 text-[13px] text-loss">
                {m.error.message}
              </p>
            )}
          </div>

          <form onSubmit={send} className="border-t border-rule p-3">
            <div className="flex items-end gap-2">
              <label htmlFor="chat-input" className="sr-only">
                Message the assistant
              </label>
              <textarea
                id="chat-input"
                ref={inputRef}
                rows={1}
                maxLength={1000}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) send(e as unknown as FormEvent)
                }}
                placeholder="e.g. show me the concall analyzer"
                className="max-h-28 min-h-[38px] flex-1 resize-none rounded-[8px] border border-rule bg-surface px-3 py-2 text-[14px] text-ink"
              />
              <button
                type="submit"
                disabled={!text.trim() || m.isPending}
                className="rounded-[8px] bg-accent px-3 py-2 text-[14px] font-semibold text-[#14213d] disabled:opacity-50"
              >
                Send
              </button>
            </div>
            <p className="m-0 mt-1.5 text-[11px] text-ink-3">Guidance only, not investment advice. Answers may be wrong.</p>
          </form>
        </section>
      )}

      {!open && greet && !icon.dragging && (
        <div role="status" style={place.bubble} className="fixed z-40 flex max-w-[260px] items-start gap-2 rounded-[12px] border border-rule bg-surface px-3.5 py-2.5 text-[13.5px] text-ink shadow-lg">
          <button
            type="button"
            onClick={() => {
              dismissGreet()
              setOpen(true)
            }}
            className="text-left font-medium hover:text-link"
            data-testid="chat-greeting"
          >
            {GREETING}
          </button>
          <button type="button" onClick={dismissGreet} aria-label="Dismiss greeting" className="-mr-1 -mt-0.5 shrink-0 px-1 text-[15px] leading-none text-ink-3 hover:text-ink">
            ×
          </button>
          {/* tail pointing at the robot icon */}
          <span
            aria-hidden
            className={cx(
              'absolute h-3 w-3 rotate-45 bg-surface',
              place.above ? '-bottom-[7px] border-b border-r border-rule' : '-top-[7px] border-l border-t border-rule',
              place.right ? 'right-6' : 'left-6',
            )}
          />
        </div>
      )}
      <button
        {...icon.handlers}
        onClick={() => {
          if (icon.consumeDrag()) return
          dismissGreet()
          setOpen((o) => !o)
        }}
        style={{ left: icon.pos.x, top: icon.pos.y, touchAction: 'none' }}
        aria-expanded={open}
        aria-controls="chat-assistant"
        aria-label={open ? 'Close MI007 bot' : 'Open MI007 bot chat assistant'}
        aria-describedby="chat-icon-hint"
        title="Click to chat · drag to move"
        data-testid="chat-icon"
        className={cx(
          'group fixed z-50 flex h-14 w-14 select-none items-center justify-center rounded-full bg-rail text-accent shadow-xl ring-2 ring-accent/40 focus-visible:outline-none focus-visible:ring-4',
          icon.dragging ? 'scale-110 cursor-grabbing' : 'cursor-grab transition-transform hover:scale-105',
        )}
      >
        <span id="chat-icon-hint" className="sr-only">
          Drag to move, or use the arrow keys while focused.
        </span>
        {open ? (
          <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        ) : (
          <svg width="30" height="30" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" data-testid="robot-icon">
            {/* antenna */}
            <path d="M12 2.5v3" />
            <circle cx="12" cy="2.2" r="1" fill="currentColor" stroke="none" />
            {/* head */}
            <rect x="4.5" y="5.5" width="15" height="11" rx="3" />
            {/* ears */}
            <path d="M2.5 10v3M21.5 10v3" />
            {/* eyes */}
            <circle cx="9" cy="10.5" r="1.4" fill="currentColor" stroke="none" />
            <circle cx="15" cy="10.5" r="1.4" fill="currentColor" stroke="none" />
            {/* mouth */}
            <path d="M9.5 13.6h5" />
            {/* body */}
            <path d="M8 16.5v2a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2" />
          </svg>
        )}
        {!open && <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full bg-accent ring-2 ring-surface group-hover:animate-ping" aria-hidden />}
      </button>
    </>
  )
}
