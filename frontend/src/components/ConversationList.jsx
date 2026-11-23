import { useEffect, useMemo, useRef } from 'react'

import { conversationRows } from './logModel.js'

/* ---------------------------------------------------------------------------
 * The sidebar: every conversation in chronological order, filed by disposition.
 *
 * A list of buttons rather than a table, because this is navigation -- clicking a
 * row is the only thing you can do with it.
 * ------------------------------------------------------------------------- */

export default function ConversationList({ conversations, selectedId, onSelect }) {
  const rows = useMemo(() => conversationRows(conversations), [conversations])
  const navRef = useRef(null)

  // The list runs oldest to newest, so the conversation being shown is somewhere down
  // the list. Bring it into view when the selection changes -- which, while the pane
  // is following the newest conversation, is what keeps a conversation somebody just
  // had visible instead of 240 rows below the fold.
  useEffect(() => {
    if (!selectedId) return
    const node = navRef.current?.querySelector('.conv-row.selected')
    if (node) node.scrollIntoView({ block: 'nearest' })
  }, [selectedId, rows.length])

  if (!rows.length) {
    return <p className="conv-empty muted small">No conversations yet.</p>
  }

  return (
    <nav className="conv-list" ref={navRef} aria-label="Conversations">
      {rows.map((row) => {
        const active = row.key === selectedId
        return (
          <button
            key={row.key}
            type="button"
            className={`conv-row${active ? ' selected' : ''}`}
            aria-current={active ? 'true' : undefined}
            title={row.title}
            onClick={() => onSelect(row.key)}
          >
            <span className="conv-row-top">
              <span className="conv-creator">{row.creator}</span>
              <span className={`conv-disposition tone-${row.tone || 'slate'}`}>
                {row.disposition}
              </span>
            </span>
            <span className="conv-row-bottom">
              <span className="conv-id mono">{row.shortId}</span>
              <span className="conv-time">{row.time}</span>
              {row.sample && <span className="conv-tag">sample</span>}
              {row.escalated && <span className="conv-tag">with a human</span>}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
