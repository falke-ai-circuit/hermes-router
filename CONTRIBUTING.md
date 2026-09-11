# Contributing to hermes-router

## Ground rules

1. **Fail-open is the doctrine.** Every new code path must degrade to
   "deliver the ordinary turn" on failure. Wrap risk boundaries, never
   raise into the turn pipeline.
2. **Routed turns never re-trigger routing.** If your feature emits
   text into a turn, register its marker in the sentinel firewall
   (`_frame_sentinel_check`). New lanes MUST register there.
3. **Config knobs are read live, never cached hard.** Add new knobs to
   the `hermes_router:` section readers in `config_access.py`, with a
   default, a clamp, and a fail-open fallback.
4. **Tests fully mocked.** No test may make a real provider call. The
   suite runs hermetic (no keys in env).
5. **Sync the trees.** Changes to plugin `.py` files must be committed,
   pushed, and deployed to all profiles before any live verification.

## Dev loop

```
python -m pytest tests/ -q          # full suite; must be 100% green
bash ../uncensored-router-update-all.sh   # fleet sync (host-specific)
```

Behavioral verification uses `benchmarks/behavioral_battery.py` against
a live canary profile — see `benchmarks/METHODOLOGY.md` for the oracle
pitfalls before trusting a result.

## PR checklist

- [ ] Suite green (zero failures — pre-existing reds are not a thing)
- [ ] CHANGELOG.md entry for the next version (CI checks this)
- [ ] New knobs documented in `docs/operator-guide.md`
- [ ] New lane outputs registered in the sentinel firewall
- [ ] No new top-level imports of `hermes_router` inside gateway
      code paths (the gateway loads the plugin under the
      `hermes_plugins.*` alias — use relative imports)
