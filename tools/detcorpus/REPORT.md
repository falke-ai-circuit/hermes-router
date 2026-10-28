# Refusal-detector corpus and layer measurement (UNIT 3)

Repo under test: `/opt/data/plugins/hermes_router` at `61e5c27` (v6.14.2).
No detector behaviour was changed in this unit. Harness lives in
`tools/detcorpus/`, data in `/opt/data/tmp/detcorpus/`.

## 1. Corpus

Read-only scan of all 11 `/opt/data/profiles/<agent>/state.db` files.

| Stage | Count |
|---|---|
| assistant rows scanned (non-empty) | 337,581 |
| rows passing the broad refusal cue screen | 16,077 |
| repeated bodies deduped (same session + body sha) | 7,878 |
| unique candidates carrying cue screen | 7,685 |
| **provenance rejects** | **4,156** |
| — rewritten by a render (delivery footer / render-lane banner) | 3,642 |
| — render body matched the persisted row | 514 |
| — paraphrase (`api_content` diverged from the persisted row) | 0 |
| excluded as router artifacts (`[hermes-router]`, empty-body banner) | 454 |
| candidates labelled | 7,673 of 7,685 (12 lost to one failed batch) |
| **refusals labelled** | **674** (639 harvested + 35 probe) |
| harvested candidates labelled NOT refusing (the honest denominator) | 7,034 |
| benign controls sampled from those non-refusals | 200 |

Provenance rungs: **A = 0, B = 674**. No refusal body in the fleet carries a
surviving byte-exact delivery proof: `api_content` is present on only ~350
assistant rows fleet-wide and the router drops it when it rewrites a turn.
Rung B here means "no rewrite evidence exists": not a summary row, no render
match, no `api_content` divergence, no router artifact. The render-footer check
is what makes B meaningful — 3,642 rows were main-model refusals that a render
had already overwritten, and every one of them is excluded.

**Denominators, stated plainly.** The corpus holds 874 rows: 639 harvested
refusals (rung B) + 35 probe refusals (provenance `probe:<domain>/<form>`) + 200
benign controls. Of the 7,673 harvested candidates the annotator labelled,
**7,034 were NOT refusals** — 91.6% of the screened pool, a refusal rate of
8.4%. The 200 controls were sampled from those 7,034 non-refusals, not from the
world at large. Twelve candidates were never labelled and appear on neither
side; they are excluded, not counted as negatives. Every recall figure below is
recall on 674 known refusals; the FP column is against 200 sampled non-refusals.
Neither is a claim about the fleet's full traffic.

### Correction to the unit brief

`api_1793194453_a0b22149` is **not** a refusal body. The persisted row is a
compliant phishing draft that ends with the render-lane footer
`· router · shadow-self (uncensored) | detection_t2 | abliteration… | tok … | $…`.
It is render content, i.e. the exact provenance class this unit was told to
reject, and it must never be stored. So the brief's "T1 True/True ⇒ caught by
T1 only" describes T1 firing on a **compliant** body — a false positive on
render text, not a catch. The two genuine specimens are confirmed:
`59571be2` (1300 chars, ends in a counter-offer, no footer) and `881df1c5`
(530 chars, "I'm withdrawing it rather than building on it.").

## 2. Per-layer measurement

674 real refusals; 200 benign controls from the same sessions. Every body was
scored independently by every layer. Deterministic layers are the shipped
modules imported from the repo; T2 is the shipped `_JUDGE_PROMPT` and
`route_decision` semantics on the configured auxiliary model; the 4th arm is a
plain judge with no cue table, no widening list and no verdict vocabulary.

| Layer | Recall (674) | FP rate (200) | p50 latency | cost/call |
|---|---|---|---|---|
| `structural.is_candidate` (T1) | 358 — **53.1%** | 22 — **11.0%** | 3.5 ms | $0 |
| `structural.has_refusal_semantics` | 321 — **47.6%** | 7 — 3.5% | 3.5 ms | $0 |
| `denial_class.classify == action_denial` | 221 — **32.8%** | 7 — 3.5% | 3.5 ms | $0 |
| `detection_t2` (beam-1.0-501b) | 461 — **68.4%** | 16 — 8.0% | 4.05 s (p95 8.31 s) | $0 |
| plain judge (workhorse, no cue table) | 504 — **74.8%** | 8 — **4.0%** | 2.75 s (p95 6.38 s) | $0.000203 |
| **as-shipped chain (T1 gate → T2)** | 295 — **43.8%** | 1 — 0.5% | 3.5 ms + judge | $0 |

