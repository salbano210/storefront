/* ---------------------------------------------------------------------------
 * The logs page's reading logic, kept apart from its rendering.
 *
 * Nothing here touches React, so the sidebar entries and the detail rows can be
 * exercised against a real event log rather than by eye.
 * ------------------------------------------------------------------------- */

import { formatters } from '../api.js'

/** The dispositions a conversation can reach, as the sidebar files them. */
export const DISPOSITIONS = {
  gift_request_approved: { label: 'approved', tone: 'sage' },
  gift_request_denied: { label: 'denied', tone: 'red' },
  other: { label: 'other', tone: 'slate' },
  escalated: { label: 'escalated', tone: 'amber' },
}

/* The detail pane is deliberately narrow: the messages, and the tool calls that ran
 * between them. Everything else the orchestrator records -- routing, profile
 * lookups, criteria evaluations, guardrails, model calls -- is still in the event
 * stream and in the export, but it is not what this pane is for. */
export const DETAIL_EVENT_TYPES = new Set([
  'message_received',
  'message_sent',
  'tool_call_completed',
  'tool_call_failed',
])

export const DETAIL_COLUMNS = [
  { key: 'time', label: 'Time', mono: true },
  { key: 'step', label: 'Step', mono: true },
  { key: 'detail', label: 'Detail', wide: true },
]

/** What a tool call did, in one line. */
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

/** One sidebar entry per conversation, newest first. */
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

/** One row per message and tool call, in the order they ran. */
export function detailRows(events = []) {
  return events
    .filter((event) => DETAIL_EVENT_TYPES.has(event.type))
    .map((event) => {
      const payload = event.payload || {}
      const tool = payload.tool
      const message = event.type === 'message_received' || event.type === 'message_sent'

      // The step column says who or what produced the row: a side of the
      // conversation, or the tool that ran.
      const step = message
        ? event.type === 'message_received'
          ? 'creator'
          : 'agent'
        : tool || event.type

      const sentence = message
        ? payload.message || payload.reply || '—'
        : toolSentence(tool, payload.arguments || {}, payload.result || {}, event)

      return {
        key: `event-${event.id}`,
        event,
        tone: event.status === 'ok' ? null : event.status === 'blocked' ? 'red' : 'amber',
        cells: {
          time: formatters.time(event.created_at),
          step,
          detail: message
            ? sentence
            : [sentence.text, sentence.meta].filter(Boolean).join(' — '),
        },
      }
    })
}

/** The counts the detail header reports, from the events it loaded. */
export function conversationStats(events = []) {
  const shown = events.filter((event) => DETAIL_EVENT_TYPES.has(event.type))
  const messages = shown.filter(
    (event) => event.type === 'message_received' || event.type === 'message_sent'
  ).length
  const toolCalls = shown.filter((event) => event.type.startsWith('tool_call')).length
  const tokens = events.reduce(
    (total, event) => total + (event.tokens_in || 0) + (event.tokens_out || 0),
    0
  )
  const cost = events.reduce((total, event) => total + (event.cost_usd || 0), 0)
  return { messages, toolCalls, tokens, cost }
}
