/* ---------------------------------------------------------------------------
 * The logs page's reading logic, kept apart from its rendering.
 *
 * Nothing here touches React, so the sidebar entries, the rows and the wording can
 * be exercised against a real event log rather than by eye.
 * ------------------------------------------------------------------------- */

import { formatters } from '../api.js'

/** The dispositions a conversation can reach, as the sidebar files them. */
export const DISPOSITIONS = {
  gift_request_approved: { label: 'approved', tone: 'sage' },
  gift_request_denied: { label: 'denied', tone: 'red' },
  other: { label: 'other', tone: 'slate' },
  escalated: { label: 'escalated', tone: 'amber' },
}

/* The detail pane is deliberately narrow: the messages, the tool calls that ran
 * between them, and the moments the conversation left the agent. Everything else the
 * orchestrator records -- routing, profile lookups, criteria evaluations, guardrails,
 * model calls -- is still in the event stream and in the export, but it is not what
 * this pane is for. */
export const DETAIL_EVENT_TYPES = new Set([
  'message_received',
  'message_sent',
  'tool_call_completed',
  'tool_call_failed',
  'escalated',
  'escalation_released',
])

/* The record kind, as the log names it. The raw event type is still in the payload
 * and the export -- this is the vocabulary a reader scans the pane by, so it stays
 * short and it stays stable even if the event names change. */
export const DETAIL_KINDS = {
  message_received: 'from_message',
  message_sent: 'to_message',
  tool_call_completed: 'tool_call',
  tool_call_failed: 'tool_call',
  escalated: 'escalation',
  escalation_released: 'escalation',
}

export const DETAIL_COLUMNS = [
  { key: 'time', label: 'Time', mono: true },
  { key: 'type', label: 'Type', mono: true },
  { key: 'step', label: 'Step', mono: true },
  { key: 'detail', label: 'Detail', wide: true },
]

/** A count with its noun, so the log never reads "1 gifts". */
function count(value, noun) {
  return `${value} ${noun}${value === 1 ? '' : 's'}`
}

/** The criteria that failed, in words: what it is, what we saw, what was needed. */
function shortfalls(checks = []) {
  return checks
    .filter((check) => !check.passed)
    .map((check) => `${check.label}: ${check.actual_display} (needs ${check.required_display})`)
    .join('; ')
}

/* ---------------------------------------------------------------------------
 * What happened, in words.
 *
 * Every row and every expanded pane is built from this, so there is one description
 * of a tool call rather than a summary that drifts from the detail. The vocabulary is
 * the brand's, not the API's: a tool is "Check the criteria for a gift" before it is
 * check_gifting_eligibility, and its result is "$3,400, and it needed $5,000" before
 * it is a nested object. The raw payload is still one click away.
 * ------------------------------------------------------------------------- */

function toolStory(name, args, result, event) {
  const story = buildToolStory(name, args, result, event)
  // A refusal, or a call that did not complete, is answered from the trace rather than
  // from the shape of whatever came back: "Refused: undefined" is not an answer, and it
  // is exactly what a missing result produces if the happy path is left to interpolate.
  if (result.ok === false || event.status !== 'ok') {
    return {
      ...story,
      output: `Refused: ${
        result.message || result.error || event.summary || 'the call did not complete'
      }`,
    }
  }
  return story
}

