import { Fragment, useState } from 'react'

/* ---------------------------------------------------------------------------
 * One row table, used by both log views.
 *
 * A log is read by scanning columns and then opening the one row that matters,
 * so this is deliberately plain: a sticky header, one line per record, and the
 * detail on demand. No cards, no colour coding beyond the status a row carries.
 * ------------------------------------------------------------------------- */

function cellClass(column, row) {
  const tone = row.tones?.[column.key]
  return [
    column.mono ? 'mono' : '',
    column.align === 'right' ? 'right' : '',
    column.wide ? 'wide' : '',
    tone ? `cell-${tone}` : '',
  ]
    .filter(Boolean)
    .join(' ')
}

export default function LogTable({ columns, rows, renderDetail }) {
  const [open, setOpen] = useState(() => new Set())

  function toggle(key) {
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (!rows.length) return null

  return (
    <div className="log-table-wrap">
      <table className="log-table">
        <thead>
          <tr>
            <th className="log-caret-col" aria-label="Expand" />
            {columns.map((column) => (
              <th key={column.key} className={cellClass(column, {})} scope="col">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const expanded = open.has(row.key)
            return (
              <Fragment key={row.key}>
                <tr
                  className={`log-row${row.tone ? ` tone-${row.tone}` : ''}${
                    expanded ? ' open' : ''
                  }`}
                  onClick={() => toggle(row.key)}
                >
                  <td className="log-caret-col">
                    <button
                      type="button"
                      className="log-toggle"
                      aria-expanded={expanded}
                      onClick={(event) => {
                        // The row already toggles; stop this from toggling twice.
                        event.stopPropagation()
                        toggle(row.key)
                      }}
                    >
                      {expanded ? '−' : '+'}
                    </button>
                  </td>
                  {columns.map((column) => {
                    const text = row.cells[column.key] ?? '—'
                    return (
                      <td key={column.key} className={cellClass(column, row)} title={text}>
                        {text}
                      </td>
                    )
                  })}
                </tr>
                {expanded && (
                  <tr className="log-detail">
                    <td colSpan={columns.length + 1}>{renderDetail(row)}</td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
