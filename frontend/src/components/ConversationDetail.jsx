import { useMemo } from 'react'

import { formatters } from '../api'
import EventDetail from './EventDetail.jsx'
import LogTable from './LogTable.jsx'
import { Facts, Spinner } from './ui.jsx'
import { DETAIL_COLUMNS, DISPOSITIONS, conversationStats, detailRows } from './logModel.js'

/* ---------------------------------------------------------------------------
 * The right-hand pane: one conversation, reduced to its messages and the tool
 * calls that ran between them.
 *
 * Everything the orchestrator recorded about the conversation is still behind the
 * export button; this pane is the part a person reads.
 * ------------------------------------------------------------------------- */

export default function ConversationDetail({
  conversation,
  events,
  loading,
  canRelease,
  onRelease,
}) {
  const rows = useMemo(() => detailRows(events), [events])
  const stats = useMemo(() => conversationStats(events), [events])

  if (!conversation) {
    return (
      <section className="conv-detail conv-placeholder">
        <p className="muted">
          Pick a conversation on the left to see its messages and the tool calls that ran between
          them.
        </p>
      </section>
    )
  }

  const disposition = DISPOSITIONS[conversation.disposition]
  const escalated = conversation.status === 'escalated'

  return (
    <section className="conv-detail">
      <header className="conv-head">
        <div className="conv-head-main">
          <h2>{conversation.creator_name || conversation.creator_id}</h2>
          <p className="conv-head-id mono">{conversation.id}</p>
        </div>
        <div className="conv-head-actions">
          {escalated && <span className="badge badge-slate">with a human</span>}
          <a
            className="btn btn-ghost btn-sm"
            href={`/api/conversations/${conversation.id}/export`}
          >
            Export JSON
          </a>
          {escalated && canRelease && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={onRelease}>
              Return to the agent
            </button>
          )}
        </div>
      </header>

      <Facts
        className="log-facts conv-facts"
        rows={[
          { label: 'Started', value: formatters.dateTime(conversation.created_at) },
          { label: 'Intent', value: conversation.intent, mono: true },
          {
            label: 'Disposition',
            value: disposition?.label || conversation.disposition || null,
          },
          { label: 'Source', value: conversation.source === 'seeded' ? 'sample' : 'live' },
          { label: 'Config version', value: `v${conversation.config_version}`, mono: true },
          { label: 'Messages', value: stats.messages },
          { label: 'Tool calls', value: stats.toolCalls },
          { label: 'Tokens', value: stats.tokens ? stats.tokens.toLocaleString() : null },
          { label: 'Cost', value: stats.tokens ? formatters.cost(stats.cost) : null },
          // Only when there were any: a conversation that fell back to the cheaper
          // model is worth knowing about before you wonder why the agent struggled.
          {
            label: 'Model errors',
            value: stats.modelErrors
              ? `${stats.modelErrors}${stats.fallbacks ? ` (${stats.fallbacks} fell back)` : ''}`
              : null,
          },
        ]}
      />

      {loading && !events.length ? (
        <Spinner label="Loading conversation" />
      ) : (
        <LogTable
          columns={DETAIL_COLUMNS}
          rows={rows}
          renderDetail={(row) => <EventDetail row={row} />}
        />
      )}
    </section>
  )
}
