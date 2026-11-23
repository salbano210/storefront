import { formatters } from '../api'
import { CriteriaTable } from './CriteriaTable.jsx'
import { Facts } from './ui.jsx'
import { eventStory } from './logModel.js'

/* ---------------------------------------------------------------------------
 * The body of an expanded log row: what the step was asked, what came back, and the
 * criteria it was judged against -- in that order, in words.
 *
 * The raw event sits in one disclosure below. It is there for when the summary is not
 * enough, not as the first thing to read: the point of this pane is that somebody who
 * has never seen a JSON payload can follow the conversation and find the step that
 * went wrong.
 * ------------------------------------------------------------------------- */

/** The criteria a payload carries, wherever in the payload they sit. */
function checksOf(payload) {
  if (Array.isArray(payload.checks)) return payload.checks
  if (Array.isArray(payload.offers)) return payload.offers.flatMap((offer) => offer.checks || [])
  if (payload.check) return [payload.check]
  return []
}

/** One muted line rather than a dozen labelled rows: the facts worth scanning. */
function metaLine(event) {
  const tokens = (event.tokens_in || 0) + (event.tokens_out || 0)
  return [
    `#${event.id}`,
    formatters.dateTime(event.created_at),
    event.agent,
    event.status,
    event.model,
    event.latency_ms == null ? null : `${event.latency_ms} ms`,
    tokens ? `${tokens.toLocaleString()} tokens` : null,
    event.cost_usd ? formatters.cost(event.cost_usd) : null,
    event.config_version == null ? null : `config v${event.config_version}`,
  ]
    .filter(Boolean)
    .join(' · ')
}

export default function EventDetail({ row }) {
  const event = row.event
  const story = eventStory(event)
  const payload = event.payload || {}
  const checks = checksOf(payload)

  return (
    <div className="log-detail-body">
      <Facts
        rows={[
          { label: 'Action', value: story.action },
          { label: 'Input', value: story.input.join('  ·  '), mono: true },
          { label: 'Output', value: story.output },
        ]}
      />

      {story.note && <p className="story-note">{story.note}</p>}

      {checks.length > 0 && <CriteriaTable checks={checks} />}

      <p className="story-meta mono">{metaLine(event)}</p>

      {event.payload ? (
        <details className="story-raw">
          <summary>Raw event</summary>
          <pre className="log-json">{JSON.stringify(payload, null, 2)}</pre>
        </details>
      ) : (
        <p className="muted small">No payload recorded for this event.</p>
      )}
    </div>
  )
}
