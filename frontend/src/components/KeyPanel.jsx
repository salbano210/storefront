import { useState } from 'react'
import { useSession } from '../session.jsx'
import { Spinner } from './ui.jsx'

export default function KeyPanel() {
  const { hasKey, saveKey, clearKey, session } = useSession()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)

  async function testAndSave() {
    const candidate = draft.trim()
    if (!candidate) return
    setBusy(true)
    setStatus(null)
    try {
      // Validate the candidate key before we start using it for real calls.
      const response = await fetch('/api/key/test', {
        method: 'POST',
        headers: { 'X-OpenRouter-Key': candidate },
        credentials: 'same-origin',
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(data?.detail?.message || `Key rejected (${response.status})`)
      }
      saveKey(candidate)
      const balance =
        data.remaining_usd === null || data.remaining_usd === undefined
          ? ''
          : ` Balance: $${data.remaining_usd}`
      setStatus({ ok: true, message: `${data.message}${balance}` })
      setDraft('')
    } catch (error) {
      setStatus({ ok: false, message: error.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="keypanel">
      <button
        type="button"
        className={`btn btn-ghost btn-sm ${hasKey ? 'key-on' : ''}`}
        onClick={() => setOpen((v) => !v)}
      >
        {hasKey ? '🔑 Your key' : '🔑 Demo key'}
      </button>

      {open && (
        <div className="keypanel-pop">
          <h4>Use your own OpenRouter key</h4>
          <p className="muted">
            Optional. The demo ships with a host key that covers normal use. Pasting your own key
            raises the message limit and spends your credits instead — it is kept in this browser
            tab only and never stored on the server.
          </p>
          <input
            type="password"
            placeholder="sk-or-v1-..."
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            autoComplete="off"
          />
          <div className="keypanel-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={testAndSave}
              disabled={busy || !draft.trim()}
            >
              {busy ? <Spinner label="Checking" /> : 'Test & use'}
            </button>
            {hasKey && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  clearKey()
                  setStatus(null)
                }}
              >
                Remove
              </button>
            )}
          </div>
          {status && (
            <div className={`keypanel-status ${status.ok ? 'ok' : 'bad'}`}>{status.message}</div>
          )}
          {session && (
            <dl className="keypanel-meta">
              <div>
                <dt>Messages used</dt>
                <dd>
                  {session.message_count} / {session.message_cap}
                </dd>
              </div>
              <div>
                <dt>Demo spend today</dt>
                <dd>
                  ${Number(session.daily_cost_usd || 0).toFixed(4)} / $
                  {Number(session.daily_budget_usd || 0).toFixed(2)}
                </dd>
              </div>
            </dl>
          )}
        </div>
      )}
    </div>
  )
}
