import { describe, expect, it } from 'vitest'
import { fmtDuration, istDateHeading, sessionState } from '../lib/session'

// IST is UTC+05:30, so 03:45 UTC is 09:15 IST.
const at = (iso: string) => new Date(iso)

describe('sessionState', () => {
  it('is closed before pre-open and counts down to it', () => {
    const s = sessionState(at('2026-10-27T02:00:00Z')) // Tue 07:30 IST
    expect(s.phase).toBe('closed')
    expect(s.detail).toBe('Pre-open starts at 09:00 IST, in 1 h 30 m')
  })

  it('is pre-open from 09:00 to 09:15', () => {
    expect(sessionState(at('2026-10-27T03:30:00Z')).phase).toBe('pre_open') // 09:00
    expect(sessionState(at('2026-10-27T03:44:00Z')).detail).toBe('Trading starts at 09:15 IST, in 1 min')
  })

  it('is open from 09:15 up to but not including 15:30', () => {
    expect(sessionState(at('2026-10-27T03:45:00Z')).phase).toBe('open') // 09:15
    const s = sessionState(at('2026-10-27T07:55:00Z')) // 13:25
    expect(s.phase).toBe('open')
    expect(s.detail).toBe('Closes at 15:30 IST, in 2 h 05 m')
    expect(sessionState(at('2026-10-27T10:00:00Z')).phase).toBe('closing') // 15:30
  })

  it('is closed after 16:00 and points at the next weekday', () => {
    expect(sessionState(at('2026-10-27T10:30:00Z')).detail).toBe('Opens tomorrow at 09:15 IST') // Tue 16:00
    expect(sessionState(at('2026-10-30T12:00:00Z')).detail).toBe('Opens Monday at 09:15 IST') // Fri 17:30
  })

  it('treats the weekend as closed all day, using the IST date', () => {
    // Fri 20:00 UTC is already Sat 01:30 IST.
    const s = sessionState(at('2026-10-30T20:00:00Z'))
    expect(s.tradingDay).toBe(false)
    expect(s.label).toBe('Market closed for the weekend')
  })
})

describe('formatting', () => {
  it('formats durations and IST dates', () => {
    expect(fmtDuration(45)).toBe('45 min')
    expect(fmtDuration(125)).toBe('2 h 05 m')
    expect(istDateHeading(at('2026-10-26T20:00:00Z'))).toBe('Tuesday 27 October')
  })
})
