import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { formatters } from './api'
import { useSession } from './session.jsx'
import CreatorSelect from './views/CreatorSelect.jsx'
import CreatorChat from './views/CreatorChat.jsx'
import AdminConfig from './views/AdminConfig.jsx'
import Logs from './views/Logs.jsx'
import KeyPanel from './components/KeyPanel.jsx'

const NAV = [
  { to: '/', label: 'Creators', end: true },
  { to: '/chat', label: 'Creator chat' },
  { to: '/logs', label: 'Agent logs' },
  { to: '/admin', label: 'Brand admin' },
]

export default function App() {
  const { session, hasKey, sessionError } = useSession()

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="wordmark">shopmy</span>
            <span className="brand-divider" />
            <span className="brand-chip">Demo Brand</span>
          </div>
          <nav className="nav">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            <KeyPanel />
            {session && (
              <div className="usage-pill" title="Model spend in this browser session">
                <span className={`dot ${session.key_mode === 'byok' ? 'dot-own' : 'dot-host'}`} />
                {session.key_mode === 'byok' ? 'Your key' : 'Demo key'}
                <span className="usage-sep">·</span>
                {formatters.cost(session.session_cost_usd)}
              </div>
            )}
          </div>
        </div>
        {sessionError && <div className="topbar-error">Backend unavailable: {sessionError}</div>}
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<CreatorSelect />} />
          <Route path="/chat" element={<CreatorChat />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/admin" element={<AdminConfig />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="footer">
        <span>
          Prototype. External APIs (order placement, tagging, creator profiles) are mocked, but the
          multi-agent execution, business rules and audit log are real.
        </span>
        {!hasKey && <span className="footer-note">Running on the demo OpenRouter key.</span>}
      </footer>
    </div>
  )
}
