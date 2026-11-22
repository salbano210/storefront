# ShopMy Multi-Agent Creator Relationship Demo
## Updated Product & Technical Specification

## 1. Project Overview

Build an interactive, ShopMy-inspired demo that simulates conversations between creators and brands, powered by a multi-agent AI architecture.

The demo showcases how AI agents can automate brand-side creator relationship management.

Core capabilities:

1. Process and evaluate creator gifting requests.
2. Evaluate inbound creator partnership pitches.
3. Execute actions based on brand-configured criteria.
4. Maintain an auditable record of conversations, agent decisions, tool calls, and agent transfers.
5. Allow brand administrators to configure agent behavior through an editable admin interface.

This is a prototype, not a production ShopMy integration.

External APIs, order placement, and tagging functionality will be mocked, but the architecture should make it straightforward to replace these integrations with real APIs.

The demo brand will be called **Demo Brand**.

## 2. Application Interfaces

The application should have four primary views.

### A. Creator Selection

Before entering the chat interface, users should select which creator they are simulating.

Requirements:
- Display a selection of predefined demo creator profiles.
- Each creator should have a profile card.
- Display creator name, profile image, follower count, engagement rate, and creator category.
- Selecting a creator should establish their identity for the subsequent conversation.
- The selected creator's ID should be passed to the backend and associated with all subsequent messages and tool calls.

Include at least three creators with meaningfully different profiles and performance metrics.

The creator's verified profile information must be retrieved from backend data, not supplied by the frontend as trusted input.

### B. Creator Chat Interface

A ShopMy-branded, creator-facing chat interface simulating what creators experience when communicating with a brand.

Requirements:
- Display a ShopMy-inspired chat interface.
- Simulate an active conversation with Demo Brand.
- Allow the selected creator to send free-form messages.
- Display agent responses conversationally.
- Support multi-turn conversations.
- Preserve conversation history.
- Support different types of interactions:
  - Product gifting requests.
  - Partnership pitches.
  - Questions about potential collaborations.
  - Follow-up messages.
- Display processing states and conversational confirmations.
- Display appropriate approval or rejection messages.

The creator must not see internal agent reasoning, tool calls, backend configuration, or system prompts.

The creator should receive a natural response reflecting the actual outcome of the agent's execution.

### C. Brand Admin / Agent Configuration

Create a brand-side administration interface for configuring agent behavior.

This interface should allow a brand administrator to modify the configuration that governs the agents.

Configuration should be stored in a JSON file in the backend repository.

The admin interface should:
- Load the current configuration from the backend.
- Display editable configuration fields.
- Allow the administrator to modify gifting approval criteria.
- Allow the administrator to modify creator pitch qualification criteria.
- Allow the administrator to manage available products and eligible SKUs.
- Allow the administrator to manage available tags.
- Allow the administrator to edit agent instructions and constraints.
- Validate configuration before saving.
- Persist changes to the backend JSON configuration.
- Ensure subsequent agent executions use the latest saved configuration.

Avoid requiring code changes to modify business criteria.

The interface should expose understandable configuration fields rather than requiring the administrator to edit raw JSON.

An optional raw JSON editor or JSON preview would be useful for debugging.

Suggested configuration structure:

```json
{
  "brand": {
    "name": "Demo Brand",
    "description": "A premium skincare brand"
  },
  "gifting": {
    "criteria": {
      "minimum_followers": 10000,
      "minimum_engagement_rate": 0.03,
      "minimum_brand_revenue": 500
    },
    "eligible_products": [
      {
        "name": "Barrier Repair Serum",
        "sku": "GT-SERUM-001"
      }
    ]
  },
  "pitch": {
    "criteria": {
      "preferred_categories": [
        "beauty",
        "skincare"
      ],
      "minimum_engagement_rate": 0.025,
      "preferred_audience_age": "22-35"
    },
    "classification_labels": [
      "Excellent Match",
      "Good Match",
      "Moderate Match",
      "Poor Match"
    ]
  }
}
```

This is an illustrative starting structure, not a complete schema. The implementation should use a validated configuration schema.

### D. Agent Logs / Observability Interface

Create a dedicated logs page showing the internal execution of the multi-agent system.

This is a critical component of the demo.

