# Phase 3 open decisions

A criterion records something that must be true and whether it is. This records something nobody has decided. A gap means unfinished work and a deferral means a postponement with a trigger; neither fits a question whose answer is a policy choice, and a question like that has two fates if it is not written down. It is settled by accident by whoever first trips over it, or silently by whoever happens to be implementing near it.

## The one this phase answered

Decision 1, on whether a registry scan result may block a publish, was Phase 1's and landed here: the only image this platform has ever published carries four critical and eight high findings, all inherited from the pinned base, and blocking on a severity threshold would have refused this phase's own workload.

It is gone from the register rather than edited to agree with what was built, which is what the register's own rule requires. The answer went a way the options did not list as obvious: block unless an exception is recorded against the exact digest, enforced at admission rather than at publish -- because ECR scans after the push, so a publish-time refusal would leave that commit permanently unpublishable. It lives in `contracts/image_scan.py`, in `config/policy.yaml`'s `image_scan` block and its `image_scan_findings_unreviewed` condition, in `config/image-exceptions.yaml`, and in `tests/test_phase3_image_scan.py`. Criterion 22 cites the absence and the enforcement together, because either alone would be satisfied by the other's failure.

## The ones still open

None of these has a recommendation, and none may have one. The source is `src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than two options, so an entry cannot become a decision by having its alternatives deleted.

| # | question | has to be answered |
| --- | --- | --- |

**The register is empty, and that is a state rather than an absence.** An empty table here does not mean nobody looked; it means every question this repository surfaced has been answered and the answer put where it is enforced. `src/edullm_platform/open_decisions.py` names each one that has left and where its answer now lives, which is the only place that history is kept.
