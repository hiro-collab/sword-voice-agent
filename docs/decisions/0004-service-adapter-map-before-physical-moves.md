# 0004 Service And Adapter Map Before Physical Moves

Status: accepted

## Context

The workspace has several large Git-managed sibling modules. Moving all code
into a final directory tree at once would break launch scripts, local assets,
and reviewability. At the same time, leaving the system unnamed makes it hard
to know whether new work belongs to thought, reflex, environment, action,
expression, memory, adapter, runtime, or ops.

## Decision

Use `docs/service-boundary-map.md` and `ops/manifests/services/*.json` as the
current bridge between physical modules and logical services. Contracts define
the public boundary first; manifests name the running process identity; physical
file moves happen later and only with tests and lifecycle verification.

Large sibling modules remain implementation modules for now:

| Physical module | Logical role |
|---|---|
| `mediapipe-sword-sign` | `reflex-core` v0 plus MediaPipe adapter |
| `environment-state-server` | `environment-server` v0 |
| `home-assistant-server` | `home-control-server` v0 plus Home Assistant adapter |
| `aituber-kit` | `aituber-ui` app and expression adapter target |
| `tts-service` | expression speech component |

## Consequences

- A new logical service does not require an immediate new repository.
- A physical move is valid only after its contracts, manifests, start/status/stop
  behavior, and tests are updated together.
- Adapters are allowed to live inside the current service module until multiple
  services need the same adapter.
- Generated caches, `__pycache__`, and local `_tmp` files are not design
  artifacts and can be removed during cleanup.

