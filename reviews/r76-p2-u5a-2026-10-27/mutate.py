import subprocess, shutil, sys, pathlib
WT="/opt/data/tmp/u5a-mut/hermes_router"
DB=pathlib.Path(WT,"debug_banner.py"); AX=pathlib.Path(WT,"anchor_exec.py")
db0=DB.read_text(); ax0=AX.read_text()
def run(tag):
    p=subprocess.run(["/opt/hermes/.venv/bin/python3","-m","pytest",
        "tests/banners/test_r76_p2_u5_failure_banner.py","-q"],cwd=WT,capture_output=True,text=True)
    tail=[l for l in p.stdout.strip().splitlines() if l.strip()][-1]
    print("%-42s rc=%d | %s" % (tag,p.returncode,tail))
    DB.write_text(db0); AX.write_text(ax0)

# M1: docstring claim flipped to asserting
DB.write_text(db0.replace("Endpoint REDACTION does NOT apply","Endpoint REDACTION applies"))
run("M1 docstring claims redaction applies")

# M1b: sentence deleted entirely
DB.write_text(db0.replace("Endpoint REDACTION does NOT apply","Endpoint handling is documented elsewhere"))
run("M1b docstring sentence removed")

# M2: break the documented boundary - endpoint starts rendering on this shape
DB.write_text(db0.replace(
 '\"%s %s | %s | %s %s\" % (BANNER_HEAD, _lane_label, trig_s,\n                                           model_s, BANNER_TAIL)',
 '\"%s %s | %s | %s @ %s %s\" % (BANNER_HEAD, _lane_label, trig_s,\n                                           model_s, ep_s, BANNER_TAIL)'))
run("M2 boundary broken: ep rendered")

# M3: reintroduce the OLD overstated claim as a comment at the call site
AX.write_text(ax0.replace(
 "# buys HERE: lane VALIDATION and the lane label come from\n                # format_banner.",
 "# buys HERE: lane validation, host redaction, the _END_LABELS mapping\n                # and the unpriced marker."))
run("M3 anchor_exec comment re-asserts redaction")

# M4: keep the pin's sentence but contradict it one line later
DB.write_text(db0.replace(
 "host redaction must use the normal shape, not this one.",
 "host redaction must use the normal shape, not this one. NB: in practice this\n    shape redacts the endpoint host and drops the query string."))
run("M4 docstring kept + contradicting sentence")
