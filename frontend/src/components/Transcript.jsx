import { useMemo, useState } from 'react'

import { formatters } from '../api'
import { Badge } from './ui.jsx'
import { CriteriaTable } from './CriteriaTable.jsx'

/* ---------------------------------------------------------------------------
 * The readable view: a conversation transcript, with each agent turn carrying
 * its own internally-expandable trace (routing, tool calls, criteria, model
 * calls, guardrails). Raw events are still available under the Raw events tab.
 * ------------------------------------------------------------------------- */

const ICONS = {
  intent_classified: '🔀',
  agent_handoff: '➡',
  agent_resumed: '↩',
  profile_lookup: '🪪',
  criteria_evaluated: '📊',
  tool_call_completed: '🔧',
  tool_call_failed: '🔧',
  llm_response: '🤖',
  llm_fallback: '↺',
  guardrail_blocked: '🛡',
  guardrail_adjusted: '✎',
  injection_suspected: '⚠',
  gift_issued: '🎁',
  tag_applied: '🏷',
  budget_blocked: '⛔',
  config_updated: '⚙',
}

const TRACE_AGENTS = new Set(['router_agent', 'guard', 'admin'])

function bucketTitle(event) {
  const payload = event.payload || {}
  switch (event.type) {
    case 'intent_classified':
      return `route → ${payload.intent} · ${Math.round((payload.confidence || 0) * 100)}%`
    case 'agent_handoff':
      return `${payload.from} → ${payload.to}`
    case 'agent_resumed':
      return `${payload.agent} resumed`
    case 'profile_lookup':
      return 'verified profile lookup'
    case 'criteria_evaluated':
      if (payload.policy === 'pitch') {
        return `pitch score ${payload.score}/100 → ${payload.label} (${payload.decision})`
      }
      if (payload.selected && payload.selected !== 'all') {
        return `gift criteria · ${payload.selected}`
      }
      return 'gift criteria · every gift'
    case 'tool_call_completed':
    case 'tool_call_failed':
      return `${payload.tool || 'tool'}()`
    case 'llm_response':
      return event.model || 'model call'
    case 'guardrail_blocked':
      return `guardrail · ${payload.guardrail || 'blocked'}`
    case 'injection_suspected':
      return 'instruction-override attempt'
    case 'gift_issued':
      return `gift issued · ${payload.product || payload.sku}`
    case 'tag_applied':
      return `tag applied · ${payload.tag}`
    default:
      return event.type.replaceAll('_', ' ')
  }
}

function bucketDetail(event) {
  const payload = event.payload || {}
  switch (event.type) {
    case 'intent_classified':
      return payload.reasoning
    case 'tool_call_completed':
    case 'tool_call_failed': {
      const result = payload.result || {}
      if (result.ok === false) return result.error || result.message
      return result.message || null
    }
    case 'guardrail_blocked':
      return event.summary
    default:
      return null
  }
}

