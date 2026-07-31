# Phase 5 access control evidence

Granting write access to this repository is granting merge access to five workflow files pinned by name in three IAM trust policies, and to the source and configuration that ship inside the two released Lambda zips. This document is the containment for that, captured from GitHub rather than described.

## How `main` is protected

| setting | value |
| --- | --- |
| branch | main |
| required approving reviews | 1 |
| code-owner review required | yes |
| stale reviews dismissed | yes |
| enforced for admins | no |
| force pushes | no |
| deletions | no |
| conversation resolution required | yes |
| required status checks | checks (python 3.12), checks (python 3.13) |

**`enforced for admins` is `no`, and check 10 is worded around it.** The master plan asks that a change to a workflow file cannot reach `main` without a code-owner review. That is false for the three admins and stays false by decision: turning `enforce_admins` on makes every pull request the author writes wait on the one other code owner, on a repository where the author is writing most of them. So the criterion is about what a *member* may do, and it is covered as that narrower claim. A gate asserting the unqualified sentence would be asserting something untrue about this account, which is worse than a narrower claim that holds.

The required checks are recorded beside the review requirement because a code-owner review with nothing else behind it lets a member merge a red branch, which is the same bypass by another route.

## What a code owner owns

- /.github/CODEOWNERS
- /.github/workflows/**
- /infra/**
- /src/edullm_platform/**
- /config/**
- /tools/build_admission_lambda.py
- /tools/build_lifecycle_lambda.py

The last four of those were added when write access was first granted to somebody who did not build this platform. Until then ownership covered the workflows and the infrastructure and left the admission validator's own source and the policy it enforces uncovered -- and `tools/build_admission_lambda.py` copies `config/*.yaml` and the whole `src/edullm_platform` tree into the zip, so a change to either decides whether a run is authorized. The test behind check 10 walks the packaged set rather than checking that the file merely exists, so the next module added under `src/` cannot quietly fall outside it.

## Who may start a deployment

Check 9 is covered, and it was not built the way the plan specified. The plan asked for a repository actor rule in evaluate mode. The organization is on the `free` plan, where ruleset enforcement is `active` or `disabled` and `evaluate` is Enterprise Cloud only, so "measured against real dispatches before it refuses one" was unavailable.

An environment gate is worse than it looks and was rejected on inspection: `infra/iam/infra-deployer-role.yaml` pins the OIDC subject with `StringLike` to a ref, and naming an `environment:` on a deploy job rewrites that claim to `…:environment:<name>` and silently revokes every deployment.

What shipped instead is a guard step, first in each deploy job and before the checkout, failing rather than skipping, tied to the admin list in `config/organization.yaml`. It guards the dispatch path only -- a control that also blocked the merge path would have stopped deployment entirely, which is why a push to `main` deploying without meeting the guard is asserted rather than treated as a hole. The three copies are asserted word-for-word identical, because three copies that drift are one workflow silently unguarded.
