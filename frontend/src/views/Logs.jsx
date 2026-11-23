import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api'
import { useSession } from '../session.jsx'
import ConversationDetail from '../components/ConversationDetail.jsx'
import ConversationList from '../components/ConversationList.jsx'
import { ErrorNote } from '../components/ui.jsx'
import { pickSelection } from '../components/logModel.js'

/* ---------------------------------------------------------------------------
 * The logs: a conversation browser.
 *
 * Left is the index -- every conversation, newest first, filed by disposition.
 * Right is the one you picked: its messages, and the tool calls that ran between
 * them.
 *
 * The page holds two requests rather than one stream, which is what lets the live
 * tail follow the conversation you are reading instead of appending to a wall of
 * every event in the database.
 * ------------------------------------------------------------------------- */

const POLL_MS = 1500
// The index is re-read less often than the open conversation: a new conversation
// appearing is worth a few seconds, a new turn in the one you are reading is not.
const INDEX_EVERY = 5

export default function Logs() {
  const { hasPasscode, savePasscode } = useSession()
  const [meta, setMeta] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [live, setLive] = useState(true)
  const [passcodeDraft, setPasscodeDraft] = useState('')
  const lastIdRef = useRef(0)
  // True once somebody has picked a conversation themselves. Until then the pane
  // follows the newest one, so a conversation started in another tab shows up here
  // without anyone hunting for it.
  const pinnedRef = useRef(false)

  const conversations = meta?.conversations || []
  const selected = conversations.find((item) => item.id === selectedId) || null

  const loadIndex = useCallback(async () => {
    const data = await api('/api/logs/meta')
    setMeta(data)
    setSelectedId((current) => pickSelection(data.conversations, current, pinnedRef.current))
    return data
  }, [])

  const loadEvents = useCallback(async (conversationId, append = false) => {
    if (!conversationId) return
    if (!append) lastIdRef.current = 0
    const params = new URLSearchParams({
      conversation_id: conversationId,
      limit: '500',
      after_id: String(lastIdRef.current),
    })
    const data = await api(`/api/logs?${params}`)
    if (data.events.length) {
      lastIdRef.current = Math.max(...data.events.map((event) => event.id))
      setEvents((previous) => (append ? [...previous, ...data.events] : data.events))
    } else if (!append) {
      setEvents([])
    }
  }, [])

  // The index, once. The newest conversation opens for you, so the page is never
  // half empty on arrival.
  useEffect(() => {
    loadIndex().catch(setError)
  }, [loadIndex])

  useEffect(() => {
    if (!selectedId) return
    setLoading(true)
    loadEvents(selectedId)
      .then(() => setError(null))
      .catch(setError)
      .finally(() => setLoading(false))
  }, [selectedId, loadEvents])

  useEffect(() => {
    if (!live) return undefined
    let tick = 0
    const timer = setInterval(() => {
      tick += 1
      if (selectedId) loadEvents(selectedId, true).catch(() => {})
      if (tick % INDEX_EVERY === 0) loadIndex().catch(() => {})
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [live, selectedId, loadEvents, loadIndex])

  // Refresh means "show me what has happened since": it drops any pinned selection and
  // opens the newest conversation, which is where a conversation somebody just had
  // will be.
  async function refresh() {
    setError(null)
    pinnedRef.current = false
    try {
      const data = await loadIndex()
      const next = pickSelection(data.conversations, null, false)
      if (next) await loadEvents(next)
    } catch (err) {
      setError(err)
    }
  }

  // Clicking a row is a choice, so the pane stops following the newest one.
  function select(conversationId) {
    pinnedRef.current = true
    setSelectedId(conversationId)
  }

  async function release() {
    try {
      await api(`/api/conversations/${selectedId}/escalation`, { method: 'DELETE' })
      await refresh()
    } catch (err) {
      setError(err)
    }
  }

  const needsPasscode = error?.code === 'admin_passcode_required'

  return (
    <div className="page logs-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Observability</p>
          <h1>Agent logs</h1>
          <p className="lede">
            The audit trail behind every conversation. Pick one on the left to see its messages and
            the tool calls that ran between them, in the order they happened. Every row was produced
            by real execution: the payload behind each one is a click away, and the export carries
            the whole event stream.
          </p>
        </div>
      </header>

      <div className="logs-toolbar">
        <span className="muted small">
          {conversations.length} conversation{conversations.length === 1 ? '' : 's'}
          {meta ? ` · ${meta.total_events.toLocaleString()} events` : ''}
        </span>
        <div className="toolbar-actions">
          <button
            type="button"
            className={`btn btn-ghost btn-sm ${live ? 'active' : ''}`}
            onClick={() => setLive((value) => !value)}
          >
            {live ? 'Pause live' : 'Resume live'}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={refresh}
            title="Reload the conversation list and open the most recent conversation"
          >
            Refresh
          </button>
        </div>
      </div>

      {needsPasscode && (
        <form
          className="passcode-bar"
          onSubmit={(event) => {
            event.preventDefault()
            savePasscode(passcodeDraft)
            setPasscodeDraft('')
            setTimeout(() => {
              refresh()
            }, 50)
          }}
        >
          <span>Enter the demo passcode to hand a conversation back to the agent:</span>
          <input
            type="password"
            value={passcodeDraft}
            onChange={(event) => setPasscodeDraft(event.target.value)}
            placeholder="passcode"
          />
          <button type="submit" className="btn btn-primary btn-sm">
            Unlock
          </button>
        </form>
      )}

      <ErrorNote error={needsPasscode ? null : error} onRetry={refresh} />

      <div className="logs-layout">
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          onSelect={select}
        />
        <ConversationDetail
          conversation={selected}
          events={events}
          loading={loading}
          canRelease={hasPasscode}
          onRelease={release}
        />
      </div>
    </div>
  )
}
