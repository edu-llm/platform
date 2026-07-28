# Phase 3 open decisions

A criterion records something that must be true and whether it is. This records something nobody has decided. A gap means unfinished work and a deferral means a postponement with a trigger; neither fits a question whose answer is a policy choice, and a question like that has two fates if it is not written down. It is settled by accident by whoever first trips over it, or silently by whoever happens to be implementing near it.

## The one this phase answered

Decision 1, on whether a registry scan result may block a publish, was Phase 1's and landed here: the only image this platform has ever published carries four critical and eight high findings, all inherited from the pinned base, and blocking on a severity threshold would have refused this phase's own workload.

It is gone from the register rather than edited to agree with what was built, which is what the register's own rule requires. The answer went a way the options did not list as obvious: block unless an exception is recorded against the exact digest, enforced at admission rather than at publish -- because ECR scans after the push, so a publish-time refusal would leave that commit permanently unpublishable. It lives in `contracts/image_scan.py`, in `config/policy.yaml`'s `image_scan` block and its `image_scan_findings_unreviewed` condition, in `config/image-exceptions.yaml`, and in `tests/test_phase3_image_scan.py`. Criterion 22 cites the absence and the enforcement together, because either alone would be satisfied by the other's failure.

## The ones still open

None of these has a recommendation, and none may have one. The source is `src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than two options, so an entry cannot become a decision by having its alternatives deleted.

| # | question | has to be answered |
| --- | --- | --- |
| 2 | Should the workload role's write access be scoped per run, per team, or per bucket? | Before a second team submits, or before Phase 4 writes its check that outputs land only under the authorized run prefix -- whichever comes first. Phase 3 may ship the widest scope provided the record says so; it may not ship a narrower claim than it enforces. |

## Decision 2 — Should the workload role's write access be scoped per run, per team, or per bucket?

**Raised by.** Phase 3 writing sbsandbox-intern-edullm-batch-workload and finding that nothing in the repository said what it should be allowed to write to.

**Why it matters.**

- Phase 3 writes the first workload role, so whichever scope it uses becomes the shape every later team inherits. Nothing forces the choice at the moment it is made, which is exactly the condition under which it gets made by whoever is typing.
- Phase 4 asserts that S3 receives outputs only under the authorized run prefix, and Phase 5 asserts that cross-team data access fails closed. Both are claims about this scope. A role scoped per bucket satisfies neither and would pass every test written before those phases.
- The scopes are not equally reachable. A static role cannot name a run id, so per-run needs either a session tag the submitting principal sets or a role assumed per run; per-team needs a prefix convention and a role per team or a tag. Deciding late means discovering the mechanism late, after the prefix layout is already in lineage records that cannot be rewritten.

**What is known.**

- The lineage bucket is not a candidate. It is write-once by bucket policy and only the admission state machine writes to it; a workload role holding s3:PutObject there would undo the property that store exists to have.
- Batch supports neither tags on the job role session nor a per-job role override at submit time in a way this platform currently uses, so per-run scoping is not free: it needs the run id to reach the policy somehow, and the two ways to do that are a session tag and a role per run.
- config/organization.yaml carries no team bindings yet, so per-team scoping has nothing to enumerate today. TeamBinding already has fields for an S3 namespace, which is where the answer would land.

**The options, none of them chosen.**

- Per bucket. One outputs bucket, the workload role may write anywhere in it. Simplest, and it makes the Phase 4 and Phase 5 isolation checks unprovable rather than failing.
- Per team, through a prefix and the S3 namespace TeamBinding already declares. Reachable with a role per team, and it matches where Phase 5 is going, but it isolates teams rather than runs.
- Per run, through a session tag the submitting principal sets and an aws:PrincipalTag condition on the prefix. The narrowest, and the only one that makes 'outputs only under the authorized run prefix' literally true; it needs the tag to travel from admission into the Batch job.

**Has to be answered.** Before a second team submits, or before Phase 4 writes its check that outputs land only under the authorized run prefix -- whichever comes first. Phase 3 may ship the widest scope provided the record says so; it may not ship a narrower claim than it enforces.
