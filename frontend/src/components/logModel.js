/* ---------------------------------------------------------------------------
 * The logs page's reading logic, kept apart from its rendering.
 *
 * Nothing here touches React, so the columns and the wording can be exercised
 * directly against a real event log -- which is how they are checked, rather
 * than by eye.
 * ------------------------------------------------------------------------- */

import { formatters } from '../api.js'

/** The decision each turn ended on. No icons: the type and the label carry it. */
export const OUTCOMES = {
  gift_issued: { tone: 'sage', label: 'Gift approved' },
  gift_duplicate: { tone: 'amber', label: 'Duplicate order suppressed' },
  gift_declined: { tone: 'red', label: 'Gift declined' },
  gift_suggested: { tone: 'slate', label: 'Product link shared' },
  details_required: { tone: 'amber', label: 'Eligible — more details needed' },
  inquiry: { tone: 'slate', label: 'Answered' },
  escalated: { tone: 'slate', label: 'Handed to a human' },
}

const AGENT_LABELS = {
  router_agent: 'intent agent',
  gift_request_agent: 'gifting agent',
  general_handler: 'front desk',
  escalation: 'escalation',
}

export function humanAgent(agent) {
  return AGENT_LABELS[agent] || String(agent || 'agent').replaceAll('_', ' ')
}

export function toolSentence(name, args, result, event) {
  const failure = event.status === 'ok' ? '' : event.summary
  if (!name) {
    // A semantic event that reuses the tool_completed type without naming a tool.
    return { text: event.summary || 'Tool call', meta: '' }
  }
  switch (name) {
    case 'get_creator_profile': {
      const creator = result.creator || {}
      return {
        text: 'Loaded the verified creator profile',
        meta: `${formatters.followers(creator.followers)} followers · ${formatters.pct(
          creator.engagement_rate
        )} engagement · ${formatters.money(creator.brand_attributed_revenue)} brand revenue`,
      }
    }
    case 'find_available_gifts': {
      const gifts = result.gifts || []
      const query = args.query
      if (!query) return { text: 'Listed the full gift list', meta: `${gifts.length} gifts` }
      if (!gifts.length) {
        return { text: `Searched the gift list for “${query}”`, meta: 'nothing matched' }
      }
      return {
        text: `Searched the gift list for “${query}”`,
        meta: `matched ${gifts.map((gift) => gift.name).join(', ')}`,
      }
    }
    case 'check_gifting_eligibility': {
      const offers = result.offers || []
      const single = offers.length === 1 ? offers[0] : null
      if (single) {
        return single.eligible
          ? { text: `Checked ${single.name} — qualifies`, meta: 'every rule passed' }
          : {
              text: `Checked ${single.name} — does not qualify`,
              meta: (single.shortfalls || []).join('; '),
            }
      }
      const eligible = result.eligible_offers || []
      return {
        text: eligible.length
          ? `Checked every gift — qualifies for ${eligible.map((offer) => offer.name).join(', ')}`
          : 'Checked every gift — qualifies for none',
        meta: `${offers.length} gifts evaluated`,
      }
    }
    case 'validate_shipping_address':
      return result.valid
        ? { text: 'Validated the delivery address — accepted', meta: result.normalised }
        : { text: 'Validated the delivery address — rejected', meta: result.reason }
    case 'issue_gift':
      return result.ok
        ? {
            text: `Placed the order — ${result.product}`,
            meta: `${result.order_id} · to ${result.shipping_address}`,
          }
        : { text: 'The order was refused', meta: result.message || result.error }
    default:
      return { text: `${name}()`, meta: failure }
  }
}

