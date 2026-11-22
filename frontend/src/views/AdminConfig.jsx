import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useSession } from '../session.jsx'
import { Field, ModelsEditor, Section } from '../components/AdminFields.jsx'
import GiftRows from '../components/GiftRows.jsx'
import { Badge, ErrorNote, Spinner } from '../components/ui.jsx'

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

/* The admin tree. Two levels of expansion, exactly as an operator would think
 * about it: the brand contains agents, and the agents contain their rules. */
const TREE = [
  { id: 'brand', label: 'Brand', hint: 'name, positioning, tone' },
  { id: 'gifting', label: 'Gifting agent', group: 'Agents', hint: 'gifts and their criteria' },
  { id: 'router', label: 'Intent agent', group: 'Agents', hint: 'do we have an agent for this?' },
  { id: 'general_inquiry', label: 'Front desk', group: 'Agents', hint: 'everything else' },
  { id: 'models', label: 'Models', hint: 'which model runs each agent' },
]

function AgentFields({ agentKey, agents, onChange }) {
  const value = agents[agentKey] || { instructions: '', constraints: '' }
  function patch(key, next) {
    onChange({ ...agents, [agentKey]: { ...value, [key]: next } })
  }
  return (
    <div className="admin-grid">
      <Field label="Instructions" wide hint="What this agent should do, in your words.">
        <textarea
          rows={3}
          value={value.instructions}
          onChange={(event) => patch('instructions', event.target.value)}
        />
      </Field>
      <Field label="Constraints" wide hint="What it must never do.">
        <textarea
          rows={3}
          value={value.constraints}
          onChange={(event) => patch('constraints', event.target.value)}
        />
      </Field>
    </div>
  )
}

export default function AdminConfig() {
  const { hasPasscode, savePasscode, clearPasscode } = useSession()
  const [version, setVersion] = useState(null)
  const [saved, setSaved] = useState(null)
  const [draft, setDraft] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [metricCatalog, setMetricCatalog] = useState([])
  const [node, setNode] = useState('gifting')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('form')
  const [jsonDraft, setJsonDraft] = useState('')
  const [passcodeDraft, setPasscodeDraft] = useState('')
  const [gateError, setGateError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [config, models, metrics] = await Promise.all([
        api('/api/config'),
        api('/api/models').catch(() => ({ models: [] })),
        api('/api/config/metrics').catch(() => ({ metrics: [] })),
      ])
      setVersion(config.version)
      setDraft(config.config)
      setSaved(clone(config.config))
      setDefaults(config.defaults)
      setJsonDraft(JSON.stringify(config.config, null, 2))
      setCatalog(models.models || [])
      setMetricCatalog(metrics.metrics || [])
      setError(null)
      setGateError('')
    } catch (err) {
      if (err.code === 'admin_passcode_required') {
        // A stored-but-wrong passcode must send the admin back to the gate
        // rather than leaving them stranded on an error page.
        setGateError('That passcode was not accepted. Try again.')
        clearPasscode()
      } else {
        setError(err)
      }
    } finally {
      setLoading(false)
    }
  }, [clearPasscode])

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
          {(gateError || error?.code === 'admin_passcode_required') && (
            <p className="detail-note bad">{gateError || 'That passcode was not accepted.'}</p>
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
        <div className="admin-tree-layout">
          <nav className="admin-tree">
            <div className="tree-root">
              <span className="tree-caret">▾</span>
              <span className="tree-root-label">{draft.brand.name}</span>
            </div>

            <button
              type="button"
              className={`tree-item${node === 'brand' ? ' active' : ''}`}
              onClick={() => setNode('brand')}
            >
              Brand
            </button>

            <div className="tree-group">
              <span className="tree-caret">▾</span>
              <span className="tree-group-label">Agents</span>
            </div>

            {TREE.filter((item) => item.group === 'Agents').map((item) => (
              <button
                key={item.id}
                type="button"
                className={`tree-item nested${node === item.id ? ' active' : ''}`}
                onClick={() => setNode(item.id)}
              >
                <span className="tree-label">{item.label}</span>
                <span className="tree-hint">{item.hint}</span>
              </button>
            ))}

            <button
              type="button"
              className={`tree-item${node === 'models' ? ' active' : ''}`}
              onClick={() => setNode('models')}
            >
              <span className="tree-label">Models</span>
              <span className="tree-hint">which model runs each agent</span>
            </button>
          </nav>

          <div className="admin-pane">
            {node === 'brand' && (
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
                <Field label="Positioning" wide hint="Feeds the agents' sense of brand fit.">
                  <textarea
                    rows={3}
                    value={draft.brand.positioning}
                    onChange={(event) => update(['brand', 'positioning'], event.target.value)}
                  />
                </Field>
              </Section>
            )}

            {node === 'gifting' && (
              <>
                <Section
                  title="Gifting agent"
                  description="Handles every gift request. Each gift carries its own criteria — there is no brand-level gate."
                >
                  <AgentFields
                    agentKey="gift_request"
                    agents={draft.agents}
                    onChange={(value) => update(['agents'], value)}
                  />
                </Section>

                <Section
                  title={`Gifts (${draft.gifting.offers.length})`}
                  description="Click a gift to open it. Criteria are an AND — every rule must pass. A creator can qualify for some gifts and not others."
                >
                  <GiftRows
                    offers={draft.gifting.offers}
                    metrics={metricCatalog}
                    onChange={(value) => update(['gifting', 'offers'], value)}
                  />
                </Section>

                <Section title="Notes" description="Free text for whoever maintains this config.">
                  <Field label="Gifting notes" wide>
                    <textarea
                      rows={2}
                      value={draft.gifting.notes || ''}
                      onChange={(event) => update(['gifting', 'notes'], event.target.value)}
                    />
                  </Field>
                </Section>
              </>
            )}

            {node === 'router' && (
              <Section
                title="Intent agent"
                description="Decides whether the creator's message is an intent we have an agent for. It never approves anything itself."
              >
                <AgentFields
                  agentKey="router"
                  agents={draft.agents}
                  onChange={(value) => update(['agents'], value)}
                />
              </Section>
            )}

            {node === 'general_inquiry' && (
              <Section
                title="Front desk"
                description="Everything we have no specialist agent for, including partnership pitches. It has no business tools at all."
              >
                <AgentFields
                  agentKey="general_inquiry"
                  agents={draft.agents}
                  onChange={(value) => update(['agents'], value)}
                />
              </Section>
            )}

            {node === 'models' && (
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
            )}

            <section className="admin-footnote">
              <p className="muted small">
                Saved configuration is versioned. Every log event records the version in force, so you
                can prove which rules produced a decision. Defaults come from{' '}
                <code>backend/app/data/brand_config.default.json</code>
                {defaults && <> ({defaults.gifting.offers.length} gifts)</>}.
              </p>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
