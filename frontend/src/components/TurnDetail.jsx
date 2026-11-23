import { useMemo } from 'react'

import EventDetail from './EventDetail.jsx'
import LogTable from './LogTable.jsx'
import { Facts } from './ui.jsx'
import { OUTCOMES, STEP_COLUMNS, decisionFacts, formatSpan, stepRows } from './logModel.js'

/* ---------------------------------------------------------------------------
 * The body of an expanded turn: what was said, what was decided, and the steps
 * that got there -- the last of which is the event table again, one level down.
 * ------------------------------------------------------------------------- */

export default function TurnDetail({ row }) {
  const turn = row.turn
  const outcome = turn.outcome || null
  const meta = outcome ? OUTCOMES[outcome.type] : null
  const steps = useMemo(() => stepRows(turn.steps), [turn.steps])

  return (
    <div className="log-detail-body">
      <Facts
        rows={[
          { label: 'Conversation', value: row.conversation?.id || null, mono: true },
          { label: 'Creator', value: row.conversation?.creator_name || turn.creator },
          { label: 'Handled by', value: turn.agent },
          { label: 'Outcome', value: meta?.label || outcome?.type || null },
          ...decisionFacts(outcome).map((fact, index) => ({
            label: index === 0 ? 'Decision' : '',
            value: fact,
          })),
          { label: 'Model calls', value: turn.modelCalls || null },
          { label: 'Span', value: formatSpan(turn.durationMs) },
        ]}
      />

      {turn.message && (
        <div className="log-message">
          <span className="log-message-role">creator</span>
          <p>{turn.message}</p>
        </div>
      )}
      {turn.reply && (
        <div className="log-message">
          <span className="log-message-role">agent</span>
          <p>{turn.reply}</p>
        </div>
      )}

      <LogTable
        columns={STEP_COLUMNS}
        rows={steps}
        renderDetail={(step) => <EventDetail row={step} />}
      />
    </div>
  )
}