function buildToolStory(name, args, result, event) {
  switch (name) {
    case 'get_creator_profile': {
      const creator = result.creator || {}
      return {
        action: "Look up the creator's verified profile",
        input: [],
        output: [
          creator.name,
          creator.followers == null ? null : `${formatters.followers(creator.followers)} followers`,
          creator.engagement_rate == null
            ? null
            : `${formatters.pct(creator.engagement_rate)} engagement`,
          creator.brand_attributed_revenue == null
            ? null
            : `${formatters.money(creator.brand_attributed_revenue)} brand revenue`,
        ]
          .filter(Boolean)
          .join(' · '),
      }
    }

    case 'find_available_gifts': {
      const gifts = result.gifts || []
      const query = args.query
      const eligible = gifts.filter((gift) => gift.eligible).map((gift) => gift.name)
      const names = gifts.map((gift) => gift.name).join(', ')
      // "1 gift matched: Discovery Set (qualifies)" rather than a clause that only
      // reads well in the plural.
      const tail =
        eligible.length === gifts.length
          ? ' (qualifies)'
          : eligible.length
            ? ` (qualifies for ${eligible.join(', ')})`
            : ' (qualifies for none)'
      return {
        action: 'Search the gift list',
        input: [query ? `search for = “${query}”` : 'search the whole catalogue'],
        output: gifts.length
          ? `${count(gifts.length, 'gift')} matched: ${names}${tail}`
          : 'No gift matched that search',
      }
    }

    case 'check_gifting_eligibility': {
      const offers = result.offers || []
      const single = offers.length === 1 ? offers[0] : null
      if (single) {
        return {
          action: 'Check the criteria for a gift',
          input: [`gift = ${single.name}`],
          output: single.eligible
            ? `Qualifies for ${single.name}`
            : `Does not qualify for ${single.name}: ${shortfalls(single.checks)}`,
        }
      }
      const eligible = result.eligible_offers || []
      return {
        action: 'Check the criteria for every gift',
        input: [],
        output: eligible.length
          ? `Qualifies for ${eligible.map((offer) => offer.name).join(', ')}`
          : `Qualifies for none of the ${count(offers.length, 'gift')} checked`,
      }
    }

    case 'validate_shipping_address':
      return {
        action: 'Validate the delivery address',
        input: [`address = ${args.address}`],
        output: result.valid
          ? `Accepted: ${result.normalised}`
          : `Rejected: ${result.reason || 'the address did not validate'}${
              result.suggestion ? ` · suggested: ${result.suggestion}` : ''
            }`,
      }

    case 'issue_gift':
      return {
        action: 'Place the gift order',
        input: [`product = ${args.sku}`, `ship to = ${args.shipping_address}`],
        output: result.order_id
          ? `Order ${result.order_id} placed for ${result.product}`
          : 'The order was not placed',
      }

    default:
      // An unknown tool is worth seeing plainly rather than hiding behind a guess.
      return {
        action: name ? `Call ${name}` : event.summary || 'Tool call',
        input: [],
        output: event.summary || 'Done',
      }
  }
}

/** What fired a hand-off, in the reader's words.
 *
 * Three kinds now: the creator's own words, a rule about their verified profile,
 * or the intent agent deciding we have no agent for the request at all. The third
 * is a classification rather than a match, so it reads as one. */
function escalationTrigger(payload) {
  if (payload.kind === 'metric') return `profile rule = ${payload.detail}`
  if (payload.kind === 'out_of_scope') {
    return `intent = ${payload.intent || 'out of scope'} — nothing handles it`
  }
  return `keyword = “${payload.detail}”`
}

/** The plain-language view of one event: what was done, with what, what came back. */
export function eventStory(event) {
  const payload = event.payload || {}

  if (event.type === 'message_received') {
    return { action: 'Message from the creator', input: [], output: payload.message || '' }
  }
  if (event.type === 'message_sent') {
    return { action: 'Reply to the creator', input: [], output: payload.reply || '' }
  }
  if (event.type === 'escalated') {
    return {
      action: 'Handed the conversation to a human',
      input: [escalationTrigger(payload)],
      output: payload.reason || 'Escalated',
      note: payload.note,
    }
  }
  if (event.type === 'escalation_released') {
    return {
      action: 'A human handed the conversation back to the agent',
      input: [`released by = ${(event.payload || {}).released_by || 'a human'}`],
      output: 'The agent answers again from here',
      note: null,
    }
  }
  if (event.type === 'tool_call_completed' || event.type === 'tool_call_failed') {
    const story = toolStory(payload.tool, payload.arguments || {}, payload.result || {}, event)
    return { ...story, note: null }
  }
  return { action: event.type.replaceAll('_', ' '), input: [], output: event.summary || '', note: null }
}

