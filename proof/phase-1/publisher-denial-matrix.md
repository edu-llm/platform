# Phase 1 publisher denial matrix and the run it came from

The publisher role is meant to hold nine ECR actions on one repository and nothing else. Everything else in this repository that says so reads a template or a capture, which is an argument from a policy. This is the other kind of evidence: a session issued to that role through OIDC attempted five things it must not be able to do, and was refused all five. The records are committed under `fixtures/evidence/phase-1/run/denials/` and each carries the CloudTrail event id of the refusal, so a reviewer can look up any of them in the account.

## The run

| fact | value |
| --- | --- |
| commit | `4204375e6db85abc244ec7f626de8d3cc3511402` |
| image tag | `4204375e6db8` |
| image digest | `sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8` |
| base image digest | `sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| pushed at | 2026-07-26T22:05:41.454000+00:00 |
| publisher session assumed at | 2026-07-26T22:05:19+00:00 |
| publisher session expires at | 2026-07-26T23:05:19+00:00 |
| OIDC issuer | `token.actions.githubusercontent.com` |
| OIDC subject | `repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/heads/edullm/platform-build-image` |
| scan status | COMPLETE |
| scan findings | 4 critical, 8 high, 4 medium, 1 low |
| repository tag mutability | IMMUTABLE |

The session is the one that made the push rather than the most recent one the role held, and it is not found by proximity: two publisher sessions exist in every run, twenty-five seconds apart and overlapping. The `PutImage` event carries the creation instant of the session that made it, and exactly one `AssumeRoleWithWebIdentity` event has that instant.

## What the session was refused

| action | the call attempted | error code | CloudTrail event |
| --- | --- | --- | --- |
| `batch:SubmitJob` | `batch SubmitJob` | AccessDeniedException | `21bd62b9-4797-494c-9255-b9c8dc84647e` |
| `s3:ListAllMyBuckets` | `s3 ListBuckets` | AccessDenied | `8fded737-d906-44ad-a74f-e3b4ead75f06` |
| `iam:CreateRole` | `iam CreateRole` | AccessDenied | `e4ed1062-27d4-456b-ae91-91e18816be72` |
| `batch:UpdateComputeEnvironment` | `batch UpdateComputeEnvironment` | AccessDeniedException | `34af3061-53d8-418b-9700-407457d31d2a` |
| `ecr:DeleteRepository` | `ecr DeleteRepository` | AccessDeniedException | `386779c1-c28d-4abd-960e-f28547020cfd` |

The record must hold one denial per matrix action, in matrix order. A run that refused four of the five proved the criterion for four of them, and a file able to hold the four would be read later as though it had proved all five.

## How each probe is aimed, and what a permitted call would have done

| action | resource | why a permitted call changes nothing |
| --- | --- | --- |
| `batch:SubmitJob` | `edullm-denial-probe-absent-queue` | the queue and the job definition do not exist |
| `s3:ListAllMyBuckets` | — | the call only lists and names no bucket |
| `iam:CreateRole` | `sbsandbox-intern-edullm-ecr-publisher` | the role name is the caller's own, which IAM already holds |
| `batch:UpdateComputeEnvironment` | `edullm-denial-probe-absent-compute-environment` | the compute environment does not exist |
| `ecr:DeleteRepository` | `sbsandbox-intern-edullm-olmo-core-denial-probe-absent` | the repository name is one beside the registered one, and absent |

## What choosing a probe has cost

Read this before adding one. Each entry is a rule some probe in this matrix broke, with the run that broke it, because a rule with no incident attached reads as caution and gets skipped. The source of truth is `edullm_platform.publisher_denials.PROBE_SELECTION_LESSONS`.

### A probe whose target may not exist can be answered by existence instead of by authorization, and will pass intermittently.

**Learned from.** The original S3 probe read an object from a bucket chosen not to exist. It returned AccessDenied on one run and NoSuchBucket on the next, for the same role against the same absent bucket.

Two answers to one question is the symptom, and the cause is that the service was answering a different question each time. S3 routes a request to a bucket before it authorizes the caller, so which answer comes back depends on where the request got to — which is a function of routing and caching rather than of the role. The dangerous half is the direction the flake runs in: AccessDenied is the answer the matrix is looking for, so a probe like this reports success some of the time whatever the role can actually do, and a role widened to include S3 would have been reported as narrow on any run that happened to answer AccessDenied first.

The rule generalises past S3. Any probe aimed at an absent resource is a race between the service's not-found path and its authorization path, and a probe that can be answered not-found can be answered not-found *instead of* denied. That conflicts directly with the requirement that a permitted call must do nothing, which is what pointed the probe at an absent resource in the first place, so the two have to be reconciled per service rather than by a blanket rule. Three ways out are in use here: an account-level call that names no resource and so has nothing to be absent (s3:ListAllMyBuckets); a call whose resource exists and whose permitted outcome is a collision rather than a change (iam:CreateRole against a role name IAM already holds); and a call against an absent resource in a service that authorizes first, which is the remaining three probes and is safe only because those services do authorize first — a fact about Batch and ECR today, not a guarantee.

The price of the way out is recorded beside the probe: ListBuckets proves the role holds no account-wide S3 permission, which is weaker than the criterion's 'cannot read datasets'. A weaker claim that is always true beats a stronger one that is true at random.

### A refusal is recognised by its error code and operation, never by its wording.

**Learned from.** The matrix first required the message to read 'is not authorized to perform: <action>'. IAM and Batch say that; S3 answers AccessDenied with the message 'Access Denied' and nothing else.

The service the criterion is most about was the one service that could never satisfy the test. Wording is not part of any API contract and changes without notice; the error code and the operation the error names are. The message is still read where there is one — a message naming an action must name this one, and one blaming a resource-based policy is refused whatever else it says — but a terse refusal is accepted on the code alone, and that limit is real.

### A run reports every probe's outcome, rather than stopping at the first anomaly.

**Learned from.** The first live run stopped when the S3 probe failed, so it established nothing about the four probes after it and cost a workflow run to learn one thing.

Reaching this account costs a real run under a real OIDC session, which is not something a developer can do in a loop. A matrix that stops at its first surprise turns one run into one fact; a matrix that attempts everything and reports everything turns one run into five.
