# Action Driver Catalog

The Action Driver Catalog is the control-plane authority for home action ABI.
Home-control executes actions, Environment observes actions, and Thought Core
chooses actions, but the action meaning and compatibility contract live in the
catalog.

## Authority Split

| Layer | Authority |
| --- | --- |
| control plane | action IDs, aliases, risk, confirmation policy, expected state, catalog version |
| home-control-server | one-shot execution through the configured adapter |
| environment-server | current state projection and action availability |
| thought-core | turn-level decision, approval flow, retry/review loop |
| ops | startup/status checks and catalog hash consistency |

## Update Flow

1. Edit `catalogs/actions/home-actions.json` first.
2. Update affected service projections:
   - `home-assistant-server/config/home-control.yaml` for execution.
   - `environment-state-server/src/environment_state_server/actions.py` for state and aliases.
   - `thought-core` fallback only for critical no-observation phrases.
3. Run catalog consistency tests.
4. Run service unit tests for touched modules.
5. Run `system.ps1 start -DryRun` and `status -ManifestOnly`.
6. Start the stack and confirm `/health`, `/environment/current`, and action preview/execute paths.

Changing an existing `action_id` meaning is a breaking ABI change. Prefer adding
a new action ID and deprecating the old one.