The logs interface should display:
- Full conversation transcripts.
- Incoming creator messages.
- Routing agent decisions.
- Agent handoffs and transfers.
- Agent responses.
- Tool calls and arguments.
- Mock tool API responses.
- Criteria evaluations.
- Final outcomes.

Each event should include:
- Timestamp.
- Conversation ID.
- Creator ID.
- Agent name.
- Event type.
- Relevant input/output payload.
- Success/failure status.

Logs should be filterable by:
- Creator.
- Conversation.
- Agent.
- Event type.

Example execution flow:

1. Creator sends gifting request.
2. Routing agent identifies `gift_request`.
3. Routing agent transfers control to `gift_request_agent`.
4. Gifting agent calls `check_creator_profile`.
5. Gifting agent evaluates approval criteria.
6. Gifting agent calls `issue_gift`.
7. Mock API returns success.
8. Gifting agent confirms the order with the creator.

The interface should make this sequence easy to follow visually.

Logs must be generated from actual backend execution events, not separately fabricated to simulate agent activity.

## 3. Multi-Agent Architecture

Implement a routing agent and two specialized resolution agents.

Use OpenRouter as the LLM provider.

### LLM Provider Requirements

- All LLM requests should route through OpenRouter.
- API keys must be stored in backend environment variables, never exposed to the frontend.
- Model selection should be configurable.
- Use structured outputs wherever possible.
- Keep model/provider configuration separate from individual agent instructions.

Suggested environment variables:

```env
OPENROUTER_API_KEY=
OPENROUTER_MODEL=
```

The implementation should support changing models without rewriting agent logic.

### A. Routing Agent

The routing agent classifies incoming creator messages and delegates them to specialized agents.

Supported intents:

- `gift_request`
- `creator_pitch`
- `general_inquiry`

Responsibilities:
- Analyze incoming messages.
- Determine the most likely intent.
- Transfer control to the appropriate specialized agent.
- Maintain conversation context across handoffs.
- Ask clarifying questions when intent is ambiguous.
- Avoid executing specialized business actions itself.

The routing agent should return structured intent classifications.

### B. Gift Request Agent

**Goal:** Evaluate creator gifting requests against brand-configured eligibility criteria and automatically execute the `issue_gift` tool when approved.

Workflow:

1. Understand the creator's gifting request.
2. Identify the requested product.
3. Call `check_creator_profile` to retrieve verified creator information.
4. Retrieve the creator's shipping address from the verified profile.
5. Compare verified creator characteristics and metrics against brand gifting criteria.
6. Determine whether the request qualifies.
7. If required information is missing, ask the creator for clarification.
8. If approved, call `issue_gift`.
9. If rejected, explain the outcome politely.
10. Confirm successful gifting only after the mock API returns success.

Important requirements:
- Never trust creator-provided claims about their own follower count, engagement, sales, or other profile characteristics when verified profile information is available.
- Only use SKUs present in the brand's eligible product catalog.
- Do not issue gifts if any mandatory eligibility criteria are unmet.
- Do not issue duplicate gifts for the same request.
- Do not fabricate successful order placement.
- Preserve the evaluation and decision in the logs.

#### Gifting approval criteria

Configurable through the admin interface:

- Minimum follower count.
- Minimum engagement rate.
- Relevant creator category.
- Minimum historical brand-attributed revenue.
- Eligible products.
- Additional brand-specific requirements.

The agent should evaluate criteria using verified profile information.

#### Tool: `check_creator_profile`

Purpose: Retrieve authoritative creator information.

Input:

```json
{
  "creator_id": "creator_001"
}
```

Mock response:
- Creator name.
- Creator ID.
- Follower count.
- Engagement rate.
- Creator categories.
- Audience demographics.
- Historical brand-attributed revenue.
- Previous gifting history.
- Shipping address.

#### Tool: `issue_gift`

Purpose: Place a gift order for an approved creator.

Input must contain exactly three fields:

```json
{
  "creator_name": "Jessica Miller",
  "sku": "GT-SERUM-001",
  "shipping_address": "123 Example Street, Boston, MA 02110"
}
```

The tool should simulate an external order-placement API.

Mock response:
- Success/failure.
- Order ID.
- Confirmation message.

The gifting agent has authority to approve qualifying requests and execute this tool automatically. No additional brand approval step is required in the demo.

### C. Creator Pitch Agent

**Goal:** Evaluate inbound creator partnership pitches against a brand-configured ideal creator profile and automatically apply an appropriate ShopMy tag.

