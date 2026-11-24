import { formatters } from '../api'

export function Field({ label, hint, children, wide = false }) {
  return (
    <label className={`field${wide ? ' wide' : ''}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function NumberInput({ value, onChange, step = 1, min, max, suffix }) {
  return (
    <span className="input-suffix">
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(event) => onChange(event.target.value === '' ? '' : Number(event.target.value))}
      />
      {suffix && <span className="suffix">{suffix}</span>}
    </span>
  )
}

export function Toggle({ checked, onChange, label }) {
  return (
    <label className="toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  )
}

export function Section({ title, description, children, actions }) {
  return (
    <section className="admin-section">
      <header>
        <div>
          <h2>{title}</h2>
          {description && <p className="muted small">{description}</p>}
        </div>
        {actions}
      </header>
      <div className="admin-grid">{children}</div>
    </section>
  )
}

export function round(value) {
  if (value === '' || value === null || value === undefined) return ''
  return Math.round(Number(value) * 1000) / 1000
}


/* ---------------------------------------------------------------------------
 * Escalation keywords: a phrase in the creator's own message that hands the
 * conversation to a human, plus the internal reason for whoever picks it up.
 * ------------------------------------------------------------------------- */
export function KeywordRows({ keywords = [], onChange }) {
  function patch(index, next) {
    onChange(keywords.map((item, i) => (i === index ? { ...item, ...next } : item)))
  }

  return (
    <div className="rows">
      {keywords.map((item, index) => (
        <div className="row-card" key={index}>
          <div className="admin-grid">
            <Field label="Phrase" hint="Matched as whole words, so 'refund' catches 'refunds'.">
              <input
                value={item.phrase || ''}
                placeholder="speak to a human"
                onChange={(event) => patch(index, { phrase: event.target.value })}
              />
            </Field>
            <Field label="Reason" hint="Internal: who should pick this up, and why.">
              <input
                value={item.reason || ''}
                placeholder="Creator asked for a person"
                onChange={(event) => patch(index, { reason: event.target.value })}
              />
            </Field>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm gift-remove"
            onClick={() => onChange(keywords.filter((_, i) => i !== index))}
          >
            Remove
          </button>
        </div>
      ))}

      {!keywords.length && (
        <p className="muted small">
          No keywords — nothing in a creator&apos;s message escalates on its own.
        </p>
      )}

      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => onChange([...keywords, { phrase: '', reason: '' }])}
      >
        + Add keyword
      </button>
    </div>
  )
}

export function defaultFor(metric, operator) {
  if (!metric) return ''
  if (metric.shape === 'list') return metric.default_ideal || []
  if (metric.shape === 'numeric') {
    if (operator === 'between') return [0, 0]
    if (metric.kind === 'percent') return 0.03
    return metric.default_ideal ?? 0
  }
  return metric.value_options?.[0] || ''
}

/**
 * The value editor for one metric. The input changes shape with the metric:
 * a number box, a pair for `between`, a comma list, or a select of known values.
 * Percent metrics are shown as percentages but stored as fractions.
 */
export function ValueInput({ metric, operator, value, onChange }) {
  if (!metric) return null

  if (metric.shape === 'numeric' && operator === 'between') {
    const pair = Array.isArray(value) ? value : [0, 0]
    const scale = metric.kind === 'percent' ? 100 : 1
    return (
      <span className="value-pair">
        <NumberInput
          value={round(pair[0] * scale)}
          step={metric.kind === 'percent' ? 0.1 : 1}
          onChange={(next) => onChange([Number(next) / scale, pair[1]])}
        />
        <span className="muted small">and</span>
        <NumberInput
          value={round(pair[1] * scale)}
          step={metric.kind === 'percent' ? 0.1 : 1}
          onChange={(next) => onChange([pair[0], Number(next) / scale])}
        />
        {metric.kind === 'percent' && <span className="muted small">%</span>}
      </span>
    )
  }

  if (metric.shape === 'numeric') {
    const isPercent = metric.kind === 'percent'
    return (
      <NumberInput
        value={round((value ?? 0) * (isPercent ? 100 : 1))}
        min={0}
        step={isPercent ? 0.1 : 1}
        suffix={isPercent ? '%' : metric.kind === 'money' ? 'USD' : undefined}
        onChange={(next) => onChange(Number(next) / (isPercent ? 100 : 1))}
      />
    )
  }

  if (metric.shape === 'list') {
    const text = Array.isArray(value) ? value.join(', ') : value || ''
    return (
      <input
        value={text}
        placeholder="comma, separated, values"
        onChange={(event) =>
          onChange(
            event.target.value
              .split(',')
              .map((part) => part.trim().toLowerCase())
              .filter(Boolean)
          )
        }
      />
    )
  }

  if (metric.value_options?.length) {
    return (
      <select value={value || ''} onChange={(event) => onChange(event.target.value)}>
        {metric.value_options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }

  return <input value={value || ''} onChange={(event) => onChange(event.target.value)} />
}

export function AgentEditor({ agents, onChange }) {
  const titles = {
    router: 'Routing agent',
    gift_request: 'Gift request agent',
  }
  return (
    <div className="rows">
      {Object.entries(agents).map(([key, value]) => (
        <div className="row-card" key={key}>
          <h4>{titles[key] || key}</h4>
          <Field label="Instructions" wide>
            <textarea
              rows={3}
              value={value.instructions}
              onChange={(event) =>
                onChange({ ...agents, [key]: { ...value, instructions: event.target.value } })
              }
            />
          </Field>
          <Field label="Constraints" wide>
            <textarea
              rows={3}
              value={value.constraints}
              onChange={(event) =>
                onChange({ ...agents, [key]: { ...value, constraints: event.target.value } })
              }
            />
          </Field>
        </div>
      ))}
    </div>
  )
}

export function ModelsEditor({ models, catalog, onChange }) {
  const keys = ['router', 'gift_request', 'fallback']
  const recommended = catalog.filter((model) => model.recommended)
  const options = recommended.length ? recommended : catalog
  return (
    <div className="rows">
      {keys.map((key) => {
        const selected = models[key]
        const known = catalog.find((model) => model.id === selected)
        return (
          <div className="row-card compact" key={key}>
            <Field
              label={key.replaceAll('_', ' ')}
              hint={
                known
                  ? `in $${known.prompt_price_per_m}/M · out $${known.completion_price_per_m}/M`
                  : 'custom model id'
              }
            >
              <select
                value={selected}
                onChange={(event) => onChange({ ...models, [key]: event.target.value })}
              >
                {!known && <option value={selected}>{selected}</option>}
                {options.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.id}
                    {model.prompt_price_per_m != null ? ` — $${model.prompt_price_per_m}/M in` : ''}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Custom model id">
              <input
                value={selected}
                onChange={(event) => onChange({ ...models, [key]: event.target.value })}
              />
            </Field>
          </div>
        )
      })}
      <p className="muted small">
        {formatters.cost(0.0001)} is roughly one routed turn on the default model. Every model listed
        is OpenRouter-hosted and tool-capable.
      </p>
    </div>
  )
}

