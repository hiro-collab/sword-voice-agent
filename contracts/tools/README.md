# Tool Contracts

Tool contracts describe the `data` payloads carried by `tool.started` and
`tool.result` events in the thought-core event stream.

## Current Payloads

| Event type | Schema | Notes |
|---|---|---|
| `tool.started` | `tool-call.schema.json` | Announces one boundary call and its `tool_call_id`. |
| `tool.result` | `tool-result.schema.json` | Returns the same `tool_call_id`, status, and raw tool result object. |

## Boundary Rules

- `tool_call_id` correlates one `tool.started` event with one `tool.result`
  event.
- Tool names use dotted `namespace.action` style, such as
  `environment.observe`, `home.preview`, `home.execute`,
  `state_query.feedback`, or `short_memory.write`.
- A tool adapter may return a domain-specific `result` object, but orchestration
  retries and success evaluation remain in thought-core.
