import { useCallback, useEffect, useState } from 'react'
import { api, formatters } from '../api'
import { useSession } from '../session.jsx'
import { EmptyState, ErrorNote, Spinner } from '../components/ui.jsx'
import {
  chart,
  dispositions,
  headline,
  legend,
  outcomes,
} from '../components/analyticsModel.js'

const RANGES = [
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
]

const INTERVALS = [
  { id: 'day', label: 'Day' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
]

// How much history the button generates. Sized to fill a 90-day chart without
// taking more than a couple of seconds.
const SAMPLE = { days: 90, count: 120 }

export default function Analytics() {
  const { hasPasscode } = useSession()
  const [days, setDays] = useState(30)
  const [interval, setBucketInterval] = useState('day')
  const [scope, setScope] = useState('session')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ days: String(days), interval, scope })
      setData(await api(`/api/analytics?${params}`))
      setError(null)
    } catch (err) {
      setError(err)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [days, interval, scope])

  useEffect(() => {
    load()
  }, [load])

  const runSampleAction = useCallback(
    async (method) => {
      setBusy(true)
      try {
        await api('/api/admin/sample-history', {
          method,
          body: method === 'POST' ? SAMPLE : undefined,
        })
        setError(null)
        await load()
      } catch (err) {
        setError(err)
      } finally {
        setBusy(false)
      }
    },
    [load],
  )

  const stats = data ? headline(data) : null
  const columns = data ? chart(data).columns : []
  const intentRows = data ? legend(data) : []
  const dispositionRows = data ? dispositions(data) : []
  const outcomeRows = data ? outcomes(data) : []
  const sampleCount = data?.totals.seeded ?? 0
  const hasSample = sampleCount > 0

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Measurement</p>
          <h1>Analytics</h1>
          <p className="lede">
            How many conversations we had, what they were about, and how they ended. Every number
            is read from the conversation record the agents wrote — not re-derived from the log.
          </p>
        </div>
        {stats && (
          <div className="stat-row">
            <div className="stat">
              <span className="stat-value">{stats.conversations}</span>
              <span className="stat-label">conversations</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats.giftRequests}</span>
              <span className="stat-label">gift requests</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats.approved}</span>
              <span className="stat-label">approved</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats.denied}</span>
              <span className="stat-label">denied</span>
            </div>
          </div>
        )}
      </header>

      <div className="logs-toolbar">
        <label className="field inline">
          <span>Scope</span>
          <select value={scope} onChange={(event) => setScope(event.target.value)}>
            <option value="session">My session</option>
            <option value="all">All sessions (needs passcode)</option>
          </select>
        </label>

        <label className="field inline">
          <span>Date range</span>
          <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {RANGES.map((range) => (
              <option key={range.days} value={range.days}>
                {range.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field inline">
          <span>Interval</span>
          <select value={interval} onChange={(event) => setBucketInterval(event.target.value)}>
            {INTERVALS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        <div className="toolbar-actions">
          {data && (
            <span className="muted small">
              {data.range.from} → {data.range.to}
              {hasSample ? ` · ${sampleCount} sample` : ''}
              {loading ? ' · refreshing…' : ''}
            </span>
          )}
          {hasPasscode && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => runSampleAction('POST')}
              disabled={busy}
              title="Generate backdated conversations by driving the real agents with an offline model. They are labelled as sample data."
            >
              {busy ? 'Working…' : hasSample ? 'Regenerate sample history' : 'Generate sample history'}
            </button>
          )}
          {hasPasscode && hasSample && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => runSampleAction('DELETE')}
              disabled={busy}
            >
              Clear sample data
            </button>
          )}
        </div>
      </div>

      <ErrorNote error={error} onRetry={load} />
      {loading && !data && <Spinner label="Loading analytics" />}

      {data && stats.conversations === 0 && (
        <EmptyState
          title="No conversations in this range"
          action={
            hasPasscode ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => runSampleAction('POST')}
                disabled={busy}
              >
                {busy ? 'Working…' : 'Generate sample history'}
              </button>
            ) : null
          }
        >
          Chat with a creator, then come back. Or generate a backdated sample history — the agents
          run for real, only the wording and the timestamps are synthetic.
        </EmptyState>
      )}

      {data && stats.conversations > 0 && (
        <>
          <section className="analytics-card">
            <header>
              <h2>Conversation count by intent</h2>
              <div className="analytics-legend">
                {intentRows.map((row) => (
                  <span className="legend-item" key={row.intent}>
                    <span className={`dot dot-${row.tone}`} />
                    {row.label}
                    <span className="muted">{row.count}</span>
                  </span>
                ))}
              </div>
            </header>

            <div className="chart">
              {columns.map((column) => (
                <div className="chart-col" key={column.bucket} title={column.title}>
                  <div className="chart-stack">
                    {column.segments.map((segment) => (
                      <span
                        key={segment.intent}
                        className={`chart-seg seg-${segment.tone}`}
                        style={{ height: `${segment.height}%` }}
                      />
                    ))}
                  </div>
                  <span className="chart-label">{column.showLabel ? column.label : ''}</span>
                </div>
              ))}
            </div>

            <table className="criteria">
              <thead>
                <tr>
                  <th>Intent</th>
                  <th>Conversations</th>
                  <th>Share</th>
                </tr>
              </thead>
              <tbody>
                {intentRows.map((row) => (
                  <tr key={row.intent}>
                    <td>
                      <span className="criteria-label">{row.label}</span>
                    </td>
                    <td className="mono">{row.count}</td>
                    <td className="mono">{formatters.pct(row.count / stats.conversations)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="analytics-card">
            <header>
              <h2>Disposition</h2>
              <span className="muted small">
                How each of the {stats.conversations} conversations ended
              </span>
            </header>

            <ul className="disposition-list">
              {dispositionRows.map((row) => (
                <li className="disposition-row" key={row.disposition}>
                  <span className={`dot dot-${row.tone}`} />
                  <span className="disposition-name">{row.label}</span>
                  <span className="disposition-bar">
                    <span
                      className={`disposition-fill fill-${row.tone}`}
                      style={{ width: `${row.width}%` }}
                    />
                  </span>
                  <span className="disposition-count mono">{row.count}</span>
                  <span className="disposition-rate mono">{formatters.pct(row.rate)}</span>
                </li>
              ))}
            </ul>

            {outcomeRows.length > 0 && (
              <div className="outcome-note">
                <span className="muted small">
                  What those conversations actually did — this is what “Other” is made of.
                </span>
                <div className="chips">
                  {outcomeRows.map((row) => (
                    <span className={`badge badge-${row.tone}`} key={row.outcome}>
                      {row.label} · {row.count}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
