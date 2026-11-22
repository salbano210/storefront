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

export function ProductRows({ products, onChange }) {
  function patch(index, key, value) {
    onChange(products.map((item, i) => (i === index ? { ...item, [key]: value } : item)))
  }
  return (
    <div className="rows">
      {products.map((product, index) => (
        <div className="row-card" key={`${product.sku}-${index}`}>
          <Field label="Product name">
            <input value={product.name} onChange={(e) => patch(index, 'name', e.target.value)} />
          </Field>
          <Field label="SKU">
            <input value={product.sku} onChange={(e) => patch(index, 'sku', e.target.value)} />
          </Field>
          <Field label="Retail price">
            <NumberInput
              value={product.retail_price}
              min={0}
              suffix="USD"
              onChange={(value) => patch(index, 'retail_price', value)}
            />
          </Field>
          <Field label="Giftable">
            <Toggle
              checked={Boolean(product.eligible)}
              label={product.eligible ? 'Eligible for gifting' : 'Not giftable'}
              onChange={(value) => patch(index, 'eligible', value)}
            />
          </Field>
          <Field label="Description" wide>
            <input
              value={product.description}
              onChange={(e) => patch(index, 'description', e.target.value)}
            />
          </Field>
          <button
            type="button"
            className="btn btn-ghost btn-sm row-remove"
            onClick={() => onChange(products.filter((_, i) => i !== index))}
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
            ...products,
            {
              name: 'New product',
              sku: `GT-NEW-${products.length + 1}`,
              description: '',
              eligible: true,
              retail_price: 0,
            },
          ])
        }
      >
        + Add product
      </button>
    </div>
  )
}

export function LabelRows({ labels, onChange }) {
  function patch(index, key, value) {
    onChange(labels.map((item, i) => (i === index ? { ...item, [key]: value } : item)))
  }
  return (
    <div className="rows">
      {labels.map((label, index) => (
        <div className="row-card compact" key={`${label.label}-${index}`}>
          <Field label="Label">
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
        onClick={() => onChange([...labels, { label: 'New Match', min_score: 50 }])}
      >
        + Add label
      </button>
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

