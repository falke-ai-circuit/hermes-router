#!/usr/bin/env python3
"""F4 post-sync verification: hash-compare every profile's deployed
hermes_router runtime files against the canonical repo. Run after any
`uncensored-router-update-all.sh` deployment. Exits non-zero on drift.

Usage: python3 scripts/verify_fleet_sync.py [--roots R1,R2,...]
"""
import hashlib
import os
import sys

SRC = os.environ.get("HERMES_ROUTER_SRC", "/opt/data/plugins/hermes_router")
DEFAULT_PROFILES = ["analyst", "architect", "coder", "conductor", "evol",
                    "operative", "orchestrator", "researcher", "reviewer",
                    "shadow", "valmet"]


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def main():
    profiles = DEFAULT_PROFILES
    if "--roots" in sys.argv:
        roots = sys.argv[sys.argv.index("--roots") + 1].split(",")
    else:
        roots = ["/opt/data/.hermes/profiles/%s/plugins/hermes_router" % p
                 for p in profiles]

    py = sorted(f for f in os.listdir(SRC) if f.endswith(".py"))
    canon = {f: md5(os.path.join(SRC, f)) for f in py}
    canon["plugin.yaml"] = md5(os.path.join(SRC, "plugin.yaml"))

    drift = []
    for root in roots:
        if not os.path.isdir(root):
            drift.append((root, "DIR MISSING"))
            continue
        for f, h in canon.items():
            path = os.path.join(root, f)
            if not os.path.isfile(path):
                drift.append((path, "FILE MISSING"))
            elif md5(path) != h:
                drift.append((path, "HASH DRIFT"))
        strays = [f for f in os.listdir(root)
                  if f.endswith(".bak") or ".bak-" in f]
        for s in strays:
            drift.append((os.path.join(root, s), "STRAY .bak ARTIFACT"))

    if drift:
        for p, why in drift:
            print("DRIFT: %s %s" % (why, p), file=sys.stderr)
        print("FAIL: %d drift items across %d roots" % (len(drift), len(roots)),
              file=sys.stderr)
        sys.exit(1)
    print("OK: %d roots x %d runtime files hash-verified" % (len(roots), len(canon)))


if __name__ == "__main__":
    main()
