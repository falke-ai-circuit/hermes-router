"""Guard: a pinned pre-existing red that goes green must be noticed, not absorbed.

``tests/baselines/pre_existing_reds.txt`` is the fleet's durable record of the
reds that already existed before any slice under review.  The whole point of a
baseline is that it is *measured*, so a pin must never outlive the red it
describes: a silently fixed test kept as a permanent excuse hides the fact that
the suite improved and blocks anyone from claiming the win.

This test is deliberately cheap — it does not run the suite.  It reads the two
artefacts pytest already wrote on the LAST run:

    .pytest_cache/v/cache/lastfailed   node ids that failed in the last run
    .pytest_cache/v/cache/nodeids      node ids collected in the last run

So it needs one prior full-suite run to have any evidence; without it the test
skips rather than pretending to know.  When you want to exercise this guard on
its own, disable the cache provider so the full-run evidence is not overwritten:

    python3 -m pytest tests/test_pre_existing_reds_guard.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO_ROOT, "tests", "baselines", "pre_existing_reds.txt")
CACHE_DIR = os.path.join(REPO_ROOT, ".pytest_cache", "v", "cache")

# The full suite collects >3.5k node ids.  Anything far below that is a partial
# run (one file, one directory) and its cache says nothing about the baseline.
FULL_SUITE_FLOOR = 1000


def read_pinned() -> list[str]:
    """Return the pinned node ids, in file order, ignoring comments."""
    ids: list[str] = []
    with open(BASELINE, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    return ids


def last_run_state() -> tuple[dict, list]:
    """Return (lastfailed mapping, collected node ids) from the pytest cache."""
    with open(os.path.join(CACHE_DIR, "lastfailed"), "r", encoding="utf-8") as fh:
        failed = json.load(fh)
    with open(os.path.join(CACHE_DIR, "nodeids"), "r", encoding="utf-8") as fh:
        collected = json.load(fh)
    return failed, collected


def test_pinned_reds_are_still_red() -> None:
    """Every pinned id must have failed in the last full suite run."""
    pinned = read_pinned()
    assert pinned, f"{BASELINE} is empty — refusing to call that a pass"

    if not os.path.exists(os.path.join(CACHE_DIR, "lastfailed")):
        pytest.skip("no pytest cache — run the full suite once to give this guard evidence")
    failed, collected = last_run_state()
    if len(collected) < FULL_SUITE_FLOOR:
        pytest.skip(
            f"last run collected only {len(collected)} node ids — partial run,"
            " the baseline cannot be judged from it"
        )

    unexpectedly_green = sorted(i for i in pinned if i in collected and i not in failed)
    vanished = sorted(i for i in pinned if i not in collected)

    assert not unexpectedly_green, (
        "pinned pre-existing reds PASSED in the last full suite run — the suite got"
        " better and the pin still calls it debt. Remove these lines from"
        f" tests/baselines/pre_existing_reds.txt deliberately:\n  "
        + "\n  ".join(unexpectedly_green)
    )
    assert not vanished, (
        "pinned ids were not collected in the last full run — renamed, deleted or"
        " deselected; a pin that matches nothing is a lie about the code:\n  "
        + "\n  ".join(vanished)
    )
