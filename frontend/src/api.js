const ADMIN_STORAGE = 'storefront.admin_passcode'

/* ---------------------------------------------------------------------------
 * The demo runs on the host OpenRouter key. The only thing the browser holds is
 * the brand-admin passcode (sessionStorage, per tab), sent as a header.
 * ------------------------------------------------------------------------- */
export function getAdminPasscode() {
  try {
    return sessionStorage.getItem(ADMIN_STORAGE) || ''
  } catch {
    return ''
  }
}

export function setAdminPasscode(value) {
  try {
    if (value) sessionStorage.setItem(ADMIN_STORAGE, value)
    else sessionStorage.removeItem(ADMIN_STORAGE)
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(status, code, message, payload) {
    super(message)
    this.status = status
    this.code = code
    this.payload = payload
  }
}

export async function api(path, { method = 'GET', body, withAdmin = true } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (withAdmin) {
    const passcode = getAdminPasscode()
    if (passcode) headers['X-Admin-Passcode'] = passcode
  }

  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text }
    }
  }

  if (!response.ok) {
    const detail = data?.detail
    let code = 'error'
    let message = `Request failed (${response.status})`
    if (typeof detail === 'string') {
      message = detail
    } else if (detail && typeof detail === 'object') {
      code = detail.code || code
      message = detail.message || message
    } else if (Array.isArray(detail)) {
      message = detail.map((item) => `${(item.loc || []).join('.')}: ${item.msg}`).join('; ')
    }
    throw new ApiError(response.status, code, message, data)
  }

  return data
}

export const formatters = {
  followers(value) {
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`
    if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}K`
    return `${value}`
  },
  pct(value) {
    return `${(value * 100).toFixed(1)}%`
  },
  money(value, digits = 0) {
    return `$${Number(value || 0).toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`
  },
  cost(value) {
    if (value === null || value === undefined) return '—'
    if (value === 0) return '$0.00'
    if (value < 0.01) return `$${value.toFixed(5)}`
    return `$${value.toFixed(4)}`
  },
  time(value) {
    if (!value) return ''
    return new Date(value).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  },
  dateTime(value) {
    if (!value) return ''
    return new Date(value).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  },
}
