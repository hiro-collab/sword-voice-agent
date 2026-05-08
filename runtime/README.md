# Runtime

`runtime/` is the future root for generated logs, state, PID files, caches, and
diagnostics. Do not commit generated runtime data here.

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
