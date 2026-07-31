# Phase 2 approval gate evidence

The gate is GitHub configuration rather than code, and nothing in this repository could read it until this capture existed. A setting here changes in a browser in ten seconds and leaves no artifact in any repository, which is why a statement about one expires rather than standing: these records stop loading on 2026-08-26.

Captured by `tools/capture_phase2_evidence.py` from `edu-llm/platform` and read from `fixtures/evidence/phase-2/github/environments.sanitized.json` and `fixtures/evidence/phase-2/github/secrets.sanitized.json`.

## Both approval environments, as configured

| environment | reviewers | branch policy form | branches | admins may bypass | prevents self-review |
| --- | --- | --- | --- | --- | --- |
| run-approval-admin | User:philote-dev, User:BritishAmericqn | custom | main | no | no |
| run-approval-lead | Team:team-leads | custom | main | no | no |

Every environment the capture found is listed, not only the two this phase expects. An environment is auto-created, with no protection rules at all, by anybody who can name one in a workflow file -- which is everybody who can submit -- so a capture reading only the two expected names would report a healthy gate with a third, unprotected environment beside it.

**The branch policy form is asserted specifically and the two forms are not equivalent.** `protected_branches` follows whatever branch protection happens to cover, so it widens the moment a second branch is protected -- a change nobody would connect to this control. `custom_branch_policies` matches names that were written down.

**Self-review is permitted deliberately, and it is not what enforces anything.** A lead self-authorizing a routine run and an admin approving their own exception are both intended. What stops a member approving their own submission is that members are not reviewers on either environment, and independently that `evaluate_authorization` returns `self_approval_not_permitted_for_member`.

## Secrets and variables, by name and never by value

| scope | names |
| --- | --- |
| repository secrets | none |
| organization secrets | none |
| dependabot secrets | none |
| environment secrets on `run-approval-admin` | none |
| environment secrets on `run-approval-lead` | none |
| repository variables | `AWS_ADMISSION_ROLE_ARN`, `AWS_INFRA_DEPLOYER_ROLE_ARN`, `AWS_REGION` |

Names only, and the model has no field a value could occupy, which is a stronger guarantee than a capture tool that is careful. It matters here more than anywhere: the evidence for no-credentials-are-stored must not itself store one.

Phase 2 introduced no credential at all, and that was a live question rather than a foregone conclusion. The fallback, had the approvals endpoint needed a fine-grained token, was to store one as an environment secret. The endpoint answered a `GITHUB_TOKEN` holding actions read, so nothing was stored and both environment secret lists are empty.

## What this capture does not carry

Four artifacts the phase plan asks this document for do not exist, and each is about a run rather than about configuration, which is why the configuration capture cannot reach them.

- **The workflow run URLs.** The runs are in GitHub's Actions history and no committed record names one, so a reviewer cannot get from this bundle to the run it describes.
- **The pending-deployment state.** A submission left unapproved on 2026-07-27 sat in status `waiting` with its submit job reporting no runner at all, and the state machine execution count did not move while it sat there. Nothing reads that.
- **The approvals API response naming the approver.** The approver reaches AWS because the submitting job read it from that endpoint and passed it along; the response itself was never committed.
- **The `$GITHUB_STEP_SUMMARY` the approver saw.** The compile job now uploads the same markdown as an artifact, copied from the file the summary is written from rather than re-rendered. No run that actually waited at a gate has had that artifact captured.

| criterion | status today | what it is short of |
| --- | --- | --- |
| 2 | a gap | the pending-deployment state |
| 3 | a gap | a second lead releasing one run |
| 9 | covered | the approvals API response |
| 11 | a gap | the rendered approver context |

Criterion 3 is the one nobody here can close alone. It needs a lead other than the submitter to release a routine submission, and every run so far was released by the submitter, who is also a lead.
