# zz_lane_hazard — tests that must run LAST

Directory name sorts after every other test package on purpose. pytest collects in
path order, so anything here runs after the rest of the suite.

Why: the R79 banner-merge pins drive the delivery edge, which keys module-level
state by session id. Proven behaviour (2026-10-30):

  - file alone                 -> 4 passed
  - file + rider16/18 + rider10-> 36 passed, 2 pre-existing egress errors
  - full suite MINUS this file  -> exactly baseline (7 failed, 2689 passed, 2 errors)
  - full suite WITH it          -> 7 beyond baseline (rider16 x5, rider18 x2)

So a third participant, somewhere between this file and tests/routing in suite
order, turns its residue into rider16/rider18 failures. The poisoner is NOT yet
named: dict-clearing guards over the ten session-keyed stores did not fix it, and
the pair run reproduces nothing. Moving the file last removes the interaction
without pretending it is solved.

UNRESOLVED (tracked, not closed): identify the poisoner and delete this directory.
An order-dependent test that only stays green by being last is a known defect, not
a clean fixture. Do not copy this pattern for new tests - isolate the store instead.
