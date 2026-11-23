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
  escalated: 'amber',
}

export const OUTCOME_TONES = {
  gift_issued: 'sage',
  gift_duplicate: 'sage',
  gift_declined: 'red',
  gift_suggested: 'slate',
  details_required: 'amber',
  inquiry: 'slate',
  escalated: 'slate',
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
    escalated: countOf(data.by_disposition, 'disposition', 'escalated'),
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

/* ---------------------------------------------------------------------------
 * Chart geometry.
 *
 * Kept here, with no React, so the axes, the labels and the bar rectangles can be
 * checked against a real payload -- an axis off by a tick, or a bar overflowing the
 * plot, is exactly the kind of thing a screenshot hides.
 * ------------------------------------------------------------------------- */

/** Box the bars and the line are drawn inside, in px. */
export const CHART = {
  height: 280,
  padTop: 24, // room for the label above the tallest bar
  padRight: 18,
  padBottom: 38, // room for the date labels
  padLeft: 52, // room for the y-axis labels
}

/** Round axis ticks that cover `max`, so labels read 0 / 10 / 20, not 0 / 8.3. */
function niceTicks(max, count = 4, { integer = false } = {}) {
  if (!(max > 0)) return [0, 1]
  const rough = max / count
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step =
    [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * magnitude).find((s) => s >= rough) ??
    magnitude * 10
  const rounded = integer ? Math.max(1, Math.round(step)) : step
  const top = Math.ceil(max / rounded) * rounded
  const ticks = []
  for (let value = 0; value <= top + 1e-9; value += rounded) {
    ticks.push(Math.round(value * 1e6) / 1e6)
  }
  return ticks
}

function frame({ width, buckets, max, integer }) {
  const plot = {
    x: CHART.padLeft,
    y: CHART.padTop,
    width: Math.max(20, width - CHART.padLeft - CHART.padRight),
    height: CHART.height - CHART.padTop - CHART.padBottom,
  }
  const ticks = niceTicks(max, 4, { integer })
  const top = ticks[ticks.length - 1] || 1
  const band = plot.width / Math.max(1, buckets)
  return {
    width,
    height: CHART.height,
    plot,
    ticks,
    top,
    band,
    // Value -> y pixel. Bars grow up from the axis, so this counts down from the top.
    y: (value) => plot.y + plot.height - (value / top) * plot.height,
    // Centre of bucket i, which is where a bar or a point sits.
    centerX: (index) => plot.x + band * (index + 0.5),
  }
}

/** Thin the x-axis labels so they never collide. */
function labelStride(band, minPx = 54) {
  return Math.max(1, Math.ceil(minPx / band))
}

/**
 * A stacked bar chart: one column per bucket, one segment per series.
 *
 * Data labels switch on by available width rather than by configuration -- a number
 * printed over a 4px bar is noise, not information -- and the column total is
 * labelled whenever there is room for it.
 */
export function stackedBars({ width, series, columns, labels, integer = true }) {
  const max = columns.reduce((peak, column) => Math.max(peak, column.total), 0)
  const f = frame({ width, buckets: columns.length, max, integer })
  const barWidth = Math.max(3, Math.min(46, f.band * 0.62))
  const showTotals = f.band >= 20
  const showSegments = f.band >= 30

  const bars = columns.map((column, index) => {
    const center = f.centerX(index)
    let cursor = f.plot.height // pixels still free above the axis
    const segments = []
    for (const item of column.values) {
      if (!item.count) continue
      const height = (item.count / f.top) * f.plot.height
      cursor -= height
      segments.push({
        key: item.key,
        tone: item.tone,
        count: item.count,
        x: center - barWidth / 2,
        y: f.plot.y + cursor,
        height,
        label: showSegments && height >= 16 ? String(item.count) : '',
        labelY: f.plot.y + cursor + height / 2 + 4,
      })
    }
    return {
      key: column.key ?? `col-${index}`,
      title: `${labels[index]}: ${column.total}`,
      center,
      width: barWidth,
      total: column.total,
      segments,
      totalLabel: showTotals && column.total ? String(column.total) : '',
      totalLabelY: f.y(column.total) - 7,
    }
  })

  const stride = labelStride(f.band)
  return {
    width: f.width,
    height: f.height,
    plot: f.plot,
    band: f.band,
    barWidth,
    showTotals,
    showSegments,
    series,
    bars,
    yTicks: f.ticks.map((value) => ({ value, y: f.y(value), label: String(value) })),
    xTicks: labels.map((label, index) => ({
      x: f.centerX(index),
      label,
      show: index % stride === 0,
    })),
  }
}

export function intentChart(data, { width }) {
  const series = (data.by_intent || []).map((row) => ({
    key: row.intent,
    label: row.label,
    tone: tone(INTENT_TONES, row.intent),
  }))
  const columns = (data.series || []).map((row) => ({
    total: row.total,
    values: series.map((item) => ({
      key: item.key,
      tone: item.tone,
      count: row.counts?.[item.key] || 0,
    })),
  }))
  return stackedBars({
    width,
    series,
    columns,
    labels: (data.series || []).map((row) => row.label),
  })
}

/** The same stacked bars, one segment per disposition, in a fixed order. */
export function dispositionChart(data, { width }) {
  const order = ['gift_request_approved', 'gift_request_denied', 'escalated', 'other']
  const series = order.map((key) => ({
    key,
    label: (data.by_disposition || []).find((row) => row.disposition === key)?.label || key,
    tone: tone(DISPOSITION_TONES, key),
  }))
  const columns = (data.series || []).map((row) => {
    const values = series.map((item) => ({
      key: item.key,
      tone: item.tone,
      count: row.dispositions?.[item.key] || 0,
    }))
    return { total: values.reduce((sum, item) => sum + item.count, 0), values }
  })
  return stackedBars({
    width,
    series,
    columns,
    labels: (data.series || []).map((row) => row.label),
  })
}

/**
 * The gift request approval rate over time, as a line.
 *
 * The y-axis is pinned to 0-100%: an axis that rescales to its own maximum turns a
 * two-point wobble into a cliff. Buckets where nothing was decided are gaps rather
 * than zeros -- the payload sends null for those, and the line breaks instead of
 * dropping to the floor and claiming a 0% approval rate that never happened.
 */
export function approvalChart(data, { width }) {
  const f = frame({ width, buckets: (data.series || []).length, max: 1, integer: false })
  const stride = labelStride(f.band)

  const points = (data.series || []).map((row, index) => ({
    key: row.bucket,
    index,
    x: f.centerX(index),
    value: row.approval_rate,
    decided: row.decided,
    label: row.label,
    y: row.approval_rate === null || row.approval_rate === undefined
      ? null
      : f.y(row.approval_rate),
  }))

  // A gap breaks the line: points are only joined when both ends exist.
  const runs = []
  let run = []
  for (const point of points) {
    if (point.y === null) {
      if (run.length) runs.push(run)
      run = []
      continue
    }
    run.push(point)
  }
  if (run.length) runs.push(run)

  const last = [...points].reverse().find((point) => point.y !== null)
  const labelled = points.filter(
    (point) => point.y !== null && (f.band >= 40 || point === last),
  )

  return {
    width: f.width,
    height: f.height,
    plot: f.plot,
    band: f.band,
    points,
    paths: runs.map((group) => group.map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' ')),
    // The axis is a percentage, so its labels are too.
    yTicks: f.ticks.map((value) => ({
      value,
      y: f.y(value),
      label: `${Math.round(value * 100)}%`,
    })),
    xTicks: points.map((point) => ({
      x: point.x,
      label: point.label,
      show: point.index % stride === 0,
    })),
    labelled: labelled.map((point) => ({
      key: point.key,
      x: point.x,
      y: point.y - 11,
      label: `${Math.round(point.value * 100)}%`,
      decided: point.decided,
    })),
  }
}
