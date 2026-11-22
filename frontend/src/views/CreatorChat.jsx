import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, formatters } from '../api'
import { readSelectedCreator, useSession } from '../session.jsx'
import { Avatar, Badge, Chip, ErrorNote, Spinner } from '../components/ui.jsx'

const QUICK_PROMPTS = [
  "Hi! I'd love to try the Barrier Repair Serum.",
  'Can I get a free gift?',
  "I'd love to partner with Demo Brand on a campaign.",
  'What products do you have?',
]

const OUTCOME_LABELS = {
  gift_issued: { tone: 'sage', text: 'Gift approved' },
  gift_duplicate: { tone: 'amber', text: 'Duplicate suppressed' },
  gift_declined: { tone: 'red', text: 'Not eligible' },
  tag_applied: { tone: 'sage', text: 'Tag applied' },
  inquiry: { tone: 'slate', text: 'Answered' },
}

function outcomeFor(payload) {
  const outcome = payload?.outcome
  if (!outcome) return null
  const meta = OUTCOME_LABELS[outcome.type] || { tone: 'slate', text: outcome.type }
  let detail = ''
  if (outcome.type === 'gift_issued') detail = `Order ${outcome.order_id}`
  if (outcome.type === 'tag_applied') detail = `${outcome.tag} · ${outcome.score}/100`
  if (outcome.type === 'gift_declined') detail = (outcome.failed_criteria || []).join(', ')
  if (outcome.guardrail_blocks?.length) detail = `guardrail: ${outcome.guardrail_blocks[0].error}`
  return { ...meta, detail }
}

export default function CreatorChat() {
  const { session } = useSession()
  const creatorId = readSelectedCreator()
  const [creator, setCreator] = useState(null)
  const [messages, setMessages] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [lastTurn, setLastTurn] = useState(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    if (!creatorId) return
    let cancelled = false
    api(`/api/creators/${creatorId}`)
      .then((data) => {
        if (!cancelled) setCreator(data.creator)
      })
      .catch((err) => setError(err))
    return () => {
      cancelled = true
    }
  }, [creatorId])

  // Restore the most recent conversation for this creator so a refresh is safe.
  useEffect(() => {
    if (!creatorId) return
    let cancelled = false
    api('/api/conversations')
      .then(async (data) => {
        const existing = (data.conversations || []).find((item) => item.creator_id === creatorId)
        if (!existing || cancelled) return
        const transcript = await api(`/api/conversations/${existing.id}`)
        if (cancelled) return
        setConversationId(existing.id)
        setMessages(
          transcript.messages.map((message) => ({
            role: message.role,
            content: message.content,
            agent: message.agent,
            created_at: message.created_at,
          })),
        )
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [creatorId])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  async function send(text) {
    const message = (text ?? draft).trim()
    if (!message || busy) return
    setError(null)
    setDraft('')
    setBusy(true)
    setMessages((current) => [...current, { role: 'creator', content: message }])
    try {
      const payload = await api('/api/chat', {
        method: 'POST',
        body: { creator_id: creatorId, message, conversation_id: conversationId },
      })
      setConversationId(payload.conversation_id)
      setLastTurn(payload)
      setMessages((current) => [
        ...current,
        {
          role: 'agent',
          content: payload.reply,
          agent: payload.agent,
          created_at: payload.created_at,
        },
      ])
    } catch (err) {
      setError(err)
      setMessages((current) => [
        ...current,
        {
          role: 'system',
          content:
            err.code === 'daily_budget' ||
            err.code === 'session_message_cap' ||
            err.code === 'credits_exhausted' ||
            err.code === 'no_api_key'
              ? err.message
              : `The demo could not complete that request: ${err.message}`,
        },
      ])
    } finally {
      setBusy(false)
    }
  }

  if (!creatorId) {
    return (
      <div className="page narrow">
        <div className="empty">
          <h3>Pick a creator first</h3>
          <p>Choose who you want to simulate before opening the chat.</p>
          <Link className="btn btn-primary" to="/">
            Choose a creator
          </Link>
        </div>
      </div>
    )
  }

  const outcome = outcomeFor(lastTurn)

  return (
    <div className="page chat-page">
      <div className="chat-shell">
        <header className="chat-header">
          <div className="chat-brand">
            <Avatar src={creator?.image} initials={creator?.initials || '··'} size={40} />
            <div>
              <strong>Demo Brand</strong>
              <span className="chat-sub">Creator concierge · replies in seconds</span>
            </div>
          </div>
          <div className="chat-header-right">
            {creator && (
              <span className="chat-as">
                You are <strong>{creator.name}</strong>
              </span>
            )}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setMessages([])
                setConversationId(null)
                setLastTurn(null)
                setError(null)
              }}
            >
              New conversation
            </button>
          </div>
        </header>

        <div className="chat-body" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="chat-intro">
              <p>
                You're chatting with <strong>Demo Brand</strong> as{' '}
                <strong>{creator?.name || creatorId}</strong>. Ask about a product, request a gift,
                or pitch a partnership.
              </p>
              <div className="quick-prompts">
                {QUICK_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="chip chip-action"
                    onClick={() => send(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message, index) => (
            <div key={`${index}-${message.role}`} className={`bubble-row ${message.role}`}>
              <div className={`bubble ${message.role}`}>
                {message.content}
                {message.role === 'agent' && message.agent && (
                  <span className="bubble-meta">{message.agent.replaceAll('_', ' ')}</span>
                )}
              </div>
            </div>
          ))}

          {busy && (
            <div className="bubble-row agent">
              <div className="bubble agent typing">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
        </div>

        <ErrorNote error={error} />

        {outcome && (
          <div className="outcome-strip">
            <Badge tone={outcome.tone}>{outcome.text}</Badge>
            {outcome.detail && <span className="muted">{outcome.detail}</span>}
            {lastTurn?.usage?.llm_calls > 0 && (
              <span className="muted right">
                {lastTurn.usage.llm_calls} model calls ·{' '}
                {lastTurn.usage.tokens_in + lastTurn.usage.tokens_out} tokens ·{' '}
                {formatters.cost(lastTurn.usage.cost_usd)}
              </span>
            )}
          </div>
        )}

        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault()
            send()
          }}
        >
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Write a message to Demo Brand…"
            disabled={busy}
            maxLength={4000}
          />
          <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim()}>
            {busy ? <Spinner /> : 'Send'}
          </button>
        </form>

        <footer className="chat-footer">
          <span>Creator view only — no agent reasoning, tool calls or configuration here.</span>
          <Link to="/logs" className="link">
            Open agent logs →
          </Link>
          {session && (
            <span className="muted">
              {session.message_count}/{session.message_cap} messages used
            </span>
          )}
        </footer>
      </div>

      {creator && (
        <aside className="chat-side">
          <h3>Verified profile</h3>
          <p className="muted small">
            Loaded server-side. The agents use these values and ignore anything a creator claims.
          </p>
          <dl className="side-metrics">
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
          <p className="side-note">{creator.content_style}</p>
          <div className="side-links">
            <Link className="link" to="/">
              Switch creator
            </Link>
          </div>
        </aside>
      )}
    </div>
  )
}