Workflow:

1. Understand the creator's pitch.
2. Call `check_creator_profile` to retrieve verified creator information.
3. Review the creator's stated collaboration interests.
4. Compare verified characteristics and metrics against the brand's configured pitch profile.
5. Evaluate alignment with brand positioning.
6. Determine an appropriate match classification.
7. Call `place_tag` to apply the appropriate tag.
8. Acknowledge receipt of the pitch to the creator.

The agent must distinguish between creator-provided claims and verified profile information.

#### Pitch evaluation criteria

Configurable through the admin interface:

- Creator category/niche.
- Audience demographics.
- Follower count.
- Engagement rate.
- Historical ShopMy sales.
- Historical brand-attributed revenue.
- Content style.
- Alignment with brand positioning.
- Stated collaboration interests.

The agent should produce a structured evaluation containing:

- Overall match classification.
- Key reasons supporting the classification.
- Strengths.
- Potential concerns.

Suggested classifications:

- `excellent_match`
- `good_match`
- `moderate_match`
- `poor_match`

The classification should be determined using the brand's configured criteria.

#### Tool: `place_tag`

Purpose: Apply a tag to a creator's ShopMy profile or brand relationship record.

Input:

```json
{
  "creator_id": "creator_001",
  "tag": "Excellent Match"
}
```

Mock response:
- Success/failure.
- Creator ID.
- Tag applied.

The agent should automatically apply its final classification. No additional brand approval step is required in the demo.

### D. General Inquiry Handling

The routing agent should also support basic general inquiries.

For example:
- Questions about the brand.
- Questions about available products.
- Questions about the gifting process.
- Questions about partnership opportunities.

These can be handled by a lightweight general response handler rather than introducing another specialized agent.

The general handler must not approve gifts, make partnership commitments, or execute business actions.

## 4. Backend Data and Mock Integrations

Use mock backend data to simulate ShopMy APIs.

### Creator profiles

Create at least three predefined profiles with meaningfully different characteristics.

Each profile should contain:
- Creator ID.
- Name.
- Profile image.
- Follower count.
- Engagement rate.
- Creator categories.
- Audience demographics.
- Historical ShopMy sales.
- Historical brand-attributed revenue.
- Previous gifting history.
- Shipping address.

Include profiles that produce different agent outcomes.

Examples:
- High-performing creator who qualifies for gifting.
- Creator who fails gifting requirements.
- Creator who makes exaggerated claims that conflict with verified profile information.
- Creator with strong pitch alignment.

### Product catalog

Include multiple eligible gifting products.

Each product should have:
- Product name.
- SKU.
- Description.
- Eligibility status.

### Brand configuration

Store the configuration in a backend JSON file.

The admin interface should update this configuration through backend endpoints.

Important: configuration changes must persist and be used by subsequent agent executions.

## 5. Logging and Execution Events

All agent activity must generate structured events.

Suggested event types:

- `message_received`
- `intent_classified`
- `agent_handoff`
- `profile_lookup`
- `criteria_evaluated`
- `tool_call_started`
- `tool_call_completed`
- `tool_call_failed`
- `tag_applied`
- `gift_issued`
- `message_sent`

Logs should capture actual execution details, including tool inputs and outputs.

Example:

`check_creator_profile`

```json
{
  "creator_id": "creator_001"
}
```

`issue_gift`

```json
{
  "creator_name": "Jessica Miller",
  "sku": "GT-SERUM-001",
  "shipping_address": "123 Example Street, Boston, MA 02110"
}
```

`place_tag`

```json
{
  "creator_id": "creator_001",
  "tag": "Excellent Match"
}
```

Persist events in a lightweight database or backend storage solution suitable for the demo.

The frontend should retrieve logs from the backend rather than maintaining a separate mock event history.

## 6. Technical Architecture

The coding agent may choose the technology stack, prioritizing:
- Fast implementation.
- Clean separation of frontend and backend.
- Ease of local development.
- Reliable multi-agent execution.
- Easy demonstration and debugging.

Required architectural principles:

