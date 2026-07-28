# Phase 1 open decisions

A criterion records something that must be true and whether it is. This records something nobody has decided. A gap means unfinished work and a deferral means a postponement with a trigger; neither fits a question whose answer is a policy choice, and a question like that has two fates if it is not written down. It is settled by accident by whoever first trips over it, or silently by whoever happens to be implementing near it.

None of these has a recommendation, and none may have one. The source is `src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than two options, so an entry cannot become a decision by having its alternatives deleted. Answering one means removing it from there and putting the answer where it is enforced.

| # | question | has to be answered |
| --- | --- | --- |

**The register is empty, and that is a state rather than an absence.** An empty table here does not mean nobody looked; it means every question this repository surfaced has been answered and the answer put where it is enforced. `src/edullm_platform/open_decisions.py` names each one that has left and where its answer now lives, which is the only place that history is kept.