/** What this step did, in one line a non-engineer can still read. */
export function stepSentence(event) {
  const payload = event.payload || {}
  const result = payload.result || {}

  if (event.type === 'intent_classified') {
    return {
      text: `Routed as ${(payload.intent || '').replaceAll('_', ' ')} — ${payload.reasoning || ''}`,
      meta: `${Math.round((payload.confidence || 0) * 100)}% confident${
        payload.keyword_fallback_used ? ' · keyword fallback' : ''
      }`,
    }
  }
  if (event.type === 'agent_handoff') {
    return { text: `Handed off to the ${humanAgent(payload.to)}`, meta: '' }
  }
  if (event.type === 'agent_resumed') {
    return {
      text: `Resumed the ${humanAgent(payload.agent)}`,
      meta: 'the creator answered its question',
    }
  }
  if (event.type === 'profile_lookup') {
    const creator = payload.profile || {}
    return {
      text: `Loaded the verified profile for ${creator.name || 'the creator'}`,
      meta: [
        `${formatters.followers(creator.followers)} followers`,
        `${formatters.pct(creator.engagement_rate)} engagement`,
        `${formatters.money(creator.brand_attributed_revenue)} brand revenue`,
      ].join(' · '),
    }
  }
  if (event.type === 'criteria_evaluated') {
    const offers = payload.offers || []
    const single = offers.length === 1 ? offers[0] : null
    if (single) {
      return single.eligible
        ? {
            text: `Checked ${single.name} against the brand's criteria — qualifies`,
            meta: 'every rule passed',
          }
        : {
            text: `Checked ${single.name} against the brand's criteria — does not qualify`,
            meta: `failed: ${(single.failed_labels || []).join(', ')}`,
          }
    }
    const eligible = payload.eligible_offers || []
    return {
      text: eligible.length
        ? `Checked every gift — qualifies for ${eligible.map((offer) => offer.name).join(', ')}`
        : 'Checked every gift — qualifies for none of them',
      meta: `${offers.length} gifts evaluated`,
    }
  }
  if (event.type === 'address_validated') {
    return payload.valid
      ? { text: 'Validated the delivery address — accepted', meta: payload.normalised }
      : { text: 'Validated the delivery address — rejected', meta: payload.reason }
  }
  if (event.type === 'gift_issued') {
    return {
      text: `Placed the order — ${payload.product}`,
      meta: `${payload.order_id} · to ${payload.shipping_address}${
        payload.address_changed ? ' · address changed' : ''
      }`,
    }
  }
  if (event.type === 'escalated') {
    return {
      text: `Handed the conversation to a human — ${payload.detail}`,
      meta:
        payload.kind === 'metric'
          ? `profile rule · ${payload.reason}`
          : `creator said this · ${payload.reason}`,
    }
  }
  if (event.type === 'escalation_released') {
    return { text: 'A human handed the conversation back to the agent', meta: event.summary }
  }
  if (event.type === 'guardrail_blocked') {
    return {
      text: `Guardrail stopped the model: ${String(payload.guardrail || 'blocked').replaceAll(
        '_',
        ' '
      )}`,
      meta: event.summary,
    }
  }
  if (event.type === 'guardrail_adjusted') {
    return { text: 'Rewrote a field to match the verified profile', meta: event.summary }
  }
  if (event.type === 'injection_suspected') {
    return {
      text: "Instruction-override attempt detected in the creator's message",
      meta: (payload.patterns || []).join(', '),
    }
  }
  if (event.type === 'model_calls') {
    const calls = payload.calls || []
    const models = [...new Set(calls.map((call) => call.model).filter(Boolean))]
    const tokens = calls.reduce(
      (sum, call) => sum + (call.tokens_in || 0) + (call.tokens_out || 0),
      0
    )
    const cost = calls.reduce((sum, call) => sum + (call.cost_usd || 0), 0)
    const retried = calls.filter((call) => (call.payload || {}).retries).length
    const fellBack = calls.filter((call) => (call.payload || {}).used_fallback).length
    return {
      text: `${calls.length} model call${calls.length === 1 ? '' : 's'} to ${models.join(', ')}`,
      meta: [
        `${tokens.toLocaleString()} tokens`,
        formatters.cost(cost),
        retried ? `${retried} retried` : '',
        fellBack ? `${fellBack} fell back` : '',
      ]
        .filter(Boolean)
        .join(' · '),
    }
  }

  if (event.type === 'tool_call_completed' || event.type === 'tool_call_failed') {
    return toolSentence(payload.tool, payload.arguments || {}, result, event)
  }
  return { text: event.summary || event.type.replaceAll('_', ' '), meta: '' }
}

/* The tool wrapper and the semantic event that describes the same step both appear
 * in the log. Keep the meaningful one so the trace reads as steps, not as calls. */
const COVERED_BY = {
  get_creator_profile: 'profile_lookup',
  check_gifting_eligibility: 'criteria_evaluated',
  validate_shipping_address: 'address_validated',
  issue_gift: 'gift_issued',
}

