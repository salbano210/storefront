/* ---------------------------------------------------------------------------
 * The analytics view's data shaping, kept apart from its rendering.
 *
 * Nothing here touches React, so it can be exercised directly against a real
 * /api/analytics payload -- which is how the chart is checked, rather than by
 * eye. The view only maps over what these functions return.
 * ------------------------------------------------------------------------- */

/* Tones come from the palette the rest of the app already uses, so a gifting
 * conversation reads the same colour here as it does in the chat and the logs. */
export const INTENT_TONES = {
  gift_request: 'coral',
  general_inquiry: 'slate',
  unknown: 'muted',
}

export const DISPOSITION_TONES = {
  gift_request_approved: 'sage',
  gift_request_denied: 'red',
  other: 'slate',
}

export const OUTCOME_TONES = {
  gift_issued: 'sage',
  gift_duplicate: 'sage',
  gift_declined: 'red',
  gift_suggested: 'slate',
  details_required: 'amber',
  inquiry: 'slate',
  none: 'muted',
}

function tone(toneMap, key) {
  return toneMap[key] || 'slate'
}

export function countOf(rows, key, value) {
  return (rows || []).find((row) => row[key] === value)?.count ?? 0
}

/** The four headline numbers above the charts. */
export function headline(data) {
  return {
    conversations: data.totals.conversations,
    giftRequests: countOf(data.by_intent, 'intent', 'gift_request'),
    approved: countOf(data.by_disposition, 'disposition', 'gift_request_approved'),
    denied: countOf(data.by_disposition, 'disposition', 'gift_request_denied'),
  }
}

export function legend(data) {
  return (data.by_intent || []).map((row) => ({
    intent: row.intent,
    label: row.label,
    count: row.count,
    tone: tone(INTENT_TONES, row.intent),
  }))
}

/**
 * The stacked column chart.
 *
 * Every column is scaled against the tallest bucket, so a bar's height means the
 * same thing wherever it sits. Labels are thinned out so 90 daily buckets don't
 * print 90 overlapping dates.
 */
export function chart(data) {
  const series = data.series || []
  const intents = (data.by_intent || []).map((row) => row.intent)
  const peak = Math.max(1, ...series.map((row) => row.total))
  const labelEvery = Math.max(1, Math.ceil(series.length / 10))

  return {
    peak,
    labelEvery,
    columns: series.map((row, index) => ({
      bucket: row.bucket,
      label: row.label,
      total: row.total,
      title: `${row.label}: ${row.total}`,
      showLabel: index % labelEvery === 0,
      segments: intents
        .map((intent) => ({
          intent,
          count: row.counts?.[intent] || 0,
          tone: tone(INTENT_TONES, intent),
        }))
        .filter((segment) => segment.count > 0)
        .map((segment) => ({ ...segment, height: (segment.count / peak) * 100 })),
    })),
  }
}

export function dispositions(data) {
  return (data.by_disposition || []).map((row) => ({
    disposition: row.disposition,
    label: row.label,
    count: row.count,
    rate: row.rate,
    tone: tone(DISPOSITION_TONES, row.disposition),
    width: Math.round(row.rate * 100),
  }))
}

export function outcomes(data) {
  return (data.by_outcome || []).map((row) => ({
    outcome: row.outcome,
    label: row.label,
    count: row.count,
    tone: OUTCOME_TONES[row.outcome] || 'muted',
  }))
}
