# Phase 2 authorization matrix

Every row below was evaluated by `evaluate_authorization` while this bundle was generated, against `config/policy.yaml` and `config/organization.yaml` as they are committed. Nothing here is a recollection of what the function returns.

The approval scope in force is `organization`, which is what makes criterion 3's statement -- that any team lead may release a routine run -- the thing to check rather than assume.

## The committed scenarios

The three scenarios under `fixtures/authorization/`, each carrying the outcome it expects. The last column compares that expectation to what the function returned just now, so a scenario whose recorded expectation has drifted away from the code shows up here rather than in a reader's assumptions.

| scenario | submitter | approver | class | outcome | reason | matches its recorded expectation |
| --- | --- | --- | --- | --- | --- | --- |
| admin-exception | `caiiris` (member) | `BritishAmericqn` (admin) | exception | granted | `exception_approved_by_admin` | yes |
| lead-self-authorization | `ericrcwu001` (lead) | — | routine | granted | `routine_self_authorized` | yes |
| member-approval | `caiiris` (member) | `ericrcwu001` (lead) | routine | granted | `routine_approved_by_lead_or_admin` | yes |

## The refusals, derived by varying one actor

Built from the committed scenarios' own request facts, changing exactly one thing per row. The logins are read off the roster by role rather than written here, so this table keeps describing the roster after somebody is promoted instead of describing whoever held a role when it was written.

| case | submitter | approver | class | outcome | reason | team verified |
| --- | --- | --- | --- | --- | --- | --- |
| member submits, nobody approves | `caiiris` (member) | — | routine | refused | `self_approval_not_permitted_for_member` | no |
| member submits, another member approves | `caiiris` (member) | `GMatherne` (member) | routine | refused | `approver_lacks_lead_or_admin_role` | no |
| member submits, approver is off the roster | `caiiris` (member) | `not-a-member` (not on the roster) | routine | refused | `approver_not_in_roster` | no |
| submitter is off the roster | `not-a-member` (not on the roster) | `ericrcwu001` (lead) | routine | refused | `submitter_not_in_roster` | no |
| exception, approved by a lead who is not an admin | `caiiris` (member) | `ericrcwu001` (lead) | exception | refused | `approver_lacks_admin_role` | no |
| lead self-authorizes, attributing the run to another team | `ericrcwu001` (lead) | — | routine | granted | `routine_self_authorized` | no |

## The last row, and why it is a deferral rather than a failure

A lead self-authorizing a run attributed to a team that is not theirs is granted, and criterion 4 is deferred for that reason rather than failing. `team_bindings.teams` in `config/organization.yaml` is empty, so membership is unverifiable and enforcing this literally would reject every submission, including the ones that should succeed.

What keeps that visible rather than silent is the `team verified` column: it is `no` on every row, and every decision record in the lineage store carries the same false, so an unverified attribution is written into the audit trail rather than passed over. The deferral becomes live again with no code change, the moment `team_bindings.teams` is populated.

## What this matrix does not establish

- That GitHub agrees. This is the platform's own authorization function, and it holds regardless of how the environments are configured. The second mechanism -- that members are not reviewers on either gate -- is GitHub configuration and lives in `approval-gate-evidence.md`.
- That the approver in a decision record is who GitHub says it is. The OIDC token proves an approval happened and which gate it passed; it carries no claim naming the approver. The identity reaches AWS because the submitting job read it from the approvals API and passed it along, so a compromised runner could still misreport who released a run.
- That any of these cases happened. Rows are evaluations of the shipped code, not observations of the account; what the account did is in `lineage-record-evidence.md`.
