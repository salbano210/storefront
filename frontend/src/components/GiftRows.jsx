import { useState } from 'react'

import CriteriaBuilder from './CriteriaBuilder.jsx'
import { Field, NumberInput } from './AdminFields.jsx'

/* ---------------------------------------------------------------------------
 * The gift offers, as a folder of expandable cards. Each gift owns its criteria
 * outright -- there is no brand-level gate -- plus the link the agent can share
 * with a creator who does not qualify for it.
 * ------------------------------------------------------------------------- */
export default function GiftRows({ offers = [], metrics = [], onChange }) {
  const [open, setOpen] = useState(offers[0]?.id || null)

  function patch(index, next) {
    onChange(offers.map((item, i) => (i === index ? { ...item, ...next } : item)))
  }

  function add() {
    const index = offers.length + 1
    const id = `gift-${index}`
    onChange([
      ...offers,
      {
        id,
        name: 'New gift',
        sku: `GT-NEW-${index}`,
        description: '',
        url: '',
        retail_price: 0,
        tags: [],
        criteria: [],
      },
    ])
    setOpen(id)
  }

  return (
    <div className="gift-list">
      {offers.map((offer, index) => {
        const isOpen = open === offer.id
        return (
          <div className={`gift-card${isOpen ? ' open' : ''}`} key={`${offer.id}-${index}`}>
            <button
              type="button"
              className="gift-head"
              onClick={() => setOpen(isOpen ? null : offer.id)}
            >
              <span className="gift-caret">{isOpen ? '▾' : '▸'}</span>
              <span className="gift-name">{offer.name || 'Untitled gift'}</span>
              <span className="muted small">{offer.sku}</span>
              <span className="gift-meta muted small">
                {offer.criteria?.length
                  ? `${offer.criteria.length} criteri${offer.criteria.length === 1 ? 'on' : 'a'}`
                  : 'open to all'}
              </span>
              <span className="gift-price">${offer.retail_price || 0}</span>
            </button>

            {isOpen && (
              <div className="gift-body">
                <div className="admin-grid">
                  <Field label="Gift name">
                    <input
                      value={offer.name}
                      onChange={(e) => patch(index, { name: e.target.value })}
                    />
                  </Field>
                  <Field label="SKU">
                    <input
                      value={offer.sku}
                      onChange={(e) => patch(index, { sku: e.target.value })}
                    />
                  </Field>
                  <Field label="Retail price">
                    <NumberInput
                      value={offer.retail_price}
                      min={0}
                      suffix="USD"
                      onChange={(value) => patch(index, { retail_price: value })}
                    />
                  </Field>
                  <Field label="Gift id" hint="Used by check_gifting_eligibility and the tools.">
                    <input
                      value={offer.id}
                      onChange={(e) => patch(index, { id: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Item link"
                    wide
                    hint="Shared with a creator who does not qualify, so they can buy it."
                  >
                    <input
                      value={offer.url}
                      placeholder="https://…"
                      onChange={(e) => patch(index, { url: e.target.value })}
                    />
                  </Field>
                  <Field label="Description" wide hint="Feeds the agent's product search.">
                    <input
                      value={offer.description}
                      onChange={(e) => patch(index, { description: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Search tags"
                    wide
                    hint="Comma separated. 'dry skin', 'cleanser' — how a creator might describe it."
                  >
                    <input
                      value={(offer.tags || []).join(', ')}
                      onChange={(e) =>
                        patch(index, {
                          tags: e.target.value
                            .split(',')
                            .map((part) => part.trim().toLowerCase())
                            .filter(Boolean),
                        })
                      }
                    />
                  </Field>
                </div>

                <h4 className="gift-subhead">Criteria — every rule must pass</h4>
                <CriteriaBuilder
                  rules={offer.criteria || []}
                  metrics={metrics}
                  onChange={(rules) => patch(index, { criteria: rules })}
                />

                <button
                  type="button"
                  className="btn btn-ghost btn-sm gift-remove"
                  onClick={() => onChange(offers.filter((_, i) => i !== index))}
                >
                  Remove this gift
                </button>
              </div>
            )}
          </div>
        )
      })}

      <button type="button" className="btn btn-ghost btn-sm" onClick={add}>
        + Add gift
      </button>
    </div>
  )
}