function Bucket({ event }) {
  const [open, setOpen] = useState(false)
  const payload = event.payload || {}
  const detail = bucketDetail(event)
  const hasBody = Boolean(
    payload.arguments ||
      payload.result ||
      payload.offers ||
      payload.components ||
      payload.message ||
      payload.patterns
  )

  return (
    <div className={`bucket ${event.status}`}>
      <button
        type="button"
        className="bucket-head"
        onClick={() => setOpen(!open)}
        disabled={!hasBody}
      >
        <span className="bucket-caret">{hasBody ? (open ? '▾' : '▸') : '·'}</span>
        <span className="bucket-icon">{ICONS[event.type] || '•'}</span>
        <span className="bucket-title">{bucketTitle(event)}</span>
        {detail && <span className="bucket-detail muted small">{detail}</span>}
        <span className="bucket-meta">
          {event.latency_ms ? `${event.latency_ms} ms` : ''}
          {event.cost_usd ? ` · ${formatters.cost(event.cost_usd)}` : ''}
        </span>
      </button>

      {open && (
        <div className="bucket-body">
          {payload.arguments && (
            <pre className="bucket-json">{JSON.stringify(payload.arguments, null, 2)}</pre>
          )}

          {payload.offers && (
            <div className="bucket-offers">
              {payload.offers.map((offer) => (
                <div key={offer.gift_id} className="bucket-offer">
                  <div className="bucket-offer-head">
                    <strong>{offer.name}</strong>
                    <Badge tone={offer.eligible ? 'sage' : 'red'}>
                      {offer.eligible ? 'qualifies' : 'does not qualify'}
                    </Badge>
                    <span className="muted small">{offer.sku}</span>
                  </div>
                  <CriteriaTable checks={offer.checks || []} />
                </div>
              ))}
            </div>
          )}

          {payload.components && (
            <ul className="bucket-components">
              {payload.components.map((component) => (
                <li key={component.key}>
                  <span>{component.label}</span>
                  <span className="muted small">{component.detail}</span>
                  <span className="mono">
                    {component.points} / {component.weight}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {payload.response_instructions && (
            <p className="bucket-note">
              <strong>Follow-up for this tag:</strong> {payload.response_instructions}
            </p>
          )}

          {payload.patterns && (
            <p className="bucket-note">
              Patterns: <code>{payload.patterns.join(', ')}</code> — {payload.action}
            </p>
          )}

          {payload.message && !payload.result && (
            <pre className="bucket-json">{payload.message}</pre>
          )}

          {payload.result && (
            <pre className="bucket-json">{JSON.stringify(payload.result, null, 2)}</pre>
          )}
        </div>
      )}
    </div>
  )
}

const OUTCOME_TONE = {
  gift_issued: 'sage',
  pitch_passed: 'sage',
  gift_duplicate: 'amber',
  gift_declined: 'red',
  pitch_rejected: 'red',
  gift_suggested: 'slate',
  inquiry: 'slate',
}

function emptyTurn(event, key) {
  return {
    key,
    creator: event.creator_id,
    agent: '',
    message: '',
    reply: '',
    outcome: null,
    buckets: [],
    all: [],
    startedAt: event.created_at,
  }
}

function groupTurns(events) {
  const turns = []
  let current = null

  for (const event of events) {
    if (event.type === 'message_received') {
      current = emptyTurn(event, `turn-${event.id}`)
      current.message = event.payload?.message || ''
      current.all.push(event)
      turns.push(current)
      continue
    }

    if (!current) {
      current = emptyTurn(event, `pre-${event.id}`)
      turns.push(current)
    }

    current.all.push(event)

    if (event.type === 'message_sent') {
      current.reply = event.payload?.reply || ''
      current.outcome = event.payload?.outcome || null
      continue
    }
    // The started event duplicates the completed event's arguments.
    if (event.type === 'tool_call_started') continue

    if (event.agent && !TRACE_AGENTS.has(event.agent) && !current.agent) {
      current.agent = event.agent
    }
    current.buckets.push(event)
  }

  return turns.map((turn) => {
    const cost = turn.all.reduce((sum, event) => sum + (event.cost_usd || 0), 0)
    const tokens = turn.all.reduce(
      (sum, event) => sum + (event.tokens_in || 0) + (event.tokens_out || 0),
      0
    )
    return { ...turn, cost, tokens, agent: turn.agent || 'agent' }
  })
}

function Turn({ turn }) {
  const [open, setOpen] = useState(true)
  const tools = turn.buckets.filter(
    (event) => event.type === 'tool_call_completed' || event.type === 'tool_call_failed'
  ).length

  return (
    <article className="turn">
      <div className="bubble creator">
        <div className="bubble-meta">
          {turn.creator} · {formatters.time(turn.startedAt)}
        </div>
        <p>{turn.message || '—'}</p>
      </div>

      {turn.buckets.length > 0 && (
        <div className="turn-trace">
          <button type="button" className="trace-head" onClick={() => setOpen(!open)}>
            <span className="bucket-caret">{open ? '▾' : '▸'}</span>
            <span className="trace-agent">{turn.agent}</span>
            <span className="muted small">
              {tools} tool call{tools === 1 ? '' : 's'} · {turn.tokens.toLocaleString()} tokens ·{' '}
              {formatters.cost(turn.cost)}
            </span>
          </button>
          {open && (
            <div className="trace-buckets">
              {turn.buckets.map((event) => (
                <Bucket key={event.id} event={event} />
              ))}
            </div>
          )}
        </div>
      )}

      {turn.reply && (
        <div className="bubble agent">
          <div className="bubble-meta">
            <span>{turn.agent}</span>
            {turn.outcome && (
              <Badge tone={OUTCOME_TONE[turn.outcome.type] || 'slate'}>
                {turn.outcome.type.replaceAll('_', ' ')}
              </Badge>
            )}
          </div>
          <p>{turn.reply}</p>
        </div>
      )}
    </article>
  )
}

export default function Transcript({ events }) {
  const turns = useMemo(() => groupTurns(events), [events])
  if (!turns.length) {
    return <p className="muted">No events yet. Send a message from a creator chat to see it here.</p>
  }
  return (
    <div className="transcript">
      {turns.map((turn) => (
        <Turn key={turn.key} turn={turn} />
      ))}
    </div>
  )
}

