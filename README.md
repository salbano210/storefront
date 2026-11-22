# Storefront — Multi-Agent Creator Relationship Demo

**Storefront** is a prototype creator-relationship platform. It simulates creator ⇄ brand
conversations handled end-to-end by a **configurable, observable multi-agent system**.

A creator picks their profile, chats with *Summer Soft Skincare*, and the agents classify the message, look up
**verified** profile data, evaluate the brand's configured rules, call tools (issue a gift, apply a
tag), and answer naturally. A separate logs page shows every routing decision, handoff, tool call,
criteria check and guardrail block — produced by real execution, never fabricated.

**Repo:** https://github.com/salbano210/storefront
**Live demo:** https://storefront-dalw.onrender.com — free tier, so the first request after
~15 minutes idle takes 30–60 seconds to wake up. See [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Why this is interesting

The hard part of putting an LLM in front of a business process is not the chat. It is making the
model *incapable* of doing the wrong thing. This demo takes a deliberate position:

> **The model proposes. Python decides.**

| Concern | Who handles it |
|---|---|
| Intent classification, product/SKU resolution, phrasing, tone | LLM |
| Follower / engagement / revenue thresholds, SKU eligibility, duplicate gifts, tag labels | **Python** |
| Refusing an out-of-policy tool call | **Python guardrails**, logged as `guardrail_blocked` |

Concretely:

- `check_gifting_eligibility` and `check_pitch_fit` are **deterministic Python functions** over the
  verified profile and the live brand configuration. Their output is the authoritative decision.
- `issue_gift` re-runs the criteria check itself and refuses when anything is unmet. A model cannot
  talk its way past it, and it cannot invent a successful order.
- `place_tag` only accepts a configured label, and only the label the scoring function computed.
- Creator-supplied claims are **never** parsed for metrics. Only the verified profile enters the
  evaluation. A creator who claims 500k followers is measured at their real 8.2k.
- Every threshold lives in a versioned JSON configuration edited through the admin UI. Change
  `minimum_followers` and the very next request is evaluated against the new value — no deploy.
- Each event records the `config_version` in force, so the audit trail proves which rules applied.

Because the risky logic is deterministic, the models can be cheap: the whole demo runs on
`google/gemini-2.5-flash-lite`, and a full conversation costs roughly **$0.0005–0.001**.

---

## Demo scenarios

| # | Scenario | What to do | What you should see |
|---|---|---|---|
| 1 | Successful gifting | Chat as **Kathryn Bigelow**, ask for the Barrier Repair Serum | Criteria table all green, `issue_gift` succeeds, order ID returned |
| 2 | Rejected gifting | Chat as **Paul Thomas Anderson**, ask for the same serum | Criteria table shows follower + revenue fails, no order, honest explanation |
| 3 | Successful pitch | Chat as **Sofia Coppola**, pitch a partnership | Fit score 100 → `Excellent Match` tag applied |
| 4 | Manipulation attempt | Chat as **Peter Bogdanovich**, claim 500k followers and $80k sales | Decision uses verified 8,210 followers; `guardrail_blocked` event; no gift |
| 5 | Multi-turn clarification | Ask "can I get a free gift?" without naming a product | Agent asks which product, then completes on the follow-up (`agent_resumed`) |
| 6 | Config changes behaviour | In `/admin`, lower `minimum_followers` to 1,000 and save | The next request from the previously rejected creator is approved, stamped `config v2` |
| 7 | Different profiles, different outcomes | Same request as two creators | One approved, one declined, both from the same rules |
| 8 | Non-giftable SKU | Ask for the Limited Edition Holiday Kit | Refused; the SKU is in the catalog but flagged not giftable |
| 9 | Cheap-model misbehaviour | (Built in) the model invents a label or skips a tool | Guardrail blocks or nudges it, and the log shows the recovery |
| 10 | Editing verified metrics | On `/`, click **Edit verified metrics**, raise a failing creator's followers, save | The same creator is now approved, and the log shows a `creator_updated` event with the before/after values |

> **A note on the creator names:** the five profiles are named after film directors so they're easy
> to keep straight while demoing. Their metrics, categories, audiences, bios and shipping addresses
> are entirely fictional, and no real person is represented.

---

## Architecture

One deployable service: FastAPI serves the JSON API **and** the built React SPA, so the whole demo
lives at a single shareable URL with no CORS.

```
Browser ──https──▶ FastAPI (uvicorn :10000)
                    ├── /api/session, /api/creators, /api/chat
                    ├── /api/config  (admin passcode)
                    ├── /api/logs, /api/conversations
                    ├── /api/models, /api/key/test, /api/health
                    └── /*  → frontend/dist/index.html  (SPA fallback)
                          │
                          ├── SQLite: sessions, conversations, messages, events,
                          │           gift_orders, tags_applied, config_versions
                          └── OpenRouter (server-side only)
```

### How one turn flows

```
POST /api/chat
 ├─ resolve the creator from the database (never trust client-supplied metrics)
 ├─ event: message_received
 ├─ router_agent      → structured JSON: intent, confidence, reasoning
 ├─ event: intent_classified
 ├─ event: agent_handoff (or agent_resumed when continuing a pending task)
 ├─ specialist agent (bounded tool loop, max 5 iterations, required-tool enforcement)
 │    gift_request_agent:
 │      get_creator_profile        → verified metrics              [profile_lookup]
 │      check_gifting_eligibility  → per-criterion pass/fail in Python [criteria_evaluated]
 │      list_products              → catalog + giftable flags
 │      issue_gift                 → guarded; idempotent          [gift_issued]
 │    creator_pitch_agent:
 │      get_creator_profile        → verified metrics              [profile_lookup]
 │      check_pitch_fit            → 0-100 score + label in Python [criteria_evaluated]
 │      place_tag                  → guarded to the computed label [tag_applied]
 ├─ event: message_sent (with the tool trace and the outcome)
 └─ response: reply + outcome + per-turn tokens/cost
```

### Guardrails (all server-side, all logged)

| Guardrail | Blocks | Event |
|---|---|---|
| Eligibility gate | `issue_gift` when any configured criterion fails | `guardrail_blocked` |
| SKU gate | Unknown SKUs and SKUs flagged not giftable | `guardrail_blocked` |
| Identity enforcement | Creator-supplied name/address that differ from the verified profile — the verified values are used instead | `guardrail_adjusted` |
| Idempotency | Duplicate gift for the same creator + SKU returns the existing order; a DB unique constraint is the backstop | `tool_call_completed` (`duplicate: true`) |
| Label gate | Tags that are not configured, or that disagree with the computed score | `guardrail_blocked` |
| Argument validation | Any tool call with malformed or extra fields (Pydantic, `extra="forbid"`) | `tool_call_failed` |
| Required tools | A specialist answering without consulting the authoritative tools gets nudged with `tool_choice="required"` | visible in the trace |
| Budget guards | Session message cap, daily spend cap, per-IP burst limit | `budget_blocked` |
| Secret scrubbing | Anything matching `sk-or-*` is redacted before it reaches the database | enforced by tests |

---

## The four views

| View | What it shows |
|---|---|
| **Creators** (`/`) | Pick which creator to simulate. Five profiles with genuinely different verified metrics — three qualify for gifting, two do not, one exaggerates. An **Edit verified metrics** button (admin passcode) turns every card into a form so you can change the numbers the agents decide on, live. |
| **Creator chat** (`/chat`) | The creator-facing experience. Free-form multi-turn chat with Summer Soft Skincare, quick prompts for each scenario, a typing indicator, and an outcome chip (gift approved / not eligible / tag applied). No agent reasoning, tools or configuration is visible here. |
| **Agent logs** (`/logs`) | The full internal trace: intent classification, agent handoffs, verified profile lookups, criteria tables with pass/fail per rule, tool calls with arguments and results, guardrail blocks, per-call model/token/cost/latency, and the final reply. Filter by creator, conversation, agent or event type. |
| **Brand admin** (`/admin`) | Passcode-gated configuration editor: gifting criteria, gifting catalog with SKUs, pitch criteria, classification labels and thresholds, ideal-profile targets, scoring weights, per-agent instructions and per-agent model selection. Form view plus a raw JSON editor and the live JSON schema. |

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + Uvicorn, Pydantic v2, SQLAlchemy 2 async + aiosqlite | Fast to build, fully typed, same pattern as my other projects |
| LLM access | OpenRouter via the OpenAI-compatible SDK | One key, any model, per-agent model selection, real cost data returned |
| Frontend | React 19 + Vite, plain CSS, `react-router-dom` | No build complexity, no UI framework, quick to load |
| Deployment | Render Blueprint, single Docker web service | One URL to share, blueprint-managed env vars |

### Default models and cost

| Agent | Model | Price (in / out per 1M tokens) |
|---|---|---|
| Router | `google/gemini-2.5-flash-lite` | $0.10 / $0.40 |
| Gift request | `google/gemini-2.5-flash-lite` | $0.10 / $0.40 |
| Creator pitch | `google/gemini-2.5-flash-lite` | $0.10 / $0.40 |
| General inquiry | `meta-llama/llama-3.1-8b-instruct` | $0.02 / $0.04 |
| Fallback (on 429/5xx) | `openai/gpt-4.1-nano` | $0.10 / $0.40 |

A live smoke run of all scenarios — 14 creator turns, ~50 model calls — cost **$0.005**.

---

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp ../.env.example .env      # then set OPENROUTER_API_KEY
uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

### Frontend

```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173, proxies /api to :8000
```

### Tests

```bash
cd backend
.venv/bin/python -m pytest -q          # 49 tests, no network required
```

The suite covers the guardrails end-to-end with a deterministic fake model: ineligible gifting
refusal, duplicate suppression, non-giftable SKUs, argument validation, verified-data-wins,
config-change-affects-behaviour, session isolation, secret redaction and the budget guards.

### Live smoke test (real models, real key)

```bash
cd backend
OPENROUTER_API_KEY=sk-or-v1-... .venv/bin/python ../scripts/smoke_demo.py
```

Runs every demo scenario against the real OpenRouter models and asserts the outcomes. Costs about
half a cent. **Run this before you present.**

### Debugging a single turn

```bash
# deterministic fake model
.venv/bin/python ../scripts/debug_turn.py "Can I get the serum?" creator_001
# real models
REAL_LLM=1 OPENROUTER_API_KEY=sk-or-v1-... .venv/bin/python ../scripts/debug_turn.py "Can I get the serum?" creator_001
```

Prints the outcome, the reply and the full event trace.


---

## Model spend and abuse guards

Every agent call runs on a single **host OpenRouter key** held in the server environment. Visitors
need no key and no setup — they open the link and chat.

The key is never returned by any endpoint and never persisted: a redactor strips anything matching
`sk-or-*` before it can reach the database or the logs, and a test asserts the host key never appears
in the audit trail.

Three guards protect it:

| Guard | Default | Behaviour when tripped |
|---|---|---|
| Per-session message cap | `SESSION_MESSAGE_CAP=40` | `429` with *"Click New conversation to start fresh"* — starting a new conversation refreshes the allowance |
| Daily spend ceiling | `MAX_DAILY_SPEND_USD=2.00` | `429` until the next UTC day; logged as `budget_blocked` |
| Per-IP burst limit | `IP_RATE_LIMIT_PER_MINUTE=10` | `429` for a minute |

`POST /api/key/test` is an **admin-gated** operator diagnostic: it reports the host key's credit
limit, total and daily usage, and remaining free-model requests, straight from OpenRouter.

---

## Verified creator profiles

Creator profiles are the authoritative data every decision is made on, so they live in the
**database** (seeded from `backend/app/data/creators.json` on first boot) rather than in the frontend
or in a prompt. That is what makes "the agents ignore what the creator claims" a structural property
instead of a polite instruction.

A brand admin can edit every metric from the Creators page:

1. `/` → **Edit verified metrics** → enter the admin passcode.
2. Each card becomes a form: name, handle, followers, engagement rate, brand-attributed revenue,
   Storefront sales, audience age range, top gender, categories, shipping address, content style,
   tagline, bio. Nested data you don't edit (audience breakdown, previous gifts, avatar) is preserved.
3. **Save metrics** persists it, and the next message the agents handle uses the new numbers.

Every edit is recorded in the audit trail as a `creator_updated` event carrying the before/after value
of each changed field — so you can show exactly which numbers produced a decision. `POST
/api/creators/reset` restores the repo fixtures.

| Endpoint | Access | Purpose |
|---|---|---|
| `GET /api/creators` | public | Card data (no revenue or shipping address) |
| `GET /api/creators?full=true` | admin | Full verified profiles, for the editor |
| `GET /api/creators/{id}/verified` | admin | One full profile |
| `PUT /api/creators/{id}` | admin | Update a profile (validated, audited) |
| `POST /api/creators/reset` | admin | Restore every profile from the repo fixtures |

Values are validated: engagement rate must be a 0–1 fraction, follower counts and revenue cannot be
negative, and the `id` in the body must match the path.

---

## Configuration reference

Everything the agents decide with lives in the brand configuration
(`backend/app/data/brand_config.default.json` seeds version 1) and is editable at `/admin`.

```jsonc
{
  "brand": { "name", "description", "positioning", "tone" },
  "gifting": {
    "criteria": {
      "minimum_followers", "minimum_engagement_rate", "minimum_brand_revenue",
      "required_categories", "notes"
    },
    "eligible_products": [{ "name", "sku", "description", "eligible", "retail_price" }]
  },
  "pitch": {
    "criteria": {
      "preferred_categories", "minimum_engagement_rate", "minimum_followers",
      "minimum_brand_revenue", "preferred_audience_age"
    },
    "classification_labels": [{ "label", "min_score" }],   // one must have min_score 0
    "targets": { "ideal_followers", "ideal_engagement_rate", "ideal_brand_revenue", "ideal_storefront_sales" },
    "weights": { "category", "engagement", "followers", "brand_revenue", "storefront_sales" }
  },
  "agents": { "router" | "gift_request" | "creator_pitch" | "general_inquiry":
              { "instructions", "constraints" } },
  "models": { "router", "gift_request", "creator_pitch", "general_inquiry", "fallback" }
}
```

Pitch scoring is deterministic: each component scores `min(1, actual / ideal)`, multiplied by its
weight and summed to a 0–100 score. The label is the highest configured threshold the score reaches.
Shipped defaults produce:

| Creator | Gifting | Pitch score | Label |
|---|---|---|---|
| Kathryn Bigelow | ✅ approved | 99.0 | Excellent Match |
| Sofia Coppola | ✅ approved | 100.0 | Excellent Match |
| Martin Scorsese | ✅ approved | 46.0 | Moderate Match |
| Paul Thomas Anderson | ❌ followers + revenue | 58.6 | Moderate Match |
| Peter Bogdanovich | ❌ all criteria | 10.3 | Poor Match |

Every save creates a new version. Events record the version in force, and `/api/config/schema`
exposes the JSON schema for the raw editor.

---

## What is mocked vs real

| Real | Mocked |
|---|---|
| Multi-agent routing, handoffs and conversation memory | Order placement API (`issue_gift` returns a synthetic order id) |
| Every business rule, threshold and guardrail | ShopMy-style tagging API (`place_tag`) |
| The full audit log and per-call cost accounting | Authentication (demo passcode only; no accounts) |
| Admin configuration and creator profiles: versioned, validated, audited | Shipping (nothing physically ships) |
| OpenRouter model calls, retries and fallback | The upstream creator-profile API itself — `get_creator_profile` reads this app's own database, which is editable at `/` |

Swapping the mocks for real integrations means replacing three functions in
`backend/app/tools/registry.py`; nothing else changes.

---

## Repository layout

```
backend/
  app/
    agents/        router_agent, tool_agent (shared loop), general_handler, prompts
    tools/         registry: schemas, validation, guarded executors
    services/      llm_client, config_manager, event_logger, budget, model_catalog,
                   conversation_store, data_store, redactor
    api/           session, creators, chat, config, logs, meta, deps
    data/          creators.json, products.json, brand_config.default.json
    evaluation.py  deterministic business rules (no LLM)
    orchestrator.py, models.py, schemas.py, db.py, settings.py, main.py
  tests/           49 tests including a deterministic fake model
frontend/
  src/views/       CreatorSelect, CreatorChat, AdminConfig, Logs
  src/components/  EventTimeline, CriteriaTable, AdminFields, ui
scripts/           smoke_demo.py, debug_turn.py
render.yaml, Dockerfile, DEPLOYMENT.md
```

---

## Scope boundaries

Prototype, not a production Storefront integration. No real order placement, tagging, shipping or
creator messaging. No production authentication or database requirement. No autonomous
paid-campaign negotiation, and no synchronised brand-side live chat. External actions are mocked but
follow realistic workflows, while the agent architecture, business rules and audit trail are real.

