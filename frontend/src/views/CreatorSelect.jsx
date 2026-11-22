import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, formatters } from '../api'
import { useSession, writeSelectedCreator } from '../session.jsx'
import { Avatar, Chip, ErrorNote, Spinner } from '../components/ui.jsx'

const toDraft = (profile) => ({
  ...profile,
  categories_text: (profile.categories || []).join(', '),
  engagement_pct: Number(((profile.engagement_rate || 0) * 100).toFixed(2)),
  top_gender: profile.audience?.top_gender || '',
})

function fromDraft(draft) {
  const { categories_text, engagement_pct, top_gender, changed_fields, ...profile } = draft
  return {
    ...profile,
    followers: Math.round(Number(profile.followers) || 0),
    brand_attributed_revenue: Number(profile.brand_attributed_revenue) || 0,
    storefront_sales: Number(profile.storefront_sales) || 0,
    categories: categories_text
      .split(',')
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean),
    engagement_rate: Number(engagement_pct) / 100,
    audience: { ...(profile.audience || {}), top_gender: top_gender || undefined },
  }
}

function ReadOnlyCard({ creator, onChoose }) {
  return (
    <article className="creator-card">
      <header>
        <Avatar src={creator.image} initials={creator.initials} size={52} />
        <div>
          <h2>{creator.name}</h2>
          <p className="handle">{creator.handle}</p>
        </div>
      </header>
      <p className="tagline">“{creator.tagline}”</p>
      <dl className="metrics">
        <div>
          <dt>Followers</dt>
          <dd>{formatters.followers(creator.followers)}</dd>
        </div>
        <div>
          <dt>Engagement</dt>
          <dd>{formatters.pct(creator.engagement_rate)}</dd>
        </div>
        <div>
          <dt>Audience</dt>
          <dd>{creator.primary_age_range}</dd>
        </div>
      </dl>
      <div className="chips">
        {creator.categories.map((category) => (
          <Chip key={category}>{category}</Chip>
        ))}
      </div>
      <p className="content-style">{creator.content_style}</p>
      <button type="button" className="btn btn-primary" onClick={() => onChoose(creator.id)}>
        Chat as {creator.name.split(' ')[0]}
      </button>
    </article>
  )
}

function EditableCard({ draft, busy, onPatch, onSave, onRevert, onChoose }) {
  const field = (label, key, props = {}) => (
    <label className={`field${props.wide ? ' wide' : ''}`}>
      <span className="field-label">{label}</span>
      <input
        type={props.type || 'text'}
        step={props.step}
        min={props.min}
        value={draft[key] ?? ''}
        onChange={(event) => onPatch(key, event.target.value)}
      />
    </label>
  )

  return (
    <article className="creator-card edit">
      <header>
        <Avatar src={draft.image} initials={draft.initials} size={44} />
        <div>
          <h2>{draft.name || 'Unnamed'}</h2>
          <p className="handle">{draft.handle}</p>
        </div>
      </header>
      <div className="edit-grid">
        {field('Name', 'name')}
        {field('Handle', 'handle')}
        {field('Followers', 'followers', { type: 'number', min: 0 })}
        {field('Engagement %', 'engagement_pct', { type: 'number', step: '0.1', min: 0 })}
        {field('Brand revenue $', 'brand_attributed_revenue', { type: 'number', min: 0 })}
        {field('Storefront sales $', 'storefront_sales', { type: 'number', min: 0 })}
        {field('Audience age range', 'primary_age_range')}
        {field('Top gender', 'top_gender')}
        {field('Categories (comma separated)', 'categories_text', { wide: true })}
        {field('Shipping address', 'shipping_address', { wide: true })}
        {field('Content style', 'content_style', { wide: true })}
        {field('Tagline', 'tagline', { wide: true })}
        <label className="field wide">
          <span className="field-label">Bio</span>
          <textarea
            rows={2}
            value={draft.bio ?? ''}
            onChange={(event) => onPatch('bio', event.target.value)}
          />
        </label>
      </div>
      <div className="edit-actions">
        <button type="button" className="btn btn-primary btn-sm" onClick={onSave} disabled={busy}>
          {busy ? <Spinner /> : 'Save metrics'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onRevert} disabled={busy}>
          Revert
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onChoose}>
          Chat as {String(draft.name).split(' ')[0]}
        </button>
      </div>
    </article>
  )
}

