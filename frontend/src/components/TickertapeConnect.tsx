import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, post, useApi } from '../api/client'
import { SourcesSchema, type Sources } from '../api/schemas'
import { fmtDateTime } from '../lib/format'
import { Button, Notice, StateBadge } from './ui'

// Tickertape MCP session (OAuth, read-only market_data scope). The API registers itself with Tickertape, issues a fresh
// authorisation link, and receives the approval on its own callback, so no token ever reaches the browser. The page
// only opens the link and then re-reads the source status until Tickertape reports the session as authorised.

export type TickertapeAuth = {
  state: string
  reason?: string
  last_error?: string | null
  login_pending?: boolean
  scope?: string
  expires_at?: number | null
  refreshable?: boolean
  authorised_at?: string
} | null

function useTickertapeSource() {
  const src = useApi<Sources>(['sources'], '/sources/status', undefined, { schema: SourcesSchema as any, refetchInterval: 60_000, retry: 1 })
  const c = src.data?.connectors.find((x: any) => x.key === 'tickertape_mcp') as any
  return { auth: (c?.auth ?? null) as TickertapeAuth, connector: c, loading: src.isLoading }
}

function useTickertapeSession(auth: TickertapeAuth) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!waiting) return
    if (auth?.state === 'authorized') {
      setWaiting(false)
      qc.invalidateQueries()
      return
    }
    const t = setInterval(() => qc.invalidateQueries({ queryKey: ['sources'] }), 3000)
    const stop = setTimeout(() => setWaiting(false), 180_000)
    return () => {
      clearInterval(t)
      clearTimeout(stop)
    }
  }, [waiting, auth?.state, qc])

  const connect = async () => {
    setBusy(true)
    setError(null)
    // Open the tab synchronously so the browser does not block it as a pop-up, then point it at the fresh link.
    const tab = window.open('about:blank', '_blank')
    try {
      const r = await post<any>('/sources/tickertape/login')
      const url = r.tickertape?.login_url
      if (url && tab) tab.location.href = url
      else if (url) window.location.href = url
      else tab?.close()
      setWaiting(true)
      await qc.invalidateQueries({ queryKey: ['sources'] })
    } catch (e) {
      tab?.close()
      setError(e instanceof Error ? e.message : 'Could not start the Tickertape login')
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async () => {
    if (!window.confirm('Disconnect Tickertape? Fundamentals from Tickertape stop until you connect again.')) return
    setBusy(true)
    setError(null)
    try {
      await post('/sources/tickertape/logout')
      setWaiting(false)
      await qc.invalidateQueries({ queryKey: ['sources'] })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not disconnect from Tickertape.')
    } finally {
      setBusy(false)
    }
  }

  const check = () => qc.invalidateQueries({ queryKey: ['sources'] })
  return { busy, waiting, error, connect, disconnect, check }
}

/** Header pill, like the Kite one: shown while Tickertape needs a login; links to the session panel once connected. */
export function TickertapeConnectButton({ auth }: { auth: TickertapeAuth }) {
  const { busy, waiting, error, connect } = useTickertapeSession(auth)
  if (!auth || auth.state === 'not_configured') return null
  if (auth.state === 'authorized') {
    return (
      <Link to="/status#tickertape-session" className="hidden whitespace-nowrap rounded-full bg-good-soft px-2.5 py-0.5 text-xs font-medium text-gain no-underline md:inline" title="Tickertape connected (read-only). Manage or disconnect on Data status.">
        ● Tickertape
      </Link>
    )
  }
  return (
    <span className="flex items-center gap-2">
      {error && <span className="hidden text-xs text-loss md:inline" role="alert">{error}</span>}
      {waiting && <span className="hidden text-xs text-ink-2 md:inline" role="status">Approve in the Tickertape tab…</span>}
      <button onClick={connect} disabled={busy} className="whitespace-nowrap rounded-full border border-accent px-2.5 py-0.5 text-xs font-medium text-ink hover:bg-accent-soft disabled:opacity-50" title={auth.last_error ?? 'Sign in to Tickertape (read-only) to fill fundamentals NSE filings lack'} data-testid="tickertape-connect">
        {auth.last_error ? 'Tickertape: connect again' : 'Connect Tickertape'}
      </button>
    </span>
  )
}

/** The Tickertape session on the Data status page: the current state and the action that fits it. */
export function TickertapeSessionPanel() {
  const { auth, connector, loading } = useTickertapeSource()
  const { busy, waiting, error, connect, disconnect, check } = useTickertapeSession(auth)
  const s = auth?.state
  const tools: number | undefined = connector?.tool_count
  const blocked: string[] = connector?.blocked_tools ?? []
  return (
    <div className="space-y-2.5 px-4 py-3" data-testid="tickertape-session" id="tickertape-session">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        {loading ? (
          <span className="text-ink-2">Checking…</span>
        ) : (
          <StateBadge state={s ?? 'unknown'} label={s === 'authorized' ? 'Connected' : s === 'login_required' ? (auth?.last_error ? 'Disconnected by Tickertape' : 'Login needed') : s === 'not_configured' ? 'Not configured' : s?.replace(/_/g, ' ')} />
        )}
        {s === 'authorized' && auth?.scope && <span>scope {auth.scope}</span>}
        {s === 'authorized' && auth?.authorised_at && <span className="text-ink-3">since {fmtDateTime(auth.authorised_at)}</span>}
        {s === 'authorized' && auth?.expires_at && (
          <span className="text-ink-3">
            access token valid to {fmtDateTime(new Date(auth.expires_at * 1000).toISOString())}
            {auth.refreshable ? ', renews automatically' : ''}
          </span>
        )}
        {s === 'authorized' && tools !== undefined && <span className="text-ink-3">{tools} read-only tools{blocked.length ? `, ${blocked.length} blocked` : ''}</span>}
      </div>
      {s === 'not_configured' && <p className="m-0 text-[13px] text-ink-2">Set TICKERTAPE_MCP_URL in .env to use Tickertape as a fallback for fundamentals.</p>}
      {s === 'login_required' && <p className="m-0 text-[13px] text-ink-2">{auth?.last_error ?? auth?.reason}</p>}
      {s !== 'not_configured' && (
        <div className="flex flex-wrap items-center gap-2">
          {s === 'authorized' ? (
            <>
              <Button onClick={check}>Check session</Button>
              <Button variant="quiet" onClick={disconnect} busy={busy} data-testid="tickertape-disconnect">
                Disconnect Tickertape
              </Button>
            </>
          ) : (
            <>
              <Button variant="primary" onClick={connect} busy={busy} data-testid="tickertape-login">
                {auth?.last_error ? 'Connect again' : 'Connect Tickertape'}
              </Button>
              <Button onClick={check} data-testid="tickertape-check">
                I have approved it
              </Button>
              {waiting && <span className="text-xs text-ink-2" role="status">Approve in the Tickertape tab…</span>}
            </>
          )}
        </div>
      )}
      <p className="m-0 text-xs text-ink-3">
        Tickertape fills fundamental metrics that NSE filings do not provide. Access is read-only (market data scope); tools that could trade or read
        your portfolio are blocked. Tokens stay on the server, renew automatically, and are deleted when you disconnect.
      </p>
      {error && <Notice tone="warn">{error}</Notice>}
    </div>
  )
}
