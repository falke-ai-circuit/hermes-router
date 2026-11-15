# Banner specification

The authoritative standard for every router banner. Until this file existed the
grammar lived only in `features/banners/turn_record.py::_render_cells`, which meant
compliance could only be judged by reading a function — an agent asked to confirm
compliance correctly answered that it was "a field-presence judgement, not a verified
gate pass". This document is that gate's referent.

## 1. The cell grammar

One line per lane, rendered by `_render_cells` — the ONLY producer of lane text.
Nothing else may compose banner text.

```
<head> <label> | <trigger> | ×<n> | <model> | tok <in>/<out> | $<cost> | <extras>
```

Worked example (frontier):

```
· router · higher-self (frontier) | pre-check | anthropic/claude-sonnet-5.5 @ 127.0.0.1:8680 | tok 1465/16603 | $0.1690
```

Worked example (decision):

```
· router · impulse (decision) | 1 hold | jev @ typesafe | tok 2300/102 | $0.00010 | row 96
```

### Cell rules

| cell | rule |
|------|------|
| head | exactly one `· router ·` per body. A second head is a violation. |
| label | registered label for the lane — see §2. Never invented at call site. |
| trigger | the reason the lane fired (`consult`, `pre-check`, `1 hold`, …). |
| `×n` | repetition count, present only when the lane fired more than once. |
| model | `<model> @ <host>` as dispatched, never a configured alias or a guess. |
| tokens | `tok <in>/<out>` — SLASH form, no thousands separators, no arrow. |
| cost | `$<d>` with exactly four decimals; `*` suffix when the price is estimated. |
| extras | lane-specific (`row <id>`, `retries`, etc.). Never spend — see §3. |

## 2. Registered labels

| lane | label |
|------|-------|
| decision | `impulse (decision)` |
| frontier | `higher-self (frontier)` |
| uncensored | `shadow-self (uncensored)` |

## 3. Spend is mandatory and appears exactly once

A lane that fired billed a provider. The spend therefore appears in the lane line
itself, at every level — `_render_cells` emits `tok` and `$` unconditionally.

Consequences, both of them learned the hard way:

- You may NOT add a second spend row at another level. It duplicates the lane line
  and introduces a second dialect of the same fact. (A `tok 2,471→29,696 · $0.3019 ·
  frontier` row was shipped on the false premise that tokens only rendered at level
  3, and reverted the same day.)
- You may NOT render spend when the record carries no token counts. `tok 0→0` is a
  field outrunning the thing it names. Omit the row; never invent the number.

## 4. Levels

`debug_banner: 0..3` — 0 off, 1 default, 2 adds task/session/gate detail, 3 adds a
`full:` diagnostic row. Levels add detail; they never add or remove the lane line's
own `tok`/`$` cells. No profile configures a level, so the fleet renders at 1.

## 5. Delivery invariants

- The banner is a DELIVERY-SURFACE artefact. It is appended to the body the reader
  receives and is never written into the persisted canonical turn.
- `park` → `consume` is best-effort. When the park misses, the stashed segment is
  promoted at the delivery chokepoint (`anchor_banner_promoted`), so a lane that
  fired always reaches the final response.
- Promotion is one-shot with a short TTL: a banner from an earlier turn is
  misinformation, and a second compose must never stack a second banner.
- Only a lane that actually fired and was billed may emit a banner. Silence is
  correct for a turn no lane touched.

## 6. How compliance is verified

Never by inspection, and never from `state.db` (the persisted row does not carry the
banner). Two mechanisms, both required:

1. **Pins** — `tests/higher_self/test_banner_template_compliance.py` asserts, for all
   three lanes: canonical `tok N/M` and `$0.0000` cells, identical cell SHAPE across
   lanes (only the label differs), one head per body, no arrow/thousands form, and
   that no lane line can render without its spend.
2. **Live probe** — `/opt/data/tmp/probe_delivered_banner.py <agent> <port>` fires a
   declared on-demand consult and reads the DELIVERED body. Score the unescaped text:
   the API body carries `\u00b7` for `·`, so a literal search for the head reports
   zero heads on a body that plainly has one.

Pass = banner in the delivered body, in canonical form, one head, no off-standard
separators, and a billed spend figure that matches the consult event.