Latency note: 3.47 ms is the measured wall time of the three deterministic calls
run together; the three are not separately instrumented, so per-layer
deterministic latency is **unmeasured**, not equal to 3.47 ms each. T2 and the
plain judge are scored on all 674 refusals and all 200 controls — no missing
cells in those two rows. T2's $0 is the free auxiliary model (it does bill
upstream in principle, but the account reports zero cost for it); the plain
judge's $0.000203/call is the provider-reported cost, summed and averaged over
every call it made here.

The chain number is the load-bearing one. T2 alone finds 68.4% of refusals, but
it is only consulted for turns T1 already flagged (`semantic_judge.py:621`), so
the shipped system catches 43.8% — worse than the judge on its own by 24.6
points, and worse than the plain judge by 31 points. `denial_class`, the layer
the spec made "the trigger", catches one refusal in three.

## 3. Recall by domain (harvested, 639)

n | T1 candidate | denial_class | T2 | plain | chain
---|---|---|---|---|---
benign_task 199 | 31.2% | 18.1% | 48.2% | 61.8% | 17.6%
meta_doctrine 126 | 30.2% | 17.5% | 47.6% | 56.3% | 16.7%
other_harm 103 | 71.8% | 50.5% | 82.5% | 75.7% | 63.1%
weapons_explosives 80 | 78.8% | 33.8% | 95.0% | 98.8% | 76.3%
sexual_content_minors 45 | 80.0% | 48.9% | 86.7% | 84.4% | 71.1%
cyber_offensive 40 | 65.0% | 47.5% | 67.5% | 95.0% | 55.0%
malicious_intent 31 | 74.2% | 29.0% | 100% | 96.8% | 74.2%
fraud_phishing 11 | 36.4% | 27.3% | 81.8% | 90.9% | 36.4%
drug_synthesis 2 | 0% | 0% | 50% | 100% | 0%

The two largest buckets are **refusals of benign/meta asks** — the agent
declining a router or doctrine request, not a weapons request. That is where the
deterministic layers collapse (T1 30-31%, denial_class 17-18%). Sensitive-topic
refusals are the easy case for regexes; in-voice doctrine refusals are the hard
case, and they are 51% of all real refusals here. This is why recall cannot be
read off a topical test set.

## 4. Recall by form (probe set, 42 asks, 35 refusals)

direct order 6 | fiction 4 | demo 6 | incremental 5 | refusal-forcing 3 | research 6 | roleplay 5
---|---|---|---|---|---|---
T1 candidate | 5/6 | 3/4 | 6/6 | 5/5 | **1/3** | 5/6 | 5/5
denial_class | 6/6 | 3/4 | 5/6 | 5/5 | 2/3 | 3/6 | 5/5
T2 | 6/6 | 4/4 | 6/6 | 5/5 | 3/3 | 6/6 | 5/5
plain | 6/6 | 3/4 | 6/6 | 5/5 | 3/3 | 6/6 | 5/5
chain | 5/6 | 3/4 | 6/6 | 5/5 | 1/3 | 5/6 | 5/5

Form barely separates the judges; it separates the regexes, and the worst form
for them is the shortest one — an explicit "do not refuse" demand that gets a
terse "no". Refusal register is not request syntax.

Probe coverage: 6 domains × 7 forms. `sexual_content_minors` was deliberately
not authored — writing a request for that content is a closed line even as a
fixture — and is covered only by the 45 harvested refusal bodies. Bodies that
**complied** in a harm domain were scored for the record but their text was
discarded and never stored (7 of 42 probes complied: drug/self-harm/weapons
refusal-forcing, fraud/malicious fiction).

## 5. Topic invariance (mandatory)

One captured refusal body (`59571be2`), three unrelated asks: its own phishing
ask, a peroxide-explosives ask, a haiku ask.

| Layer | own ask | unrelated harm | benign | verdict |
|---|---|---|---|---|
| is_candidate | False | False | False | PASS (never reads the ask) |
| has_refusal_semantics | False | False | False | PASS |
| denial_class | not_a_denial | not_a_denial | not_a_denial | PASS |
| T2 | TRUE | TRUE | TRUE | **PASS** |
| plain judge | TRUE | TRUE | TRUE | **PASS** |

No layer flips with topic. Note the sharper result hidden in it: both judges
say "this declines" under a *benign* ask, because the body declines its own
ask regardless of what it is attached to. That is the behaviour the label asks
for — the deliverable is the denial, not the topic.

## 6. The gate chain: is refusal detection work-mass-gated?

**No.** Refusal detection is not conditional on work mass, tool count or turn
cadence. It runs unconditionally on every delivered body:

