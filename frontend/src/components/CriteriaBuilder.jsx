import { useMemo } from 'react'

import { ValueInput, defaultFor } from './AdminFields.jsx'

/* ---------------------------------------------------------------------------
 * A criterion is {metric, operator, value}, drawn from the verified-metric
 * registry served at /api/config/metrics. Both the operator list and the value
 * input change with the metric's shape, so an invalid rule is hard to build --
 * and the server rejects one anyway.
 *
 * Escalation rules carry a `reason` as well, which is why the reason input is
 * opt-in: the same builder serves gift criteria and profile escalations, and the
 * extra field is preserved either way because rows are patched, not replaced.
 * ------------------------------------------------------------------------- */
export default function CriteriaBuilder({
  rules = [],
  metrics = [],
  onChange,
  reasonPlaceholder = '',
  emptyText = 'No criteria — every creator qualifies for this gift.',
  addLabel = '+ Add criterion',
}) {
  const byKey = useMemo(
    () => Object.fromEntries(metrics.map((metric) => [metric.key, metric])),
    [metrics]
  )

  function patch(index, next) {
    onChange(rules.map((rule, i) => (i === index ? { ...rule, ...next } : rule)))
  }

  function changeMetric(index, key) {
    const metric = byKey[key]
    if (!metric) return
    const operator = metric.operators[0].value
    patch(index, { metric: key, operator, value: defaultFor(metric, operator) })
  }

  function changeOperator(index, operator) {
    const metric = byKey[rules[index].metric]
    patch(index, { operator, value: defaultFor(metric, operator) })
  }

  function add() {
    const metric = metrics[0]
    if (!metric) return
    const operator = metric.operators[0].value
    onChange([...rules, { metric: metric.key, operator, value: defaultFor(metric, operator) }])
  }

  return (
    <div className="criteria">
      {rules.map((rule, index) => {
        const metric = byKey[rule.metric]
        return (
          <div className="criterion" key={`${rule.metric}-${index}`}>
            <select
              value={rule.metric}
              onChange={(event) => changeMetric(index, event.target.value)}
            >
              {!metric && <option value={rule.metric}>{rule.metric} (unknown)</option>}
              {metrics.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
            <select
              value={rule.operator}
              onChange={(event) => changeOperator(index, event.target.value)}
            >
              {(metric?.operators || []).map((operator) => (
                <option key={operator.value} value={operator.value}>
                  {operator.label}
                </option>
              ))}
            </select>
            <ValueInput
              metric={metric}
              operator={rule.operator}
              value={rule.value}
              onChange={(value) => patch(index, { value })}
            />
            {reasonPlaceholder && (
              <input
                type="text"
                className="criterion-reason"
                placeholder={reasonPlaceholder}
                value={rule.reason || ''}
                onChange={(event) => patch(index, { reason: event.target.value })}
              />
            )}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onChange(rules.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        )
      })}

      {!rules.length && <p className="muted small">{emptyText}</p>}

      <button type="button" className="btn btn-ghost btn-sm" onClick={add}>
        {addLabel}
      </button>
    </div>
  )
}
