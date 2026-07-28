# Phase 2 open decisions

A criterion records something that must be true and whether it is. This records something nobody has decided. A gap means unfinished work and a deferral means a postponement with a trigger; neither fits a question whose answer is a policy choice, and a question like that has two fates if it is not written down. It is settled by accident by whoever first trips over it, or silently by whoever happens to be implementing near it.

Phase 1's question -- whether a registry scan result may block a publish -- was carried into this register and answered during Phase 3. It is gone from here rather than edited to agree with what was built, which is what the register's own rule requires; the answer lives in `contracts/image_scan.py`, in `config/policy.yaml` and in `config/image-exceptions.yaml`.

None of what is left has a recommendation, and none may have one. The source is `src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than two options, so an entry cannot become a decision by having its alternatives deleted.

| # | question | has to be answered |
| --- | --- | --- |

**The register is empty, and that is a state rather than an absence.** An empty table here does not mean nobody looked; it means every question this repository surfaced has been answered and the answer put where it is enforced. `src/edullm_platform/open_decisions.py` names each one that has left and where its answer now lives, which is the only place that history is kept.

## What Phase 2 left unrecorded here, and why

The nine decisions the Phase 2 plan opened with, D1 to D9, were all taken before the phase shipped: they are settled in `config/organization.yaml`, in the two environments' configuration, in `infra/lineage-bucket.yaml` and in the submission workflow. A decision that has been taken belongs where it is enforced rather than in a register of open ones, so none of them is repeated here.