1. Separate frontend and backend responsibilities.
2. Route all LLM requests through OpenRouter.
3. Keep API keys server-side.
4. Keep brand configuration separate from agent code.
5. Implement tools as discrete backend functions.
6. Separate routing logic from specialized agent logic.
7. Use structured outputs for intent classification and evaluations.
8. Maintain conversation history across messages.
9. Maintain creator ID and conversation context across agent handoffs.
10. Return tool execution results to the calling agent.
11. Generate logs from actual backend execution.
12. Prevent duplicate execution of side-effecting tools when requests are retried.
13. Validate all tool arguments server-side.
14. Ensure configuration updates are validated before persistence.

Suggested logical backend organization:

```text
backend/
  agents/
    router_agent
    gift_request_agent
    creator_pitch_agent
    general_handler

  tools/
    check_creator_profile
    issue_gift
    place_tag

  config/
    brand_config.json

  services/
    llm_provider
    conversation_manager
    agent_orchestrator
    event_logger
    configuration_manager

  data/
    creator_profiles
    product_catalog

  api/
    chat
    creators
    configuration
    logs
```

The frontend should contain separate views for:
- Creator selection.
- Creator chat.
- Brand configuration.
- Agent logs.

The implementation should prioritize functional end-to-end workflows over unnecessary architectural complexity.

## 7. Demo Scenarios and Acceptance Criteria

### Scenario 1: Successful gifting request

- Select a qualifying creator.
- Request an eligible product.
- Agent retrieves verified profile information.
- Agent evaluates all configured gifting criteria.
- Agent approves the request.
- Agent calls `issue_gift`.
- Mock API returns success.
- Creator receives confirmation.
- Logs show the complete execution sequence.

### Scenario 2: Rejected gifting request

- Select a creator who fails one or more requirements.
- Request a product.
- Agent retrieves verified profile information.
- Agent identifies the unmet criteria.
- Agent declines the request.
- No gift order is placed.
- Logs explain the decision.

### Scenario 3: Successful creator pitch

- Select a creator.
- Send a partnership pitch.
- Agent retrieves verified profile information.
- Agent evaluates the pitch against brand criteria.
- Agent assigns a match classification.
- Agent calls `place_tag`.
- Creator receives acknowledgment.
- Logs show evaluation and tag application.

### Scenario 4: Creator attempts to manipulate eligibility

- Select a creator with relatively weak verified metrics.
- Creator claims significantly higher follower counts, engagement, or sales.
- Agent retrieves the authoritative profile.
- Agent bases its decision on verified information.
- Agent does not approve an otherwise ineligible request based on unverified claims.

### Scenario 5: Multi-turn conversation

- Creator sends an incomplete request.
- Agent asks a clarifying question.
- Creator supplies missing information.
- Agent resumes the workflow without losing context.
- Agent executes the appropriate action once all requirements are met.

### Scenario 6: Configuration changes affect agent behavior

- Open the brand admin interface.
- Change the minimum follower requirement for gifting.
- Save the configuration.
- Start a new gifting request.
- Agent uses the updated requirement.
- Logs demonstrate the updated evaluation.

### Scenario 7: Different creator profiles produce different outcomes

- Select multiple creators.
- Submit similar gifting requests or pitches.
- Verify that the agents use the selected creator's actual profile.
- Verify that different verified characteristics produce appropriately different decisions.

## 8. Scope Boundaries

For the initial demo:

- No real ShopMy API integrations.
- No real shipping or order placement.
- No real creator messaging.
- No production authentication requirements.
- No production database requirement.
- No autonomous paid campaign negotiations.
- No separate brand-side live chat interface.

The brand-side admin interface and logs page are included, but a fully synchronized second conversational interface is explicitly out of scope for this iteration.

All external actions should be mocked but follow realistic workflows.

## 9. Definition of Done

The demo is complete when:

- A user can select a creator.
- The creator can initiate a multi-turn conversation with Demo Brand.
- The routing agent correctly delegates messages.
- The gifting agent can evaluate and execute qualifying gift requests.
- The gifting agent can reject ineligible requests without issuing gifts.
- The pitch agent can evaluate creators and apply tags.
- Verified profile data is used for decisions.
- Brand administrators can edit and persist configuration through the UI.
- Configuration changes affect subsequent agent executions.
- The logs page displays actual conversations, handoffs, decisions, and tool calls.
- The entire experience works end-to-end using mock integrations and OpenRouter.

**Primary objective:** Demonstrate a configurable, observable multi-agent system that can independently manage routine creator-brand interactions while maintaining reliable business rules and an auditable execution history.