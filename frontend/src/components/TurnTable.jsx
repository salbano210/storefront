import { useMemo } from 'react'

import LogTable from './LogTable.jsx'
import TurnDetail from './TurnDetail.jsx'
import { TURN_COLUMNS, turnRows } from './logModel.js'

/* ---------------------------------------------------------------------------
 * The same stream grouped into turns: one row per creator message, with the
 * decision it produced and the steps that got there.
 *
 * This is the flat table with a different key, so it reads as the same kind of
 * record -- a turn is just the row you would otherwise have to assemble by
 * filtering on a conversation id.
 * ------------------------------------------------------------------------- */

export default function TurnTable({ events, conversations }) {
  const rows = useMemo(() => turnRows(events, conversations), [events, conversations])
  return (
    <LogTable
      columns={TURN_COLUMNS}
      rows={rows}
      renderDetail={(row) => <TurnDetail row={row} />}
    />
  )
}
