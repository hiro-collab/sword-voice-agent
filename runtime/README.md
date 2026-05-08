# Runtime

`runtime/` is the future root for generated logs, state, PID files, caches, and
diagnostics. Do not commit generated runtime data here.

Runtime covers M1 module state, M3 event journals, process registries, caches,
and diagnostics. It does not hold committed M4 memory or M6 secrets.

The current default compatibility path is still:

```text
.cache/home-control-stack
```

Advanced runs can override the stack state directory with:

```text
HOME_CONTROL_STACK_STATE_DIR
-StackStateDir
```

See `docs/runtime-layout.md` for the current mapping and migration rules.
