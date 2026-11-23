import { formatters } from '../api'
import { CriteriaTable } from './CriteriaTable.jsx'
import { Facts } from './ui.jsx'

/* ---------------------------------------------------------------------------
 * The body of an expanded log row: the fields that did not fit in a column,
 * then the payload exactly as the agent recorded it.
 *
 * The JSON is not hidden behind a second toggle. An expanded row is already the
 * request for detail, and the payload is the evidence.
 * ------------------------------------------------------------------------- */

/** The criteria a payload carries, wherever in the payload they sit. */
function checksOf(payload) {
  if (Array.isArray(payload.checks)) return payload.checks
  if (Array.isArray(payload.offers)) return payload.offers.flatMap((offer) => offer.checks || [])
  if (payload.check) return [payload.check]
  return []
}

export default function EventDetail({ row }) {
  const event = row.event
  const payload = event.payload || {}
  const tokens = (event.tokens_in || 0) + (event.tokens_out || 0)
  const checks = checksOf(payload)

  return (
    <div className="log-detail-body">
      <Facts
        rows={[
          { label: 'Event', value: `#${event.id}`, mono: true },
          { label: 'Time', value: formatters.dateTime(event.created_at), mono: true },
          { label: 'Type', value: event.type, mono: true },
          { label: 'Status', value: event.status },
          { label: 'Agent', value: event.agent },
          { label: 'Model', value: event.model, mono: true },
          { label: 'Conversation', value: event.conversation_id, mono: true },
          { label: 'Session', value: event.session_id, mono: true },
          { label: 'Creator', value: event.creator_id, mono: true },
          {
            label: 'Config version',
            value: event.config_version == null ? null : `v${event.config_version}`,
            mono: true,
          },
          { label: 'Latency', value: event.latency_ms == null ? null : `${event.latency_ms} ms` },
          {
            label: 'Tokens',
            value: tokens ? `${event.tokens_in || 0} in / ${event.tokens_out || 0} out` : null,
          },
          { label: 'Cost', value: formatters.cost(event.cost_usd) },
        ]}
      />

      {checks.length > 0 && <CriteriaTable checks={checks} />}

      {event.payload ? (
        <pre className="log-json">{JSON.stringify(payload, null, 2)}</pre>
      ) : (
        <p className="muted small">No payload recorded for this event.</p>
      )}
    </div>
  )
}
