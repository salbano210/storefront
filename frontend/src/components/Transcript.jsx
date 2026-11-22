import { useMemo, useState } from 'react'

import { formatters } from '../api'
import { Badge } from './ui.jsx'
import { CriteriaTable } from './CriteriaTable.jsx'
import {
  OUTCOMES,
  STEP_ICONS,
  decisionFacts,
  groupTurns,
  humanAgent,
  stepSentence,
} from './transcriptModel.js'

/* ---------------------------------------------------------------------------
 * The transcript: answer first, evidence underneath.
 *
 * Each turn opens with what was decided and the facts behind it, then a trace
 * that reads as sentences rather than tool names and JSON. The wording lives in
 * transcriptModel.js, where it can be checked against a real event log.
 * ------------------------------------------------------------------------- */

function Facts({ rows }) {
  const shown = rows.filter((row) => row.value)
  if (!shown.length) return null
  return (
    <dl className="step-facts">
      {shown.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function BucketBody({ event }) {
  const payload = event.payload || {}
  const result = payload.result || {}
  const tool = payload.tool
  const [raw, setRaw] = useState(false)

  const offers = event.type === 'criteria_evaluated' ? payload.offers || [] : []
  const components = event.type === 'criteria_evaluated' ? payload.components || [] : []
  const gifts = tool === 'find_available_gifts' ? result.gifts || [] : []

  return (
    <div className="bucket-body">
      {event.type === 'address_validated' && (
        <Facts
          rows={[
            { label: 'Given', value: payload.input || '—' },
            { label: 'Shipping to', value: payload.normalised },
            { label: 'Suggested', value: payload.suggestion },
            { label: 'Why not', value: payload.reason },
            { label: 'Address on file', value: payload.address_on_file },
          ]}
        />
      )}

      {event.type === 'gift_issued' && (
        <Facts
          rows={[
            { label: 'Order', value: payload.order_id },
            { label: 'Product', value: payload.product },
            { label: 'Shipping to', value: payload.shipping_address },
            { label: 'Address on file', value: payload.address_on_file },
            {
              label: 'Address',
              value: payload.address_changed ? 'different from the one on file' : null,
            },
          ]}
        />
      )}

      {gifts.length > 0 && (
        <ul className="step-list">
          {gifts.map((gift) => (
            <li key={gift.gift_id}>
              <span className="step-list-name">{gift.name}</span>
              <span className="muted small">
                {gift.sku} · {formatters.money(gift.retail_price)}
              </span>
              <Badge tone={gift.eligible ? 'sage' : 'slate'}>
                {gift.eligible ? 'qualifies' : 'not eligible'}
              </Badge>
            </li>
          ))}
        </ul>
      )}

      {offers.map((offer) => (
        <div key={offer.gift_id} className="step-offer">
          <div className="step-offer-head">
            <strong>{offer.name}</strong>
            <Badge tone={offer.eligible ? 'sage' : 'red'}>
              {offer.eligible ? 'qualifies' : 'does not qualify'}
            </Badge>
            <span className="muted small">{offer.sku}</span>
          </div>
          <CriteriaTable checks={offer.checks || []} />
        </div>
      ))}

      {components.length > 0 && (
        <ul className="step-list">
          {components.map((component) => (
            <li key={component.key}>
              <span className="step-list-name">{component.label}</span>
              <span className="muted small">{component.detail}</span>
              <span className="mono">
                {component.points} / {component.weight}
              </span>
            </li>
          ))}
        </ul>
      )}

      {payload.response_instructions && (
        <p className="bucket-note">
          <strong>What the agent was told to say next:</strong> {payload.response_instructions}
        </p>
      )}

      {payload.patterns && (
        <p className="bucket-note">
          Patterns matched: <code>{payload.patterns.join(', ')}</code>. {payload.action}
        </p>
      )}

      {payload.message && !payload.result && <pre className="bucket-json">{payload.message}</pre>}

      <button type="button" className="step-raw-toggle" onClick={() => setRaw(!raw)}>
        {raw ? 'Hide the raw payload' : 'Show the raw payload'}
      </button>
      {raw && (
        <pre className="bucket-json">
          {JSON.stringify(
            { arguments: payload.arguments, result: payload.result || payload },
            null,
            2
          )}
        </pre>
      )}
    </div>
  )
}

function Bucket({ event }) {
  const [open, setOpen] = useState(false)
  const { text, meta } = stepSentence(event)
  const tone = event.status === 'blocked' ? 'blocked' : event.status === 'warn' ? 'warn' : ''

  return (
    <div className={`step ${tone}`}>
      <button type="button" className="step-head" onClick={() => setOpen(!open)}>
        <span className="step-caret">{open ? '▾' : '▸'}</span>
        <span className="step-icon">{STEP_ICONS[event.type] || '•'}</span>
        <span className="step-text">{text}</span>
        {meta && <span className="step-meta muted small">{meta}</span>}
      </button>
      {open && <BucketBody event={event} />}
    </div>
  )
}

/** The answer, before the evidence: what was decided, and the facts behind it. */
function DecisionStrip({ turn }) {
  const outcome = turn.outcome
  if (!outcome) return null
  const meta = OUTCOMES[outcome.type] || {
    tone: 'slate',
    icon: '•',
    label: outcome.type.replaceAll('_', ' '),
  }
  const facts = decisionFacts(outcome)

  return (
    <div className={`decision ${meta.tone}`}>
      <div className="decision-head">
        <span className="decision-icon">{meta.icon}</span>
        <span className="decision-label">{meta.label}</span>
        {facts.map((fact) => (
          <span className="decision-fact" key={fact}>
            {fact}
          </span>
        ))}
      </div>
      <div className="decision-meta muted small">
        {humanAgent(turn.agent)}
        {turn.modelCalls ? ` · ${turn.modelCalls} model calls` : ''}
        {turn.tokens ? ` · ${turn.tokens.toLocaleString()} tokens` : ''}
        {turn.cost ? ` · ${formatters.cost(turn.cost)}` : ''}
        {turn.durationMs ? ` · ${(turn.durationMs / 1000).toFixed(1)}s` : ''}
      </div>
    </div>
  )
}

function Turn({ turn }) {
  const [open, setOpen] = useState(true)

  return (
    <article className="turn">
      <div className="bubble creator">
        <div className="bubble-meta">
          {turn.creator} · {formatters.time(turn.startedAt)}
        </div>
        <p>{turn.message || '—'}</p>
      </div>

      <DecisionStrip turn={turn} />

      {turn.steps.length > 0 && (
        <div className="turn-trace">
          <button type="button" className="trace-head" onClick={() => setOpen(!open)}>
            <span className="step-caret">{open ? '▾' : '▸'}</span>
            <span className="trace-agent">How it decided</span>
            <span className="muted small">
              {turn.steps.length} step{turn.steps.length === 1 ? '' : 's'}
            </span>
          </button>
          {open && (
            <div className="trace-steps">
              {turn.steps.map((event) => (
                <Bucket key={event.id} event={event} />
              ))}
            </div>
          )}
        </div>
      )}

      {turn.reply && (
        <div className="bubble agent">
          <div className="bubble-meta">
            <span>{humanAgent(turn.agent)}</span>
          </div>
          <p>{turn.reply}</p>
        </div>
      )}
    </article>
  )
}

export default function Transcript({ events }) {
  const turns = useMemo(() => groupTurns(events), [events])
  if (!turns.length) {
    return (
      <p className="muted">No events yet. Send a message from a creator chat to see it here.</p>
    )
  }
  return (
    <div className="transcript">
      {turns.map((turn) => (
        <Turn key={turn.key} turn={turn} />
      ))}
    </div>
  )
}
