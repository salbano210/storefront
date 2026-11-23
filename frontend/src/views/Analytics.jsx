import { useCallback, useEffect, useState } from 'react'
import { api, formatters } from '../api'
import { useSession } from '../session.jsx'
import { EmptyState, ErrorNote, Spinner } from '../components/ui.jsx'
import { RateLine, StackedBars } from '../components/Chart.jsx'
import {
  approvalChart,
  dispositions,
  dispositionChart,
  headline,
  intentChart,
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

function Legend({ rows, toneKey }) {
  return (
    <div className="analytics-legend">
      {rows.map((row) => (
        <span className="legend-item" key={row[toneKey]}>
          <span className={`dot dot-${row.tone}`} />
          {row.label}
          <span className="muted">{row.count}</span>
        </span>
      ))}
    </div>
  )
}

export default function Analytics() {
  const { hasPasscode } = useSession()
  const [days, setDays] = useState(30)
  const [interval, setBucketInterval] = useState('day')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ days: String(days), interval })
      setData(await api(`/api/analytics?${params}`))
      setError(null)
    } catch (err) {
      setError(err)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [days, interval])

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
  const intentRows = data ? legend(data) : []
  const dispositionRows = data ? dispositions(data) : []
  const outcomeRows = data ? outcomes(data) : []
  const sampleCount = data?.totals.seeded ?? 0
  const hasSample = sampleCount > 0
  const approvalRate = data?.totals.approval_rate
  const decided = data?.totals.decided ?? 0
  const rateLabel =
    approvalRate === null || approvalRate === undefined ? '—' : formatters.pct(approvalRate)

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Measurement</p>
          <h1>Analytics</h1>
          <p className="lede">
            How many conversations we had, what they were about, and how they ended. Every number
            is read from the conversation record the agents wrote — not re-derived from the log.
            Every session is included.
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
              <span className="stat-value">{rateLabel}</span>
              <span className="stat-label">approval rate · {decided} decided</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats.escalated}</span>
              <span className="stat-label">escalated to a human</span>
            </div>
          </div>
        )}
      </header>

      <div className="logs-toolbar">
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
              {busy
                ? 'Working…'
                : hasSample
                  ? 'Regenerate sample history'
                  : 'Generate sample history'}
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
              <h2>Conversations by intent</h2>
              <Legend rows={intentRows} toneKey="intent" />
            </header>
            <StackedBars build={(width) => intentChart(data, { width })} />
          </section>

          <section className="analytics-card">
            <header>
              <h2>Dispositions over time</h2>
              <Legend rows={dispositionRows} toneKey="disposition" />
            </header>
            <StackedBars build={(width) => dispositionChart(data, { width })} />
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

          <section className="analytics-card">
            <header>
              <h2>Gift request approval rate</h2>
              <span className="muted small">
                Approved share of gift requests that reached a decision
                {decided ? ` · ${rateLabel} overall, from ${decided}` : ' · nothing decided yet'}
              </span>
            </header>
            <RateLine build={(width) => approvalChart(data, { width })} />
            <p className="muted small chart-note">
              A gap means no gift request was decided in that bucket, so there is no rate to plot.
              Questions and escalations are not counted as denials.
            </p>
          </section>
        </>
      )}
    </div>
  )
}