export function dedupe(steps) {
  const present = new Set(steps.map((event) => event.type))
  return steps.filter((event) => {
    if (event.type !== 'tool_call_completed') return true
    const covering = COVERED_BY[(event.payload || {}).tool]
    if (!covering) return true
    return !(present.has(covering) && event.status === 'ok')
  })
}

/** The decision facts behind a turn, in the order they matter. */
export function decisionFacts(outcome) {
  if (!outcome) return []
  return [
    outcome.gift || outcome.product,
    outcome.order_id ? `order ${outcome.order_id}` : null,
    outcome.tag,
    typeof outcome.score === 'number' ? `${outcome.score}/100` : null,
    outcome.decision ? `decision: ${outcome.decision}` : null,
    outcome.failed_criteria?.length ? `failed on ${outcome.failed_criteria.join(', ')}` : null,
    outcome.alternatives?.length ? `offered ${outcome.alternatives[0].name}` : null,
    outcome.guardrail_blocks?.length ? `guardrail: ${outcome.guardrail_blocks[0].error}` : null,
  ].filter(Boolean)
}

const TRACE_AGENTS = new Set(['router_agent', 'guard', 'admin'])

/** Events that describe a model call rather than a business step. */
const MODEL_EVENTS = new Set(['llm_request', 'llm_response', 'llm_error', 'llm_fallback'])

function emptyTurn(event, key) {
  return {
    key,
    creator: event.creator_id,
    agent: '',
    message: '',
    reply: '',
    outcome: null,
    steps: [],
    models: [],
    all: [],
    startedAt: event.created_at,
  }
}

/** Group a flat event stream into turns, each with its decision and its trace. */
export function groupTurns(events) {
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
    // Model-level events are summarised once per turn rather than listed per call.
    if (MODEL_EVENTS.has(event.type)) {
      current.models.push(event)
      continue
    }

    if (event.agent && !TRACE_AGENTS.has(event.agent) && !current.agent) {
      current.agent = event.agent
    }
    current.steps.push(event)
  }

  return turns.map((turn) => {
    const cost = turn.all.reduce((sum, event) => sum + (event.cost_usd || 0), 0)
    const tokens = turn.all.reduce(
      (sum, event) => sum + (event.tokens_in || 0) + (event.tokens_out || 0),
      0
    )
    const modelCalls = turn.models.length
    const first = turn.all[0]
    const last = turn.all[turn.all.length - 1]
    const durationMs =
      first && last
        ? new Date(last.created_at).getTime() - new Date(first.created_at).getTime()
        : 0

    const steps = dedupe(turn.steps)
    if (turn.models.length) {
      steps.push({
        id: `${turn.key}-models`,
        type: 'model_calls',
        status: 'ok',
        summary: '',
        payload: { calls: turn.models },
      })
    }

    return {
      ...turn,
      cost,
      tokens,
      modelCalls,
      durationMs,
      agent: turn.agent || 'agent',
      steps,
    }
  })
}

/* ---------------------------------------------------------------------------
 * Rows
 *
 * The tables render these, so what a row holds is decided here rather than in
 * JSX -- which is what lets the columns be checked against a real event log.
 * ------------------------------------------------------------------------- */

/** Columns of the flat event stream, which is the page's default view. */
export const EVENT_COLUMNS = [
  { key: 'id', label: '#', mono: true, align: 'right' },
  { key: 'time', label: 'Time', mono: true },
  { key: 'source', label: 'Source' },
  { key: 'creator', label: 'Creator', mono: true },
  { key: 'type', label: 'Type', mono: true },
  { key: 'agent', label: 'Agent' },
  { key: 'status', label: 'Status' },
  { key: 'summary', label: 'Summary', wide: true },
  { key: 'latency', label: 'ms', mono: true, align: 'right' },
  { key: 'tokens', label: 'tok', mono: true, align: 'right' },
  { key: 'cost', label: 'cost', mono: true, align: 'right' },
]

/** Columns of the grouped view: one row per turn, its steps nested underneath. */
export const TURN_COLUMNS = [
  { key: 'time', label: 'Time', mono: true },
  { key: 'source', label: 'Source' },
  { key: 'creator', label: 'Creator', mono: true },
  { key: 'agent', label: 'Agent' },
  { key: 'message', label: 'Message', wide: true },
  { key: 'outcome', label: 'Outcome', mono: true },
  { key: 'steps', label: 'Steps', align: 'right' },
  { key: 'span', label: 'Span', mono: true, align: 'right' },
  { key: 'tokens', label: 'tok', mono: true, align: 'right' },
  { key: 'cost', label: 'cost', mono: true, align: 'right' },
]

