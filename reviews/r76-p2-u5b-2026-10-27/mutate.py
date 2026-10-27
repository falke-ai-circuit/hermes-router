"""Mutation harness for the U5b pins.

Each mutation copies the reviewed tree, applies ONE textual/structural edit, and runs
tests/banners/test_r76_p2_u5_failure_banner.py from inside the copy. A pin that
survives both the true and the false reading is not pinning the claim.
"""
import pathlib, shutil, subprocess, sys

SRC = pathlib.Path("/opt/data/tmp/u5b/child/hermes_router")
WORK = pathlib.Path("/opt/data/tmp/u5b/mut")
PY = "/opt/hermes/.venv/bin/python3"
PIN = "tests/banners/test_r76_p2_u5_failure_banner.py"

MUTATIONS = {}


def mut(name):
    def deco(fn):
        MUTATIONS[name] = fn
        return fn
    return deco


@mut("M1_restore_false_sentence_in_docstring")
def m1(root):
    p = root / "debug_banner.py"
    s = p.read_text()
    old = "Endpoint REDACTION does NOT reach the rendered line"
    assert old in s
    p.write_text(s.replace(old, "Endpoint REDACTION does NOT apply: metadata_only "
                               "returns before the endpoint is ever read", 1))


@mut("M2_code_genuinely_skips_reading_endpoint")
def m2(root):
    """Make the FALSE claim TRUE: move the ep_s build/redaction BELOW the branch."""
    p = root / "debug_banner.py"
    lines = p.read_text().splitlines(keepends=True)
    # 1-based 184..187 is the ep_s block in the child tree
    block = lines[183:187]
    assert block[0].strip().startswith("ep_s = str(endpoint"), block[0]
    rest = lines[:183] + lines[187:]
    idx = next(i for i, l in enumerate(rest) if l.strip() == "if metadata_only:")
    # insert AFTER the branch body (the return spans two physical lines)
    insert_at = idx + 3
    rest = rest[:insert_at] + block + rest[insert_at:]
    p.write_text("".join(rest))


@mut("M3_reinstate_false_claim_in_call_site_comment")
def m3(root):
    p = root / "anchor_exec.py"
    s = p.read_text()
    old = ("# `ep_s` and the usage cells are USED, so an endpoint passed\n"
           "                # here would be read and host-redacted and then discarded —")
    assert old in s, "anchor comment anchor text not found"
    new = ("# metadata_only returns before the endpoint and usage args are read, so\n"
           "                # an endpoint passed here is inert.")
    p.write_text(s.replace(old, new, 1))


@mut("M4_reinstate_false_claim_wrapped_in_test_own_docstring")
def m4(root):
    p = root / PIN
    s = p.read_text()
    old = "metadata_only returns before `ep_s` and the usage cells are USED — the endpoint is\nread and host-redacted"
    assert old in s, "test module docstring anchor not found"
    new = ("metadata_only returns before the endpoint\nis ever read, and the usage args are\n"
           "never touched")
    p.write_text(s.replace(old, new, 1))


@mut("M5_reinstate_false_claim_as_docstring_only_in_anchor_exec")
def m5(root):
    p = root / "anchor_exec.py"
    s = p.read_text()
    old = "# this shape renders no endpoint cell — and no usage cell"
    assert old in s
    p.write_text(s.replace(old, "# this shape renders no endpoint cell: metadata_only returns\n"
                               "                # before the endpoint/usage args are read — and no usage cell", 1))


results = []
for name, fn in MUTATIONS.items():
    root = WORK / name / "hermes_router"
    if root.parent.exists():
        shutil.rmtree(root.parent)
    root.parent.mkdir(parents=True)
    shutil.copytree(SRC, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    fn(root)
    r = subprocess.run([PY, "-m", "pytest", "-q", PIN, "-p", "no:randomly",
                        "-p", "no:cacheprovider"],
                       cwd=root, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin",
                            "PYTHONPATH": "/opt/hermes:/opt/data/.local/lib/python3.13/site-packages",
                            "HOME": "/tmp"})
    tail = [l for l in r.stdout.strip().splitlines() if l.strip()][-1] if r.stdout else ""
    failed = [l.split("::")[-1].split(" ")[0] for l in r.stdout.splitlines()
              if l.startswith("FAILED")]
    results.append((name, tail, failed))
    print(f"{name}: {tail}")
    for f in failed:
        print(f"    caught-by {f}")

print("\n--- summary ---")
for n, t, f in results:
    print(f"{n}: {'CAUGHT' if f else 'SURVIVED'} ({t}) :: {f}")