/** One sidebar entry per conversation, in the order the API returns them: newest first. */
export function conversationRows(conversations = []) {
  return conversations.map((item) => {
    const disposition = DISPOSITIONS[item.disposition] || null
    return {
      key: item.id,
      id: item.id,
      // The id is a uuid, so the sidebar shows a stub of it and the detail shows it
      // in full: enough to match a row against a log line, not enough to crowd it.
      shortId: String(item.id).slice(0, 8),
      creator: item.creator_name || item.creator_id,
      time: formatters.dateTime(item.created_at),
      disposition: disposition?.label || '—',
      tone: disposition?.tone || null,
      sample: item.source === 'seeded',
      escalated: item.status === 'escalated',
      title: `${item.creator_name || item.creator_id} · ${item.id}`,
    }
  })
}

/**
 * Which conversation the pane should be showing.
 *
 * The index is newest first, so the conversation somebody just had is the first one.
 * That is the one to open, and the one to keep following while new conversations
 * arrive -- until they click a row, at which point the choice is theirs and the page
 * stops moving under them.
 */
export function pickSelection(conversations = [], currentId, pinned = false) {
  if (!conversations.length) return null
  if (pinned && conversations.some((item) => item.id === currentId)) return currentId
  return conversations[0].id
}

/** The step column: who or what produced the row. */
function stepFor(event) {
  const payload = event.payload || {}
  if (event.type === 'message_received') return 'creator'
  if (event.type === 'message_sent') return 'agent'
  if (event.type === 'escalated') {
    if (payload.kind === 'metric') return 'profile rule'
    return payload.kind === 'out_of_scope' ? 'out of scope' : 'keyword'
  }
  if (event.type === 'escalation_released') return 'admin'
  return payload.tool || event.type
}

/** Colour is for the two states worth spotting: handed over, and refused. */
function rowTone(event) {
  if (event.type === 'escalated') return 'amber'
  if (event.status === 'blocked') return 'red'
  if (event.status === 'warn') return 'amber'
  return null
}

/** One row per message, tool call and hand-off, in the order they ran. */
export function detailRows(events = []) {
  return events
    .filter((event) => DETAIL_EVENT_TYPES.has(event.type))
    /* A message_sent with an empty reply is not a message: it is escalation with no
     * holding text configured, which is silence. The turn is still in the event
     * stream and the export; this pane shows what was actually said. */
    .filter((event) => event.type !== 'message_sent' || (event.payload || {}).reply)
    .map((event) => {
      const tone = rowTone(event)
      return {
        key: `event-${event.id}`,
        event,
        tone,
        tones: tone ? { type: tone } : {},
        cells: {
          time: formatters.time(event.created_at),
          type: DETAIL_KINDS[event.type] || event.type,
          step: stepFor(event),
          detail: eventStory(event).output || '—',
        },
      }
    })
}

/** The counts the detail header reports, from the events it loaded.
 *
 * Counted off the rows the pane draws rather than off the event types, so the header
 * cannot claim a message that was never said. Model errors are counted from the whole
 * stream: they are not rows in the pane, and a conversation that fell back to the
 * cheaper model eight times is exactly what somebody troubleshooting needs to know.
 */
export function conversationStats(events = []) {
  const rows = detailRows(events)
  const messages = rows.filter((row) => row.event.type.startsWith('message')).length
  const toolCalls = rows.filter((row) => row.event.type.startsWith('tool_call')).length
  const tokens = events.reduce(
    (total, event) => total + (event.tokens_in || 0) + (event.tokens_out || 0),
    0
  )
  const cost = events.reduce((total, event) => total + (event.cost_usd || 0), 0)
  const modelErrors = events.filter((event) => event.type === 'llm_error').length
  const fallbacks = events.filter((event) => event.type === 'llm_fallback').length
  return { messages, toolCalls, tokens, cost, modelErrors, fallbacks }
}