/** Columns of the steps nested under an expanded turn. */
export const STEP_COLUMNS = [
  { key: 'time', label: 'Time', mono: true },
  { key: 'type', label: 'Type', mono: true },
  { key: 'agent', label: 'Agent' },
  { key: 'status', label: 'Status' },
  { key: 'detail', label: 'Detail', wide: true },
  { key: 'latency', label: 'ms', mono: true, align: 'right' },
  { key: 'tokens', label: 'tok', mono: true, align: 'right' },
]

function conversationIndex(conversations) {
  return new Map(conversations.map((item) => [item.id, item]))
}

function statusTone(status) {
  if (status === 'blocked') return 'red'
  if (status === 'warn') return 'amber'
  return null
}

/** A wall-clock span, in the largest unit that stays readable. */
export function formatSpan(ms) {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  // Round the seconds first, then split them: rounding each half separately is how
  // a duration ends up reading "1m 60s".
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

/** The model calls a synthetic model_calls step carries. */
function callsOf(event) {
  return (event.payload || {}).calls || null
}

function tokensOf(event) {
  const calls = callsOf(event)
  const total = calls
    ? calls.reduce((sum, call) => sum + (call.tokens_in || 0) + (call.tokens_out || 0), 0)
    : (event.tokens_in || 0) + (event.tokens_out || 0)
  return total ? total.toLocaleString() : '—'
}

function latencyOf(event) {
  const calls = callsOf(event)
  const ms = calls
    ? calls.reduce((sum, call) => sum + (call.latency_ms || 0), 0)
    : event.latency_ms
  return ms ? `${ms}` : '—'
}

function sourceOf(conversation) {
  return conversation?.source === 'seeded' ? 'sample' : 'live'
}

/** One row per event. */
export function eventRows(events, conversations = []) {
  const index = conversationIndex(conversations)
  return events.map((event) => {
    const tone = statusTone(event.status)
    return {
      key: `event-${event.id}`,
      event,
      conversation: index.get(event.conversation_id) || null,
      tone,
      tones: tone ? { status: tone } : {},
      cells: {
        id: String(event.id),
        time: formatters.time(event.created_at),
        source: sourceOf(index.get(event.conversation_id)),
        creator: event.creator_id || '—',
        type: event.type,
        agent: event.agent || '—',
        status: event.status,
        summary: event.summary || '—',
        latency: latencyOf(event),
        tokens: tokensOf(event),
        cost: formatters.cost(event.cost_usd),
      },
    }
  })
}

/** One row per turn, with its decision and its totals. */
export function turnRows(events, conversations = []) {
  const index = conversationIndex(conversations)
  return groupTurns(events).map((turn) => {
    const first = turn.all[0]
    const outcome = turn.outcome || {}
    const meta = OUTCOMES[outcome.type]
    const blocked = turn.all.some((event) => event.status === 'blocked')
    return {
      key: turn.key,
      turn,
      conversation: index.get(first?.conversation_id) || null,
      tone: blocked ? 'red' : null,
      tones: meta ? { outcome: meta.tone } : {},
      cells: {
        time: formatters.time(turn.startedAt),
        source: sourceOf(index.get(first?.conversation_id)),
        creator: turn.creator || '—',
        agent: turn.agent,
        message: turn.message || '—',
        outcome: outcome.type || '—',
        steps: String(turn.steps.length),
        span: formatSpan(turn.durationMs),
        tokens: turn.tokens ? turn.tokens.toLocaleString() : '—',
        cost: formatters.cost(turn.cost),
      },
    }
  })
}

/** One row per step of a turn, in the order they ran. */
export function stepRows(steps) {
  return steps.map((event) => {
    const { text, meta } = stepSentence(event)
    const tone = statusTone(event.status)
    return {
      key: `step-${event.id}`,
      event,
      tone,
      tones: tone ? { status: tone } : {},
      cells: {
        time: event.created_at ? formatters.time(event.created_at) : '—',
        type: event.type,
        agent: event.agent || '—',
        status: event.status,
        detail: [text, meta].filter(Boolean).join(' — '),
        latency: latencyOf(event),
        tokens: tokensOf(event),
      },
    }
  })
}
