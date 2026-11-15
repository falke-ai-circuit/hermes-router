#!/usr/bin/env python3
"""M50-23 live probe: drive the REAL anchored_call retry arm.

Invocation:
  HERMES_HOME=/opt/data/profiles/m3629canary python3 m50_23_probe.py --max-tokens 1200 --tag a

What it does:
  * imports the production plugin from /opt/data/plugins (head of the checked-out repo)
  * builds the same AnchorEndpoint the profile config resolves (nous://anthropic/claude-sonnet-5.5)
  * calls hermes_router.anchor_exec.anchored_call() with the pre-fix input shape:
      reasoning_effort comes from anchor_chain.reasoning_effort (m3629canary = max)
      max_tokens = --max-tokens
  * the retry arm, its log lines and the recovery are produced by the production code,
    never by this script.

No file in the plugin tree is modified.
"""
import argparse
import json
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--max-tokens", type=int, required=True)
ap.add_argument("--model", default="anthropic/claude-sonnet-5.5")
ap.add_argument("--tag", default="x")
ap.add_argument("--ask", default="design")
ap.add_argument("--timeout", type=int, default=600)
args = ap.parse_args()

# provider key for the anchor endpoint (profile .env, parsed, not sourced)
for line in open("/opt/data/profiles/coder/.env", errors="replace"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    k = k.strip()
    if k and k.replace("_", "").isalnum():
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

os.environ.setdefault("HERMES_HOME", "/opt/data/profiles/m3629canary")
sys.path.insert(0, "/opt/data/plugins")

import hermes_router.anchor_exec as ax  # noqa: E402
from hermes_router.anchor_chain import AnchorEndpoint  # noqa: E402

ASKS = {
    "design": (
        "Design a fault-tolerant ingestion pipeline for 40k events/sec. Constraints: exactly-once "
        "delivery, 200ms p99, single region now but must support multi-region failover within one "
        "quarter, no schema registry allowed, existing on-prem Kafka cluster must be reused. "
        "Give the component breakdown, the failure modes and a testing plan."
    ),
    "long": (
        "Write a complete engineering reference for a fault-tolerant, exactly-once ingestion "
        "pipeline at 40k events/sec with 200ms p99, on an existing on-prem Kafka cluster, no "
        "schema registry, multi-region failover inside one quarter. Cover: (1) problem and "
        "constraints, (2) architecture with every component and its contract, (3) implementation "
        "steps with runnable code for the producer, dedup store and rebalance path, (4) failure "
        "modes and edge cases, (5) testing and verification. Be exhaustive; this is a reference "
        "document, not a summary."
    ),
}

endpoint = AnchorEndpoint(
    scheme="nous",
    model=args.model,
    base_url="http://127.0.0.1:8680/v1",
    api_key_env="NOUS_API_KEY",
    role="primary",
)

payload = {
    "messages": [
        {"role": "system", "content": "You are a senior specialist consultant answering one bounded technical question."},
        {"role": "user", "content": ASKS[args.ask]},
    ],
    "max_tokens": args.max_tokens,
}

conf_eff = None
try:
    from hermes_router import config_access as ca
    conf_eff = (ca.sub_block("anchor_chain") or {}).get("reasoning_effort")
except Exception as e:
    conf_eff = "CONFIG_READ_FAIL:%s" % e

out = {
    "tag": args.tag,
    "model": endpoint.model,
    "config_reasoning_effort": conf_eff,
    "payload_max_tokens_in": args.max_tokens,
    "model_output_limit": ax.model_output_limit(endpoint.model),
    "retry_max_tokens_would_be": ax._budget_retry_max_tokens(args.max_tokens, endpoint.model),
    "retry_effort_would_be": ax._budget_retry_effort(conf_eff),
    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
t0 = time.time()
try:
    content, cost, pt, ct = ax.anchored_call(endpoint, payload, timeout=args.timeout)
    out.update(
        content_chars=len(content or ""),
        delivered=bool(str(content or "").strip()),
        cost_usd=cost,
        prompt_tokens=pt,
        completion_tokens=ct,
        content_head=str(content or "")[:160].replace("\n", " "),
    )
except Exception as e:  # noqa: BLE001
    out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
out["secs"] = round(time.time() - t0, 1)
out["ended"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
print(json.dumps(out, indent=1))
with open("/opt/data/tmp/r78u/m50_23_probe_%s.json" % args.tag, "w") as fh:
    json.dump(out, fh, indent=1)
