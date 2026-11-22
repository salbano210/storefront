import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, formatters } from '../api'
import { useSession } from '../session.jsx'
import EventTimeline from '../components/EventTimeline.jsx'
import { Badge, ErrorNote, Spinner } from '../components/ui.jsx'

const EMPTY_FILTERS = {
  scope: 'session',
  creator_id: '',
  conversation_id: '',
  agent: '',
  event_type: '',
}

export default function Logs() {
  const { hasPasscode, savePasscode } = useSession()
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [events, setEvents] = useState([])
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [live, setLive] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [passcodeDraft, setPasscodeDraft] = useState('')
  const lastIdRef = useRef(0)

  const query = useCallback(
    (afterId) => {
      const params = new URLSearchParams()
      params.set('scope', filters.scope)
      params.set('limit', '300')
      params.set('after_id', String(afterId))
      for (const key of ['creator_id', 'conversation_id', 'agent', 'event_type']) {
        if (filters[key]) params.set(key, filters[key])
      }
      return params.toString()
    },
    [filters],
  )

  const load = useCallback(
    async (reset) => {
      if (reset) {
        lastIdRef.current = 0
        setEvents([])
      }
      const data = await api(`/api/logs?${query(lastIdRef.current)}`)
      if (data.events.length) {
        lastIdRef.current = Math.max(...data.events.map((event) => event.id))
        setEvents((previous) => (reset ? data.events : [...previous, ...data.events]))
      }
      setError(null)
    },
    [query],
  )

  useEffect(() => {
    api('/api/logs/meta')
      .then(setMeta)
      .catch(() => {})
  }, [events.length])

  useEffect(() => {
    setLoading(true)
    load(true)
      .catch((err) => setError(err))
      .finally(() => setLoading(false))
  }, [load])

  useEffect(() => {
    if (!live) return undefined
    const timer = setInterval(() => {
      load(false).catch(() => {})
    }, 1500)
    return () => clearInterval(timer)
  }, [live, load])

  const stats = useMemo(() => {
    const cost = events.reduce((total, event) => total + (event.cost_usd || 0), 0)
    const tokens = events.reduce(
      (total, event) => total + (event.tokens_in || 0) + (event.tokens_out || 0),
      0,
    )
    const blocked = events.filter((event) => event.status === 'blocked').length
    return { cost, tokens, blocked, count: events.length }
  }, [events])

  const conversations = meta?.conversations || []
  const needsPasscode = error?.code === 'admin_passcode_required'

  return (
    <div className="page logs-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Observability</p>
          <h1>Agent logs</h1>
          <p className="lede">
            Every event below was produced by real execution: routing decisions, handoffs, verified
            profile lookups, criteria evaluations, tool calls, guardrail blocks and the final reply.
          </p>
        </div>
        <div className="stat-row">
          <div className="stat">
            <span className="stat-value">{stats.count}</span>
            <span className="stat-label">events</span>
          </div>
          <div className="stat">
            <span className="stat-value">{stats.blocked}</span>
            <span className="stat-label">guardrail blocks</span>
          </div>
          <div className="stat">
            <span className="stat-value">{stats.tokens.toLocaleString()}</span>
            <span className="stat-label">tokens</span>
          </div>
          <div className="stat">
            <span className="stat-value">{formatters.cost(stats.cost)}</span>
            <span className="stat-label">model spend</span>
          </div>
        </div>
      </header>

      <div className="logs-toolbar">
        <label className="field inline">
          <span>Scope</span>
          <select
            value={filters.scope}
            onChange={(event) =>
              setFilters((current) => ({ ...current, scope: event.target.value }))
            }
          >
            <option value="session">My session</option>
            <option value="all">All sessions (needs passcode)</option>
          </select>
        </label>

        <label className="field inline">
          <span>Creator</span>
          <select
            value={filters.creator_id}
            onChange={(event) =>
              setFilters((current) => ({ ...current, creator_id: event.target.value }))
            }
          >
            <option value="">Any</option>
            {(meta?.creator_ids || []).map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>

        <label className="field inline">
          <span>Conversation</span>
          <select
            value={filters.conversation_id}
            onChange={(event) =>
              setFilters((current) => ({ ...current, conversation_id: event.target.value }))
            }
          >
            <option value="">Any</option>
            {conversations.map((item) => (
              <option key={item.id} value={item.id}>
                {item.creator_name} · {formatters.dateTime(item.created_at)}
              </option>
            ))}
          </select>
        </label>

        <label className="field inline">
          <span>Agent</span>
          <select
            value={filters.agent}
            onChange={(event) =>
              setFilters((current) => ({ ...current, agent: event.target.value }))
            }
          >
            <option value="">Any</option>
            {(meta?.agents || []).map((agent) => (
              <option key={agent} value={agent}>
                {agent}
              </option>
            ))}
          </select>
        </label>

        <label className="field inline">
          <span>Event type</span>
          <select
            value={filters.event_type}
            onChange={(event) =>
              setFilters((current) => ({ ...current, event_type: event.target.value }))
            }
          >
            <option value="">Any</option>
            {(meta?.event_types || []).map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>

        <div className="toolbar-actions">
          <button
            type="button"
            className={`btn btn-ghost btn-sm ${live ? 'active' : ''}`}
            onClick={() => setLive((value) => !value)}
          >
            {live ? '⏸ Pause live' : '▶ Resume live'}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => load(true)}>
            ↻ Refresh
          </button>
          {filters.conversation_id && (
            <a
              className="btn btn-ghost btn-sm"
              href={`/api/conversations/${filters.conversation_id}/export`}
            >
              ↓ Transcript JSON
            </a>
          )}
          {filters.scope === 'all' && <Badge tone="amber">all sessions</Badge>}
        </div>
      </div>

      {needsPasscode && (
        <form
          className="passcode-bar"
          onSubmit={(event) => {
            event.preventDefault()
            savePasscode(passcodeDraft)
            setPasscodeDraft('')
            setTimeout(() => {
              load(true).catch(setError)
            }, 50)
          }}
        >
          <span>Enter the demo passcode to view every session's logs:</span>
          <input
            type="password"
            value={passcodeDraft}
            onChange={(event) => setPasscodeDraft(event.target.value)}
            placeholder="passcode"
          />
          <button type="submit" className="btn btn-primary btn-sm">
            Unlock
          </button>
        </form>
      )}

      <ErrorNote error={needsPasscode ? null : error} onRetry={() => load(true)} />
      {loading && <Spinner label="Loading events" />}

      <EventTimeline events={events} selectedId={selectedId} onSelect={setSelectedId} />
    </div>
  )
}
