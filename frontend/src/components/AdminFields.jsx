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

export function ScoringRows({ scoring, metrics, onChange }) {
  const included = new Map(scoring.map((item) => [item.metric, item]))
  const total = scoring.reduce((sum, item) => sum + (Number(item.weight) || 0), 0)

  function toggle(metric, on) {
    if (on) {
      onChange([
        ...scoring,
        { metric: metric.key, weight: 25, ideal: metric.default_ideal },
      ])
    } else {
      onChange(scoring.filter((item) => item.metric !== metric.key))
    }
  }

  function patch(key, next) {
    onChange(scoring.map((item) => (item.metric === key ? { ...item, ...next } : item)))
  }

  return (
    <div className="rows">
      {metrics.map((metric) => {
        const item = included.get(metric.key)
        return (
          <div className="row-card compact" key={metric.key}>
            <Toggle
              checked={Boolean(item)}
              label={metric.label}
              onChange={(value) => toggle(metric, value)}
            />
            {item && (
              <>
                <Field label="Weight">
                  <NumberInput
                    value={item.weight}
                    min={0}
                    max={100}
                    onChange={(value) => patch(metric.key, { weight: value })}
                  />
                </Field>
                <Field label="Ideal value" hint={metric.description}>
                  <ValueInput
                    metric={metric}
                    operator={metric.shape === 'list' ? 'includes_any' : 'gte'}
                    value={item.ideal}
                    onChange={(value) => patch(metric.key, { ideal: value })}
                  />
                </Field>
              </>
            )}
          </div>
        )
      })}
      <p className="muted small">
        Weights are relative: the score is always normalised to 0–100, so the tag thresholds below
        keep their meaning whatever you select. Total weight {total}.
      </p>
    </div>
  )
}

export function TagRows({ labels, onChange }) {
  function patch(index, key, value) {
    onChange(labels.map((item, i) => (i === index ? { ...item, [key]: value } : item)))
  }

  return (
    <div className="rows">
      {labels.map((label, index) => (
        <div className="row-card" key={`${label.label}-${index}`}>
          <Field label="Tag">
            <input value={label.label} onChange={(e) => patch(index, 'label', e.target.value)} />
          </Field>
          <Field label="Minimum score">
            <NumberInput
              value={label.min_score}
              min={0}
              max={100}
              onChange={(value) => patch(index, 'min_score', value)}
            />
          </Field>
          <Field label="Decision" hint="Written onto the conversation.">
            <select
              value={label.decision}
              onChange={(e) => patch(index, 'decision', e.target.value)}
            >
              <option value="pass">pass — move forward</option>
              <option value="reject">reject — not a fit</option>
            </select>
          </Field>
          <Field label="Internal note" wide>
            <input
              value={label.description || ''}
              onChange={(e) => patch(index, 'description', e.target.value)}
            />
          </Field>
          <Field
            label="What the agent should say next"
            wide
            hint="Injected into the pitch agent's reply for this tag."
          >
            <textarea
              rows={2}
              value={label.response_instructions || ''}
              onChange={(e) => patch(index, 'response_instructions', e.target.value)}
            />
          </Field>
          <button
            type="button"
            className="btn btn-ghost btn-sm row-remove"
            onClick={() => onChange(labels.filter((_, i) => i !== index))}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() =>
          onChange([
            ...labels,
            {
              label: 'New tag',
              min_score: 50,
              decision: 'reject',
              description: '',
              response_instructions: '',
            },
          ])
        }
      >
        + Add tag
      </button>
      <p className="muted small">
        Tags are matched highest threshold first, and one must sit at 0 so every score lands
        somewhere.
      </p>
    </div>
  )
}

export function AgentEditor({ agents, onChange }) {
  const titles = {
    router: 'Routing agent',
    gift_request: 'Gift request agent',
    creator_pitch: 'Creator pitch agent',
    general_inquiry: 'General inquiry handler',
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
  const keys = ['router', 'gift_request', 'creator_pitch', 'general_inquiry', 'fallback']
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

