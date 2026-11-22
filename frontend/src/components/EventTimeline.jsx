import { formatters } from '../api'
import { CriteriaTable } from './CriteriaTable.jsx'
import { JsonBlock } from './ui.jsx'

const AGENT_LABELS = {
  router_agent: 'Intent agent',
  gift_request_agent: 'Gift request agent',
  general_handler: 'Front desk',
  admin: 'Admin',
  guard: 'Guard',
}

const AGENT_TONES = {
  router_agent: 'slate',
  gift_request_agent: 'coral',
  general_handler: 'amber',
  admin: 'ink',
  guard: 'red',
}

const EVENT_TONES = {
  message_received: 'neutral',
  intent_classified: 'slate',
  agent_handoff: 'slate',
  agent_resumed: 'slate',
  profile_lookup: 'sage',
  criteria_evaluated: 'sage',
  tool_call_started: 'neutral',
  tool_call_completed: 'neutral',
  tool_call_failed: 'red',
  guardrail_blocked: 'red',
  guardrail_adjusted: 'amber',
  gift_issued: 'coral',
  message_sent: 'coral',
  llm_response: 'muted',
  llm_error: 'red',
  llm_fallback: 'amber',
  budget_blocked: 'red',
  config_updated: 'ink',
  config_reset: 'ink',
  session_started: 'muted',
}

export function agentLabel(agent) {
  if (!agent) return '—'
  return AGENT_LABELS[agent] || agent
}

export function agentTone(agent) {
  return AGENT_TONES[agent] || 'slate'
}

export function eventTone(type) {
  return EVENT_TONES[type] || 'neutral'
}

function Detail({ event }) {
  const payload = event.payload || {}

  if (event.type === 'criteria_evaluated' && payload.policy === 'gifting') {
    return (
      <>
        <CriteriaTable checks={payload.checks || []} />
        {payload.eligible ? (
          <p className="detail-note ok">All configured gifting criteria were met.</p>
        ) : (
          <p className="detail-note bad">
            Unmet: {(payload.checks || []).filter((c) => !c.passed).map((c) => c.label).join(', ')}
          </p>
        )}
        <p className="detail-hint">
          Evaluated against verified profile values only. Creator-stated numbers were ignored.
        </p>
      </>
    )
  }

  if (event.type === 'guardrail_blocked') {
    return (
      <div className="guardrail">
        <strong>Guardrail: {payload.guardrail || 'blocked'}</strong>
        {payload.checks && <CriteriaTable checks={payload.checks} />}
        {payload.allowed_labels && (
          <p className="detail-hint">Allowed labels: {payload.allowed_labels.join(', ')}</p>
        )}
        {payload.expected && (
          <p className="detail-hint">
            Expected <code>{payload.expected}</code>, got <code>{payload.attempted}</code>.
          </p>
        )}
      </div>
    )
  }

  if (event.type === 'gift_issued') {
    return (
      <div className="receipt">
        <div>
          <span className="receipt-key">Order</span>
          <span className="mono">{payload.order_id}</span>
        </div>
        <div>
          <span className="receipt-key">Product</span>
          <span>
            {payload.product} <span className="mono muted">({payload.sku})</span>
          </span>
        </div>
        <div>
          <span className="receipt-key">Ship to</span>
          <span>{payload.shipping_address}</span>
        </div>
      </div>
    )
  }

  if (event.type === 'llm_response') {
    return (
      <>
        <div className="llm-meta">
          <span className="mono">{payload.model}</span>
          {payload.used_fallback && <span className="badge badge-amber">fallback</span>}
          <span className="muted">
            {event.tokens_in ?? '—'} in / {event.tokens_out ?? '—'} out ·{' '}
            {formatters.cost(event.cost_usd)} · {event.latency_ms ?? '—'}ms
          </span>
        </div>
        {payload.tool_calls?.length > 0 && (
          <JsonBlock value={payload.tool_calls} label="Requested tool calls" open />
        )}
        {payload.parsed && <JsonBlock value={payload.parsed} label="Structured output" open />}
        {payload.text && (
          <div className="llm-text">
            <span className="receipt-key">Model text</span>
            <p>{payload.text}</p>
          </div>
        )}
        <JsonBlock value={payload} label="Model call payload (system prompt withheld, messages + tool calls shown)" />
      </>
    )
  }

  if (event.type === 'message_received' || event.type === 'message_sent') {
    return (
      <>
        <p className="quote">{payload.message || payload.reply}</p>
        <JsonBlock value={payload} label="Payload" />
      </>
    )
  }

  return <JsonBlock value={payload} label="Payload" open />
}


export default function EventTimeline({ events, onSelect, selectedId }) {
  if (!events.length) {
    return (
      <div className="empty">
        <h3>No events yet</h3>
        <p>
          Open the creator chat, send a message, and every routing decision, tool call and guardrail
          check will appear here in real time.
        </p>
      </div>
    )
  }

  return (
    <ol className="timeline">
      {events.map((event) => {
        const selected = selectedId === event.id
        const tone = eventTone(event.type)
        return (
          <li key={event.id} className={`timeline-item tone-${tone}`}>
            <button
              type="button"
              className={`timeline-head ${selected ? 'selected' : ''}`}
              onClick={() => onSelect?.(selected ? null : event.id)}
            >
              <span className="timeline-time mono">{formatters.time(event.created_at)}</span>
              <span className={`event-type type-${tone}`}>{event.type}</span>
              <span className={`agent-tag agent-${agentTone(event.agent)}`}>
                {agentLabel(event.agent)}
              </span>
              <span className="timeline-summary">{event.summary}</span>
              {event.status !== 'ok' && (
                <span className={`badge badge-${event.status === 'blocked' ? 'red' : 'amber'}`}>
                  {event.status}
                </span>
              )}
              {event.config_version != null && (
                <span className="config-chip" title="Brand configuration version in force">
                  v{event.config_version}
                </span>
              )}
            </button>
            {selected && (
              <div className="timeline-body">
                <div className="timeline-meta">
                  <span>#{event.id}</span>
                  {event.conversation_id && <span className="mono">{event.conversation_id}</span>}
                  {event.creator_id && <span className="mono">{event.creator_id}</span>}
                  <span>{formatters.dateTime(event.created_at)}</span>
                </div>
                <Detail event={event} />
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}

