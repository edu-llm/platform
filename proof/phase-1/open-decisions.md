# Phase 1 open decisions

A criterion records something that must be true and whether it is. This records something nobody has decided. A gap means unfinished work and a deferral means a postponement with a trigger; neither fits a question whose answer is a policy choice, and a question like that has two fates if it is not written down. It is settled by accident by whoever first trips over it, or silently by whoever happens to be implementing near it.

None of these has a recommendation, and none may have one. The source is `src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than two options, so an entry cannot become a decision by having its alternatives deleted. Answering one means removing it from there and putting the answer where it is enforced.

| # | question | has to be answered |
| --- | --- | --- |
| 1 | Should the result of the registry image scan be able to block a publish, and if so on what? | Before the first workload runs a published image. The phase that introduces the workload role is where that happens, and this must be answered before its acceptance criteria are written rather than after. |

## Decision 1 — Should the result of the registry image scan be able to block a publish, and if so on what?

**Raised by.** Phase 1's first live publish, whose scan returned four critical and eight high findings and blocked nothing, because nothing was ever wired to it.

**Why it matters.**

- Nothing runs a Phase 1 image, so the findings are inert today and the question has no urgency. It acquires all of its urgency at once, on the day the first workload runs one, and that is the worst moment to be deciding it: whoever is standing there will settle it by whichever way makes their job work.
- It is a policy question rather than an implementation one. Blocking on criticals would have refused the image this phase published, whose four critical findings are all inherited from a base image the platform chose and pins. Not blocking at all means a scan runs on every push, costs nothing to ignore, and is decoration.
- Whatever the answer, the enforcement point is the publish workflow, which is Phase 1's file. A rule added there later is a change to the path this phase's criteria are written about.

**What is known.**

- The repository is created with ScanOnPush, so a scan exists as soon as an image does. The scan of the published image is committed under fixtures/evidence/phase-1/run/image-scan.sanitized.json: status COMPLETE, four critical and eight high findings.
- Those findings are the base image's. The Dockerfile installs nothing — it sets three environment variables, creates a working directory and copies the source — so every package a scanner can see came from the registered base, which is pinned by digest in config/repositories.yaml.
- A gate would need no new permission. The publisher role already holds ecr:DescribeImageScanFindings, because reading the scan back was anticipated even though nothing reads it yet.
- Whatever the rule, it cannot be enforced at push time in the obvious way: ECR scans an image after it is pushed, so a scan result can refuse the next step but cannot prevent the image existing. A tag that has been written cannot be withdrawn, only left unused.

**The options, none of them chosen.**

- Record and never block. The scan is evidence, a run manifest names a digest, and whether a digest with findings may run is decided by whoever authorizes the run rather than by the build.
- Block on a severity threshold. Simple to state and simple to check, and it would have refused this phase's first image on findings nobody in this project introduced or can fix without changing the base.
- Block only on findings the build introduced — those present in the image and absent from the registered base, which is scannable on its own. Narrow and meaningful, and it needs a second scan and a comparison that does not exist.
- Block unless an exception is recorded against the digest, in the way Phase 0 already records exceptions for fan-out over the routine ceiling.

**Has to be answered.** Before the first workload runs a published image. The phase that introduces the workload role is where that happens, and this must be answered before its acceptance criteria are written rather than after.