export default function CreatorSelect() {
  const { hasPasscode, savePasscode } = useSession()
  const [creators, setCreators] = useState([])
  const [drafts, setDrafts] = useState({})
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [passcodeDraft, setPasscodeDraft] = useState('')
  const [gateOpen, setGateOpen] = useState(false)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api('/api/creators')
      setCreators(data.creators)
      setError(null)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function startEditing() {
    setStatus(null)
    if (!hasPasscode) {
      setGateOpen(true)
      return
    }
    await enterEditMode()
  }

  async function enterEditMode() {
    try {
      const data = await api('/api/creators?full=true')
      const next = {}
      data.creators.forEach((profile) => {
        next[profile.id] = toDraft(profile)
      })
      setDrafts(next)
      setEditing(true)
      setGateOpen(false)
      setError(null)
    } catch (err) {
      if (err.code === 'admin_passcode_required') {
        setGateOpen(true)
      } else {
        setError(err)
      }
    }
  }

  async function unlock(event) {
    event.preventDefault()
    savePasscode(passcodeDraft)
    setPasscodeDraft('')
    // the header is applied on the next request, so verify straight away
    try {
      await api('/api/admin/check')
      await enterEditMode()
      setStatus({ ok: true, message: 'Admin unlocked — editing verified profiles.' })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    }
  }

  function patch(id, key, value) {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], [key]: value } }))
  }

  async function save(id) {
    setBusyId(id)
    setStatus(null)
    try {
      const payload = fromDraft(drafts[id])
      const result = await api(`/api/creators/${id}`, { method: 'PUT', body: payload })
      setDrafts((current) => ({ ...current, [id]: toDraft(result.creator) }))
      setStatus({
        ok: true,
        message: result.changed_fields.length
          ? `Saved ${payload.name} — changed ${result.changed_fields.join(', ')}. The agents will use these numbers immediately.`
          : `Saved ${payload.name} — no field changes.`,
      })
      await load()
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setBusyId(null)
    }
  }

  function cancel(id) {
    setDrafts((current) => ({ ...current, [id]: toDraft(creators.find((c) => c.id === id)) }))
    setStatus(null)
  }

  async function resetAll() {
    setBusyId('all')
    setStatus(null)
    try {
      await api('/api/creators/reset', { method: 'POST' })
      await load()
      await enterEditMode()
      setStatus({ ok: true, message: 'All profiles restored to repo defaults.' })
    } catch (err) {
      setStatus({ ok: false, message: err.message })
    } finally {
      setBusyId(null)
    }
  }

  function choose(creatorId) {
    writeSelectedCreator(creatorId)
    navigate('/chat')
  }

  const editingEnabled = editing && hasPasscode

  return (
    <div className="page">
      <section className="hero">
        <p className="eyebrow">Multi-agent creator relationship demo</p>
        <h1>Who are you today?</h1>
        <p className="lede">
          Pick a creator to simulate. Your chosen profile is loaded from the Storefront backend — the
          agents always decide on verified profile data, never on what the creator claims.
        </p>
        <div className="hero-actions">
          {editingEnabled ? (
            <>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing(false)}>
                Done editing
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={resetAll}
                disabled={busyId === 'all'}
              >
                {busyId === 'all' ? <Spinner /> : 'Reset all to repo defaults'}
              </button>
            </>
          ) : (
            <button type="button" className="btn btn-ghost btn-sm" onClick={startEditing}>
              ✎ Edit verified metrics
            </button>
          )}
        </div>
      </section>

      {gateOpen && (
        <form className="gate-inline" onSubmit={unlock}>
          <div>
            <strong>Brand admin passcode required</strong>
            <p className="muted small">
              Verified metrics are brand-side data. Enter the demo passcode to edit them.
            </p>
          </div>
          <input
            type="password"
            value={passcodeDraft}
            onChange={(event) => setPasscodeDraft(event.target.value)}
            placeholder="passcode"
            autoFocus
          />
          <button type="submit" className="btn btn-primary btn-sm">
            Unlock
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setGateOpen(false)
              setStatus(null)
            }}
          >
            Cancel
          </button>
        </form>
      )}

      {status && <div className={`status-bar ${status.ok ? 'ok' : 'bad'}`}>{status.message}</div>}
      <ErrorNote error={error} onRetry={load} />
      {loading && <Spinner label="Loading creator profiles" />}

      {editingEnabled && (
        <div className="edit-banner">
          <strong>Editing verified profiles.</strong> These are the exact numbers the agents decide
          on — change one, save, and the next message uses it. Every edit is recorded in the agent
          logs as a <code>creator_updated</code> event.
        </div>
      )}

      <div className="creator-grid">
        {creators.map((creator) =>
          editingEnabled && drafts[creator.id] ? (
            <EditableCard
              key={creator.id}
              draft={drafts[creator.id]}
              busy={busyId === creator.id}
              onPatch={(key, value) => patch(creator.id, key, value)}
              onSave={() => save(creator.id)}
              onRevert={() => cancel(creator.id)}
              onChoose={() => choose(creator.id)}
            />
          ) : (
            <ReadOnlyCard key={creator.id} creator={creator} onChoose={choose} />
          ),
        )}
      </div>

      <section className="how-it-works">
        <h2>How the demo works</h2>
        <ol className="steps">
          <li>
            <strong>Route.</strong> An intent agent decides whether your message is something we
            have an agent for. Gifting requests go to the gifting agent; everything else goes to
            the front desk.
          </li>
          <li>
            <strong>Execute.</strong> The specialist calls real backend tools — verified profile
            lookup, criteria evaluation, order placement — and every call is logged.
          </li>
          <li>
            <strong>Enforce.</strong> Eligibility maths, SKU checks and duplicate protection run in
            server-side code. A cheap model can never approve something the brand rules forbid.
          </li>
          <li>
            <strong>Observe.</strong> The Agent logs page shows the full trace, including the exact
            configuration version each decision was made under.
          </li>
        </ol>
        <p className="muted">
          Every creator below produces a different outcome: three qualify for gifting, two do not,
          and one will happily exaggerate their metrics.
        </p>
      </section>
    </div>
  )
}
