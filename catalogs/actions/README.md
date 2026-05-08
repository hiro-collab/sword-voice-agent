# Action Driver Catalog

This directory is the control-plane authority for home action driver metadata.

The catalog is the ABI for home actions. Services may execute, observe, or
choose actions, but they should not invent independent meanings for an
`action_id`.

## Files

- `home-actions.json`: canonical action driver catalog.
- `home-actions.schema.json`: JSON Schema for the catalog shape.

## Update Flow

1. Propose the catalog change first.
2. Review the affected fields by owner:
   - home-control reviews `execution`, confirmation, and risk.
   - environment reviews `observation`, state, and freshness expectations.
   - thought-core reviews aliases, intent examples, and response hints.
   - control-plane reviews schema, versioning, and compatibility.
3. Update the service implementations that project or consume the catalog.
4. Run catalog consistency tests before runtime tests.
5. Run a dry-run stack start and then a live status check.
6. Only then treat the new action ABI as available to users.

`stack.lock.json` is intentionally not maintained by hand. When needed, ops
should generate it as a snapshot of a verified system cell.
