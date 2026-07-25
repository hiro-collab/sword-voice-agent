# 0003 Agentic Intent And Response Authority

Status: accepted by explicit user correction

## Context

The product is intended to let the AI agent connected to Thought Core understand
natural conversation, inspect available capabilities and context, decide which
API or tool to use, propose bounded parameters, and respond naturally. The local
control side supplements missing implementation details and enforces safety.

During incremental implementation, deterministic tests and fail-closed parsers
were allowed to become the primary semantic route for home actions and
projection effects. Launcher profiles then disabled the action LLM and limited
the provider to visible wording. This made stable tests easier, but changed the
product: fixed phrase tables decided meaning and the AI merely decorated the
result.

That behavior conflicts with the product requirement. Test stability is not
authority to replace the requested product.

## Decision

For ordinary conversation, home actions, and expression or magic actions:

1. The AI agent connected to Thought Core owns semantic intent, capability/tool
   selection, structured argument proposals, and natural response generation.
2. Capability schemas, Environment State, bounded memory, and optional Self
   Mirror observations are reasoning inputs. Targets and positions that depend
   on the live scene must be derived from current observations, not enumerated
   as an unbounded table of fixed coordinates.
3. Deterministic code owns schema validation, allowlists, ranges, policy,
   confirmation, execution, receipts, lifecycle, and cleanup. It may reject or
   request clarification, but it must not silently replace ordinary semantic
   intent with a fixed phrase interpretation.
4. Deterministic no-LLM handling is reserved for Emergency Stop, explicit Reset,
   low-latency reflexes, and clearly labelled degraded or compatibility modes.
5. Provider-unavailable fallback must be visible as degraded. It cannot satisfy
   normal-operation, filming-readiness, product-acceptance, or user-acceptance
   gates.
6. A production change that makes fixed phrase matching or fixed replies the
   default again requires explicit user approval and an ADR update.

## Test Policy

Determinism belongs at the agent boundary, not in the product's language:

- Unit tests may mock an AI proposal and deterministically test validation,
  execution, receipt, replay, collision, timeout, and cleanup.
- Contract tests assert capability IDs, schemas, bounds, provenance, and
  agreement between proposal, execution result, and response.
- Natural response tests assert safety and semantic consistency, not exact
  ordinary wording.
- Live tests use multiple paraphrases and verify that an AI provider was used.
- Exact fixed strings remain valid only for safety errors, privacy-preserving
  failure classes, explicit degraded notices, and emergency controls.
- A reviewer must not request production fixed wording or vocabulary expansion
  merely to make an ordinary-response test deterministic.

## Current Nonconformance

The current fixed home-action detector, fixed projection-effect intent/compiler,
response-only launcher presets, and action-LLM-disabled filming path are
compatibility implementation. Their validators, executors, renderers, receipts,
and cleanup remain reusable, but their semantic routing is not the target
product architecture.

## Consequences

- Stop expanding fixed Japanese phrase lists as the primary fix for missed
  ordinary requests.
- Build one agentic capability-proposal route for home and expression actions.
- Keep emergency and validation paths deterministic and independent of the LLM.
- Report compatibility demonstrations honestly; do not promote them to product
  completion.
