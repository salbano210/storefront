import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, formatters } from '../api'
import { writeSelectedCreator } from '../session.jsx'
import { Avatar, Chip, ErrorNote, Spinner } from '../components/ui.jsx'

export default function CreatorSelect() {
  const [creators, setCreators] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  async function load() {
    setLoading(true)
    try {
      const data = await api('/api/creators')
      setCreators(data.creators)
      setError(null)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function choose(creatorId) {
    writeSelectedCreator(creatorId)
    navigate('/chat')
  }

  return (
    <div className="page">
      <section className="hero">
        <p className="eyebrow">Multi-agent creator relationship demo</p>
        <h1>Who are you today?</h1>
        <p className="lede">
          Pick a creator to simulate. Your chosen profile is loaded from the Storefront backend — the
          agents always decide on verified profile data, never on what the creator claims.
        </p>
      </section>

      <ErrorNote error={error} onRetry={load} />
      {loading && <Spinner label="Loading creator profiles" />}

      <div className="creator-grid">
        {creators.map((creator) => (
          <article key={creator.id} className="creator-card">
            <header>
              <Avatar src={creator.image} initials={creator.initials} size={52} />
              <div>
                <h2>{creator.name}</h2>
                <p className="handle">{creator.handle}</p>
              </div>
            </header>
            <p className="tagline">“{creator.tagline}”</p>
            <dl className="metrics">
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
            <p className="content-style">{creator.content_style}</p>
            <button type="button" className="btn btn-primary" onClick={() => choose(creator.id)}>
              Chat as {creator.name.split(' ')[0]}
            </button>
          </article>
        ))}
      </div>

      <section className="how-it-works">
        <h2>How the demo works</h2>
        <ol className="steps">
          <li>
            <strong>Route.</strong> A routing agent classifies your message as a gifting request, a
            partnership pitch, or a general question, then hands off to a specialist agent.
          </li>
          <li>
            <strong>Execute.</strong> The specialist calls real backend tools — verified profile
            lookup, criteria evaluation, order placement, tagging — and every call is logged.
          </li>
          <li>
            <strong>Enforce.</strong> Eligibility maths, SKU checks and duplicate protection run in
            server-side code. A cheap model can never approve something the brand rules forbid.
          </li>
          <li>
            <strong>Observe.</strong> The Agent logs page shows the full trace, including the exact
            configuration version each decision was made under.
          </li>
        </ol>
        <p className="muted">
          Every creator below produces a different outcome: two qualify for gifting, two do not, and
          one will happily exaggerate their metrics.
        </p>
      </section>
    </div>
  )
}
