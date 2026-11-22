import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, formatters } from '../api'
import { useSession } from '../session.jsx'
import {
  AgentEditor,
  Field,
  LabelRows,
  ModelsEditor,
  NumberInput,
  ProductRows,
  Section,
} from '../components/AdminFields.jsx'
import { Badge, ErrorNote, Spinner } from '../components/ui.jsx'

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

export default function AdminConfig() {
  const { hasPasscode, savePasscode, clearPasscode } = useSession()
  const [version, setVersion] = useState(null)
  const [saved, setSaved] = useState(null)
  const [draft, setDraft] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('form')
  const [jsonDraft, setJsonDraft] = useState('')
  const [passcodeDraft, setPasscodeDraft] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [config, models] = await Promise.all([
        api('/api/config'),
        api('/api/models').catch(() => ({ models: [] })),
      ])
      setVersion(config.version)
      setDraft(config.config)
      setSaved(clone(config.config))
      setDefaults(config.defaults)
      setJsonDraft(JSON.stringify(config.config, null, 2))
      setCatalog(models.models || [])
      setError(null)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (hasPasscode) load()
    else setLoading(false)
  }, [hasPasscode, load])

  function update(path, value) {
    setDraft((previous) => {
      const next = clone(previous)
      let node = next
      for (const key of path.slice(0, -1)) node = node[key]
      node[path[path.length - 1]] = value
      return next
    })
  }

  async function save() {
    setBusy(true)
    setStatus(null)
    try {
      const result = await api('/api/config', {
        method: 'PUT',
        body: { config: draft, expected_version: version, updated_by: 'brand admin' },
      })
      setVersion(result.version)
      setDraft(result.config)
      setSaved(clone(result.config))
      setJsonDraft(JSON.stringify(result.config, null, 2))
      setStatus({ ok: true, message: `Saved as configuration v${result.version}. New conversations use it immediately.` })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setBusy(false)
    }
  }

  async function reset() {
    setBusy(true)
    setStatus(null)
    try {
      const result = await api('/api/config/reset', { method: 'POST' })
      setVersion(result.version)
      setDraft(result.config)
      setSaved(clone(result.config))
      setJsonDraft(JSON.stringify(result.config, null, 2))
      setStatus({ ok: true, message: `Restored repo defaults as v${result.version}.` })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setBusy(false)
    }
  }

  function applyJson() {
    try {
      const parsed = JSON.parse(jsonDraft)
      setDraft(parsed)
      setStatus({ ok: true, message: 'JSON applied to the form. Review, then save.' })
      setTab('form')
    } catch (err) {
      setStatus({ ok: false, message: `Invalid JSON: ${err.message}` })
    }
  }

  const dirty = useMemo(
    () => Boolean(draft && saved) && JSON.stringify(draft) !== JSON.stringify(saved),
    [draft, saved],
  )

  if (!hasPasscode) {
    return (
      <div className="page narrow">
        <div className="gate">
          <p className="eyebrow">Brand admin</p>
          <h1>Enter the demo passcode</h1>
          <p className="lede">
            The brand configuration drives every agent decision, so it is not exposed to demo
            visitors. Ask the person running the demo for the passcode.
          </p>
          <form
            className="gate-form"
            onSubmit={(event) => {
              event.preventDefault()
              savePasscode(passcodeDraft)
              setPasscodeDraft('')
            }}
          >
            <input
              type="password"
              value={passcodeDraft}
              onChange={(event) => setPasscodeDraft(event.target.value)}
              placeholder="passcode"
              autoFocus
            />
            <button type="submit" className="btn btn-primary">
              Unlock
            </button>
          </form>
          {error?.code === 'admin_passcode_required' && (
            <p className="detail-note bad">That passcode was not accepted.</p>
          )}
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="page">
        <Spinner label="Loading configuration" />
      </div>
    )
  }
  if (error) {
    return (
      <div className="page">
        <ErrorNote error={error} onRetry={load} />
      </div>
    )
  }
  if (!draft) return null

  return (
    <div className="page admin-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Brand admin</p>
          <h1>Agent configuration</h1>
          <p className="lede">
            These rules are enforced server-side on every turn. Change a threshold here and the very
            next gifting request is evaluated against it — no code changes, no redeploy.
          </p>
        </div>
        <div className="admin-head-meta">
          <Badge tone="ink">config v{version}</Badge>
          {dirty && <Badge tone="amber">unsaved changes</Badge>}
        </div>
      </header>

      <div className="admin-toolbar">
        <div className="tabs">
          <button
            type="button"
            className={`tab ${tab === 'form' ? 'active' : ''}`}
            onClick={() => setTab('form')}
          >
            Form
          </button>
          <button
            type="button"
            className={`tab ${tab === 'json' ? 'active' : ''}`}
            onClick={() => {
              setJsonDraft(JSON.stringify(draft, null, 2))
              setTab('json')
            }}
          >
            Raw JSON
          </button>
        </div>
        <div className="toolbar-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={reset} disabled={busy}>
            Reset to repo defaults
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              clearPasscode()
              setDraft(null)
            }}
          >
            Lock admin
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy}>
            {busy ? <Spinner /> : dirty ? 'Save configuration' : 'Saved'}
          </button>
        </div>
      </div>

      {status && <div className={`status-bar ${status.ok ? 'ok' : 'bad'}`}>{status.message}</div>}

      {tab === 'json' ? (
        <section className="admin-section">
          <header>
            <div>
              <h2>Raw configuration</h2>
              <p className="muted small">
                Edit and apply to the form, or paste a configuration from another environment. The
                server validates everything on save.
              </p>
            </div>
            <div className="toolbar-actions">
              <button type="button" className="btn btn-ghost btn-sm" onClick={applyJson}>
                Apply to form
              </button>
              <a
                className="btn btn-ghost btn-sm"
                href="/api/config/schema"
                target="_blank"
                rel="noreferrer"
              >
                View JSON schema
              </a>
            </div>
          </header>
          <textarea
            className="json-editor"
            rows={28}
            value={jsonDraft}
            onChange={(event) => setJsonDraft(event.target.value)}
            spellCheck={false}
          />
        </section>
      ) : (
        <>
          <Section title="Brand" description="Used in every prompt and reply.">
            <Field label="Brand name">
              <input
                value={draft.brand.name}
                onChange={(event) => update(['brand', 'name'], event.target.value)}
              />
            </Field>
            <Field label="Tone of voice">
              <input
                value={draft.brand.tone}
                onChange={(event) => update(['brand', 'tone'], event.target.value)}
              />
            </Field>
            <Field label="Description" wide>
              <textarea
                rows={2}
                value={draft.brand.description}
                onChange={(event) => update(['brand', 'description'], event.target.value)}
              />
            </Field>
            <Field label="Positioning" wide hint="Feeds the pitch agent's sense of brand fit.">
              <textarea
                rows={3}
                value={draft.brand.positioning}
                onChange={(event) => update(['brand', 'positioning'], event.target.value)}
              />
            </Field>
          </Section>

          <Section
            title="Gifting criteria"
            description="Evaluated in code against verified profile data. The model never does this maths."
          >
            <Field label="Minimum followers">
              <NumberInput
                value={draft.gifting.criteria.minimum_followers}
                min={0}
                onChange={(value) => update(['gifting', 'criteria', 'minimum_followers'], value)}
              />
            </Field>
            <Field label="Minimum engagement rate" hint="Decimal, e.g. 0.03 = 3%">
              <NumberInput
                value={draft.gifting.criteria.minimum_engagement_rate}
                step={0.001}
                min={0}
                max={1}
                onChange={(value) =>
                  update(['gifting', 'criteria', 'minimum_engagement_rate'], value)
                }
              />
            </Field>
            <Field label="Minimum brand-attributed revenue">
              <NumberInput
                value={draft.gifting.criteria.minimum_brand_revenue}
                min={0}
                suffix="USD"
                onChange={(value) =>
                  update(['gifting', 'criteria', 'minimum_brand_revenue'], value)
                }
              />
            </Field>
            <Field label="Required categories" hint="Comma separated. Empty means any category.">
              <input
                value={draft.gifting.criteria.required_categories.join(', ')}
                onChange={(event) =>
                  update(
                    ['gifting', 'criteria', 'required_categories'],
                    event.target.value
                      .split(',')
                      .map((item) => item.trim())
                      .filter(Boolean),
                  )
                }
              />
            </Field>
            <Field label="Notes for the agent" wide>
              <textarea
                rows={2}
                value={draft.gifting.criteria.notes}
                onChange={(event) => update(['gifting', 'criteria', 'notes'], event.target.value)}
              />
            </Field>
          </Section>

          <Section
            title="Gifting catalog"
            description="Only SKUs marked giftable can ever be issued — the order tool re-checks this."
          >
            <ProductRows
              products={draft.gifting.eligible_products}
              onChange={(value) => update(['gifting', 'eligible_products'], value)}
            />
          </Section>

          <Section
            title="Pitch criteria"
            description="Feeds the deterministic 0-100 fit score. The label is chosen by threshold, never by the model."
          >
            <Field label="Preferred categories" hint="Comma separated.">
              <input
                value={draft.pitch.criteria.preferred_categories.join(', ')}
                onChange={(event) =>
                  update(
                    ['pitch', 'criteria', 'preferred_categories'],
                    event.target.value
                      .split(',')
                      .map((item) => item.trim())
                      .filter(Boolean),
                  )
                }
              />
            </Field>
            <Field label="Minimum engagement rate">
              <NumberInput
                value={draft.pitch.criteria.minimum_engagement_rate}
                step={0.001}
                min={0}
                max={1}
                onChange={(value) => update(['pitch', 'criteria', 'minimum_engagement_rate'], value)}
              />
            </Field>
            <Field label="Minimum followers">
              <NumberInput
                value={draft.pitch.criteria.minimum_followers}
                min={0}
                onChange={(value) => update(['pitch', 'criteria', 'minimum_followers'], value)}
              />
            </Field>
            <Field label="Minimum brand revenue">
              <NumberInput
                value={draft.pitch.criteria.minimum_brand_revenue}
                min={0}
                suffix="USD"
                onChange={(value) => update(['pitch', 'criteria', 'minimum_brand_revenue'], value)}
              />
            </Field>
            <Field label="Preferred audience age">
              <input
                value={draft.pitch.criteria.preferred_audience_age}
                onChange={(event) =>
                  update(['pitch', 'criteria', 'preferred_audience_age'], event.target.value)
                }
              />
            </Field>
          </Section>

          <Section
            title="Classification labels"
            description="Ordered by score threshold. One label must have a minimum score of 0."
          >
            <LabelRows
              labels={draft.pitch.classification_labels}
              onChange={(value) => update(['pitch', 'classification_labels'], value)}
            />
          </Section>

          <Section title="Ideal profile targets" description="Score = actual / ideal, capped at 100%.">
            <Field label="Ideal followers">
              <NumberInput
                value={draft.pitch.targets.ideal_followers}
                min={1}
                onChange={(value) => update(['pitch', 'targets', 'ideal_followers'], value)}
              />
            </Field>
            <Field label="Ideal engagement rate">
              <NumberInput
                value={draft.pitch.targets.ideal_engagement_rate}
                step={0.005}
                min={0.001}
                max={1}
                onChange={(value) => update(['pitch', 'targets', 'ideal_engagement_rate'], value)}
              />
            </Field>
            <Field label="Ideal brand revenue">
              <NumberInput
                value={draft.pitch.targets.ideal_brand_revenue}
                min={1}
                suffix="USD"
                onChange={(value) => update(['pitch', 'targets', 'ideal_brand_revenue'], value)}
              />
            </Field>
            <Field label="Ideal ShopMy sales">
              <NumberInput
                value={draft.pitch.targets.ideal_shopmy_sales}
                min={1}
                suffix="USD"
                onChange={(value) => update(['pitch', 'targets', 'ideal_shopmy_sales'], value)}
              />
            </Field>
          </Section>

          <Section title="Scoring weights" description="Points available per component. Must sum above zero.">
            {Object.entries(draft.pitch.weights).map(([key, value]) => (
              <Field key={key} label={key.replaceAll('_', ' ')}>
                <NumberInput
                  value={value}
                  min={0}
                  max={100}
                  onChange={(next) => update(['pitch', 'weights', key], next)}
                />
              </Field>
            ))}
          </Section>

          <Section
            title="Agent instructions"
            description="Appended to each agent's system prompt. Business rules stay in code — these steer tone and judgement."
          >
            <AgentEditor agents={draft.agents} onChange={(value) => update(['agents'], value)} />
          </Section>

          <Section
            title="Models"
            description="Per-agent OpenRouter model. Cheap tool-capable models are plenty for this workflow."
          >
            <ModelsEditor
              models={draft.models}
              catalog={catalog}
              onChange={(value) => update(['models'], value)}
            />
          </Section>

          <section className="admin-footnote">
            <p className="muted small">
              Saved configuration is versioned. Every log event records the version in force, so you
              can prove which rules produced a decision. Defaults come from{' '}
              <code>backend/app/data/brand_config.default.json</code>
              {defaults && <> (v1: min followers {formatters.followers(defaults.gifting.criteria.minimum_followers)})</>}.
            </p>
          </section>

        </>
      )}
    </div>
  )
}
