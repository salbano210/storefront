import { useState } from 'react'

export function Avatar({ src, initials, size = 46 }) {
  const [failed, setFailed] = useState(false)
  return (
    <span
      className="avatar"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.34) }}
    >
      <span className="avatar-initials">{initials}</span>
      {src && !failed && (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      )}
    </span>
  )
}

export function Chip({ children, tone = 'neutral', title }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  )
}

export function Badge({ children, tone = 'neutral' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function Spinner({ label }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" />
      {label && <span className="spinner-label">{label}</span>}
    </span>
  )
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}

/**
 * A definition list of labelled values, skipping the rows with nothing to say.
 * Used by the log detail panes, where most fields are absent on most events.
 */
export function Facts({ rows, className = 'log-facts' }) {
  const shown = rows.filter(
    (row) => row.value !== null && row.value !== undefined && row.value !== ''
  )
  if (!shown.length) return null
  return (
    <dl className={className}>
      {shown.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.mono ? 'mono' : undefined}>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="error-note" role="alert">
      <strong>{error.code === 'admin_passcode_required' ? 'Passcode required' : 'Something went wrong'}</strong>
      <span>{error.message}</span>
      {onRetry && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}
