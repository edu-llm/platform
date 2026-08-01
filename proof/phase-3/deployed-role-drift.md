# Phase 3 deployed-role drift

The four roles this phase creates, captured from the account and compared to the templates that declare them. This is the only check in the bundle that can see a role widened in a console: every other test of these roles reads a committed template, which is what the account was asked for rather than what it holds.

| role | template | verdict |
| --- | --- | --- |
| `sbsandbox-intern-edullm-batch-execution` | `infra/iam/batch-roles.yaml` | ok |
| `sbsandbox-intern-edullm-batch-instance` | `infra/iam/batch-roles.yaml` | ok |
| `sbsandbox-intern-edullm-batch-workload` | `infra/iam/batch-roles.yaml` | ok |
| `sbsandbox-intern-edullm-lifecycle-lambda` | `infra/iam/lifecycle-lambda-role.yaml` | ok |

## What this does not cover

Two roles the checks about separation of authority are actually about are not here. `sbsandbox-intern-edullm-admission-lambda` and `sbsandbox-intern-edullm-admission-states` are registered in `PHASE2_ROLE_TEMPLATES`, so a capture of them belongs to Phase 2's evidence and Phase 2's freshness window rather than being copied here. Until they are captured, the claim that the validator could not have submitted the job rests on a template.

A policy declining to permit an action is also not AWS refusing one. The workload role's deployed policy grants no lineage write and no way to start anything, and that is what these captures establish; the denial matrix is the only thing that shows a call being turned down, and the workload half of it has not run.

One thing these captures did find, recorded here rather than left for a later phase to discover: the deployed workload role permits `s3:PutObject` under `teams/*/runs/*` rather than under one team's prefix. The template agrees, so it is deliberate rather than drift, and for a single-team pilot nothing is misattributed -- but the cross-team isolation the `teams/` segment exists to make expressible is not expressed yet.
