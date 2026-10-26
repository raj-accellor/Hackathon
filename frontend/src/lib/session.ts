import { useEffect, useState } from 'react'

/** NSE cash-market session in IST, from the clock alone. Exchange holidays are not known here,
    so the UI must say so wherever it shows the result. Times are minutes after midnight IST. */
export const SESSION = { preOpen: 9 * 60, open: 9 * 60 + 15, close: 15 * 60 + 30, postClose: 16 * 60 } as const

export type Phase = 'pre_open' | 'open' | 'closing' | 'closed'

export type SessionState = {
  phase: Phase
  /** Minutes after midnight, IST. */
  minutes: number
  /** 0 = Sunday … 6 = Saturday, IST. */
  weekday: number
  tradingDay: boolean
  label: string
  detail: string
}

const IST_OFFSET_MIN = 330

export function istParts(now: Date): { minutes: number; weekday: number } {
  const t = new Date(now.getTime() + IST_OFFSET_MIN * 60_000)
  return { minutes: t.getUTCHours() * 60 + t.getUTCMinutes(), weekday: t.getUTCDay() }
}

export function fmtDuration(min: number): string {
  const h = Math.floor(min / 60)
  const m = min % 60
  return h > 0 ? `${h} h ${String(m).padStart(2, '0')} m` : `${m} min`
}

export function fmtClock(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`
}

/** The next trading weekday after `weekday`, as a word relative to today. */
function nextOpenDay(weekday: number): string {
  if (weekday === 5) return 'Monday'
  if (weekday === 6) return 'Monday'
  return 'tomorrow'
}

export function sessionState(now: Date): SessionState {
  const { minutes, weekday } = istParts(now)
  const base = { minutes, weekday, tradingDay: weekday >= 1 && weekday <= 5 }
  if (!base.tradingDay) {
    return { ...base, phase: 'closed', label: 'Market closed for the weekend', detail: 'Opens Monday at 09:15 IST' }
  }
  if (minutes < SESSION.preOpen) {
    return { ...base, phase: 'closed', label: 'Market closed', detail: `Pre-open starts at 09:00 IST, in ${fmtDuration(SESSION.preOpen - minutes)}` }
  }
  if (minutes < SESSION.open) {
    return { ...base, phase: 'pre_open', label: 'Pre-open session', detail: `Trading starts at 09:15 IST, in ${fmtDuration(SESSION.open - minutes)}` }
  }
  if (minutes < SESSION.close) {
    return { ...base, phase: 'open', label: 'Market open', detail: `Closes at 15:30 IST, in ${fmtDuration(SESSION.close - minutes)}` }
  }
  if (minutes < SESSION.postClose) {
    return { ...base, phase: 'closing', label: 'Closing session', detail: 'Closing-price orders until 16:00 IST' }
  }
  return { ...base, phase: 'closed', label: 'Market closed', detail: `Opens ${nextOpenDay(weekday)} at 09:15 IST` }
}

/** "Tuesday 27 October", in IST regardless of the browser's zone. */
export function istDateHeading(now: Date): string {
  return new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'Asia/Kolkata' }).format(now).replace(',', '')
}

/** Current time, re-read every `ms`. */
export function useNow(ms = 30_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}
