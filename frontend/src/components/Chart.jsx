import { useEffect, useRef, useState } from 'react'

/* ---------------------------------------------------------------------------
 * Hand-rolled SVG charts.
 *
 * No chart library: the geometry lives in analyticsModel.js (so it can be checked
 * without a browser) and this file only turns it into elements. The width is
 * measured rather than baked into a viewBox, so axis text stays at 11px instead of
 * scaling with the container.
 * ------------------------------------------------------------------------- */

function useChartWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    setWidth(node.getBoundingClientRect().width)
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width)
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}

function Axes({ chart }) {
  const { plot, yTicks, xTicks } = chart
  return (
    <>
      {yTicks.map((tick) => (
        <g key={tick.label}>
          <line
            className="chart-grid"
            x1={plot.x}
            x2={plot.x + plot.width}
            y1={tick.y}
            y2={tick.y}
          />
          <text className="chart-axis-label" x={plot.x - 10} y={tick.y + 4} textAnchor="end">
            {tick.label}
          </text>
        </g>
      ))}
      <line
        className="chart-axis"
        x1={plot.x}
        x2={plot.x + plot.width}
        y1={plot.y + plot.height}
        y2={plot.y + plot.height}
      />
      {xTicks.map((tick, index) =>
        tick.show ? (
          <text
            key={`${tick.label}-${index}`}
            className="chart-axis-label"
            x={tick.x}
            y={plot.y + plot.height + 20}
            textAnchor="middle"
          >
            {tick.label}
          </text>
        ) : null,
      )}
    </>
  )
}

/** A stacked bar chart with a labelled y-axis and value labels. */
export function StackedBars({ build }) {
  const [ref, width] = useChartWidth()
  const chart = width ? build(width) : null

  return (
    <div className="chart-frame" ref={ref}>
      {chart && (
        <svg className="chart-svg" width={chart.width} height={chart.height} role="img">
          <Axes chart={chart} />
          {chart.bars.map((bar) => (
            <g key={bar.key}>
              <title>{bar.title}</title>
              {bar.segments.map((segment) => (
                <rect
                  key={segment.key}
                  className={`chart-bar bar-${segment.tone}`}
                  x={segment.x}
                  y={segment.y}
                  width={bar.width}
                  height={Math.max(1, segment.height)}
                  rx={Math.min(3, bar.width / 3)}
                >
                  <title>{`${segment.count}`}</title>
                </rect>
              ))}
              {bar.segments
                .filter((segment) => segment.label)
                .map((segment) => (
                  <text
                    key={`${segment.key}-label`}
                    className="chart-value chart-value-in"
                    x={segment.x + bar.width / 2}
                    y={segment.labelY}
                    textAnchor="middle"
                  >
                    {segment.label}
                  </text>
                ))}
              {bar.totalLabel && (
                <text
                  className="chart-value"
                  x={bar.center}
                  y={bar.totalLabelY}
                  textAnchor="middle"
                >
                  {bar.totalLabel}
                </text>
              )}
            </g>
          ))}
        </svg>
      )}
    </div>
  )
}

/** A line chart over the same buckets, with point labels. */
export function RateLine({ build }) {
  const [ref, width] = useChartWidth()
  const chart = width ? build(width) : null

  return (
    <div className="chart-frame" ref={ref}>
      {chart && (
        <svg className="chart-svg" width={chart.width} height={chart.height} role="img">
          <Axes chart={chart} />
          {chart.paths.map((d) => (
            <path key={d} className="chart-line" d={d} />
          ))}
          {chart.points
            .filter((point) => point.y !== null)
            .map((point) => (
              <circle
                key={point.key}
                className="chart-point"
                cx={point.x}
                cy={point.y}
                r={3.5}
              >
                <title>{`${point.label}: ${Math.round(point.value * 100)}% of ${point.decided} decided${
                  point.windowDays ? ` in the last ${point.windowDays} days` : ''
                }`}</title>
              </circle>
            ))}
          {chart.labelled.map((point) => (
            <text
              key={`${point.key}-label`}
              className="chart-value"
              x={point.x}
              y={point.y}
              textAnchor="middle"
            >
              {point.label}
            </text>
          ))}
        </svg>
      )}
    </div>
  )
}
