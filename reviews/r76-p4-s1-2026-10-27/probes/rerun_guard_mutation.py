import re,subprocess,os,shutil,sys
W='/opt/data/profiles/reviewer/cache/scratch/r76s1/wt'
PKG=os.path.join(W,'hermes_router')
CFG=os.path.join(PKG,'.importlinter')
orig=open(CFG).read()
entries=[
 "hermes_router.core.canonical -> hermes_router.persona_card",
 "hermes_router.core.usage_ledger -> hermes_router.anchor_chain",
 "hermes_router.core.usage_ledger -> hermes_router.provider_prices",
 "hermes_router.core.routing_caps -> hermes_router.anchor_chain",
 "hermes_router.core.routing_caps -> hermes_router.debug_banner",
 "hermes_router.core.decisions -> hermes_router.suggestions",
 "hermes_router.core.routing_caps -> hermes_router",
 "hermes_router.core.telemetry -> hermes_router",
 "hermes_router.core.config_access -> hermes_cli",
]
def run():
    p=subprocess.run([sys.executable,'-m','importlinter.cli','lint-imports'],cwd=PKG,env={**os.environ,'PYTHONPATH':W},capture_output=True,text=True)
    out=p.stdout+p.stderr
    tail=[l for l in out.splitlines() if 'KEPT' in l or 'BROKEN' in l or 'Contracts:' in l or 'broken' in l]
    return p.returncode, tail
try:
    for e in entries:
        assert e in orig, e
        open(CFG,'w').write(orig.replace('    '+e+'\n','',1))
        rc,tail=run()
        print('DROP_IGNORE:',e,'| rc=',rc)
        for l in tail: print('   ',l.strip())
finally:
    open(CFG,'w').write(orig)
print('restored rc=',run()[0])
