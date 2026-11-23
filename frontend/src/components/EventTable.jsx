import { useMemo } from 'react'

import EventDetail from './EventDetail.jsx'
import LogTable from './LogTable.jsx'
import { EVENT_COLUMNS, eventRows } from './logModel.js'

/** The flat stream: one row per event, which is what the page opens on. */
export default function EventTable({ events, conversations }) {
  const rows = useMemo(() => eventRows(events, conversations), [events, conversations])
  return (
    <LogTable
      columns={EVENT_COLUMNS}
      rows={rows}
      renderDetail={(row) => <EventDetail row={row} />}
    />
  )
}