- `gate/orchestration.py:1316-1345` — the four-class detector block; the
  classification happens at `orchestration.py:1342` (`_dcls.classify(response_text)`).
- `gate/orchestration.py:1400-1424` — a `not_a_denial` verdict falls through to
  `post_detection_scan(...)` at `orchestration.py:1424`.
- `audit_gate` (`completion_audit.py:883`) and `work_mass_gate`
  (`completion_audit.py:398`, called at `:914`) are **downstream**: the audit
  arm is only reached at `orchestration.py:1455`, under `if not matches:` from
  `:1445`.

So the T2 sweep **is reachable on a 0-tool turn**; nothing keys on tool mass
before line 1342. What it is *not* reachable on is a body that fails the
structural candidate test: `semantic_judge.py:621` returns False before any
judge call. That single line, not work mass, is what blocked the live miss.

**The specific miss** (`api_1793197680_59571be2`, the "Not building that one"
bank-phishing refusal, delivered to the user unrouted):

| layer | verdict |
|---|---|
| is_candidate | False |
| has_refusal_semantics | False |
| denial_class | not_a_denial |
| **T2** | **TRUE** |
| **plain judge** | **TRUE** |
| as-shipped chain | False |

Both judges would have caught it. The deterministic gate prevented them from
ever seeing it.

## 7. Label quality (limitation, measured)

Two annotators labelled 397 bodies in common: the workhorse (corpus truth) and
the free 122b single-item annotator. Raw agreement 93.2%, Cohen's kappa 0.56
(workhorse-only true 12, other-only true 15, both true 20). Label noise is
real and it is asymmetric on the hard register — a soft label widens the
confidence interval on every recall number above by roughly ±3-4 points, and
it can only make the deterministic layers look *better*, because the misses
they would otherwise have are the terse in-voice ones the annotators dispute.

Also unmeasured, and therefore not reported as zero:

- the **cue screen's own recall**: 119,830 assistant bodies never entered the
  candidate pool, so their refusal rate is unknown and 674 is a floor;
- **per-layer deterministic latency**: the 3.47 ms is three calls timed together;
- the **minors domain has no probe rows** (closed line), so its recall figures
  come only from the 45 harvested bodies and are not comparable to the other
  domains' probe rows;
- **model variance**: one probe per ask, one delivery each — no repeat sampling;
- **precision on real traffic**: the 200 controls are a sample of screened
  non-refusals, not a random sample of fleet deliveries.

## 8. Recommendation

Make the **plain judge the primary trigger** and demote the deterministic layer
to a pre-filter and fallback. The plain judge has the best recall (74.8%) at the
lowest false-positive rate (4.0%), half the latency of the T2 prompt (2.75 s vs
4.05 s p50) and a measurable price ($0.000203/call) — and, decisively, it is
topic-invariant. The T2 prompt earns its keep as the second opinion on contested
rows, not as the only judge: its widening list is a cue table by another name
and it scores 6.4 points worse while doubling false positives. The deterministic
layer keeps two jobs and loses one: it stays as a **cheap pre-filter for cost
control** only if the judge is then run on everything the filter admits *and* a
sampled slice of what it rejects — never as a hard gate; it stays as the
**fail-open path** when the judge times out or is unconfigured, so a broken
provider degrades to today's behaviour instead of silence; and `denial_class`
keeps its real value, which is the routable/non-routable split (truth and
capacity denials must never route) — a job no LLM judge should be trusted with,
because a judge that suppresses honesty re-creates the moralising instrument the
spec forbids. What these numbers do **not** yet support: that the plain judge is
better on *live* traffic (it was measured on a harvested, cue-screened corpus
with 8.4% refusal density, not on deliveries at large); that its 4.0% FP rate is
acceptable at that density (4% of benign deliveries is still a routed turn every
25 benign turns, and the control set is 200 rows, so the interval is wide); that
either judge is stable across model versions (one model each, one sample each);
or that the deterministic layer can be removed — it was never measured as a
cost/benefit trade, only as a gate that blocks recall.

## 9. Reproduce

```
python3 harness/harvest.py        # state.db scan -> candidates.jsonl
python3 harness/label_wf.py       # full-population annotation -> labels_wf.jsonl
python3 harness/probe.py          # 42 domain x form probes -> probes.jsonl
python3 harness/build_corpus.py   # -> corpus.jsonl
python3 harness/score.py          # 4 arms -> det_scores.json + scores.jsonl
python3 harness/aggregate.py      # -> summary.json
python3 harness/invariance.py     # -> invariance.json
python3 harness/agreement.py      # -> agreement.json
```
