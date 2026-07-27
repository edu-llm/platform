"""The Phase 2 acceptance criteria and the tests that are cited for each one.

Phase 2 authorizes one run manifest behind a reviewed GitHub environment, assumes a
bounded AWS role whose trust requires that gate, executes Step Functions admission, and
writes one immutable intent record and one immutable decision record to S3. This module
records the twenty-two checks the phase must satisfy, against the contract in
``edullm_platform.criteria``.

**Most of them are gaps today, and the reason is worth being exact about.** The path ran.
On 2026-07-27 a submission went through the lead gate and produced records; an
exception-classified submission routed to the admin gate and produced records; a duplicate
execution name was refused; a tampered hash was refused and still earned a decision record;
and the six-probe denial matrix came back refused on every entry. Those runs are in the
account. What is not in this repository is a committed, sanitized capture of any of them,
so no test reads them, so no criterion may cite one. A criterion whose statement can only
be established by evidence nobody has committed is a ``GAP`` here however convincing the
run was to whoever watched it. That is the whole discipline: the gate executes tests, and a
test that reads nothing proves nothing.

Each such criterion records, in its ``gaps``, what was observed and what would close it.
Those entries are the Wave 6 checklist. They are not a promise that a capture will agree
with the run; that is what capturing is for.

**What is covered rests on committed artifacts, and the split follows what the artifact
is.** A workflow file is read by GitHub exactly as committed, with no deployment step in
between, so a test that pins one proves what runs. The admission core is the code the
packaged Lambda carries, so a test of it proves what AWS decides. A CloudFormation template
describes a role deployed once, by hand, from a laptop; a test of it proves what a document
declares and stays supporting until a capture has been compared against it, which for the
three Phase 2 roles has not happened yet.

**One criterion is deferred and it is inherited.** Wrong-team lead approval is
intentionally not enforced while ``team_bindings.teams`` is empty, exactly as in Phase 0.
Its trigger is a configuration change rather than a code change, which is the point of
having recorded it.

**The residual this phase cannot close by testing harder.** The OIDC token proves that an
approval happened and which gate it passed; it carries no claim naming the approver. The
identity in a decision record reaches AWS because the submitting job read it from the
GitHub API and passed it along. Criterion 21 states what the record carries; it does not
claim AWS verified it.
"""

from __future__ import annotations

from typing import Final

from edullm_platform.criteria import (
    CriterionSpec,
    CriterionStatus,
    validate_criterion_specs,
)

__all__ = [
    "NEEDS_A_COMMITTED_CAPTURE",
    "PHASE2_CRITERION_COUNT",
    "TEMPLATE_NOT_CAPTURE",
    "phase2_criteria",
]

PHASE2_CRITERION_COUNT: Final = 22

ADMISSION = "tests/test_phase2_admission.py"
HANDLER = "tests/test_phase2_admission_handler.py"
RECORDS = "tests/test_phase2_admission_records.py"
WORKFLOW = "tests/test_phase2_submit_run_workflow.py"
INFRA = "tests/test_phase2_infrastructure.py"
DENIALS = "tests/test_phase2_admission_denials.py"
PACKAGE = "tests/test_phase2_lambda_package.py"
AUTHZ = "tests/test_authorization.py"

#: What closes a criterion resting on a run that happened and was never captured. Written
#: once because it is the same sentence twelve times, and a reader who has met it once
#: should not have to check whether the twelfth wording differs. ``gaps`` is a tuple the
#: gate joins with a space, so this is a sibling element rather than a concatenation.
NEEDS_A_COMMITTED_CAPTURE: Final = (
    "Nothing reads any of this. tools/capture_phase2_evidence.py does not exist, nothing "
    "is committed under fixtures/evidence/phase-2/, and no test in this repository opens "
    "a Phase 2 capture. Closing this means capturing the named artifact, sanitizing it "
    "through the existing SecretFreeStr and account-id redaction machinery, committing "
    "it, and citing a test that reads it."
)

#: Why a template citation is not a deployed-role citation. Phase 1 draws the same
#: distinction for its two roles; Phase 2's three have no capture at all yet.
TEMPLATE_NOT_CAPTURE: Final = (
    "A citation here reads a committed CloudFormation template, which is what the account "
    "was asked for rather than what it holds. The three Phase 2 roles were deployed from "
    "a laptop on 2026-07-27 and no capture has been compared against them, so no Phase 2 "
    "role appears in role_drift.COMMITTED_ROLE_TEMPLATES and the comparison that catches "
    "a role widened in the console does not run for them."
)


def _ids(module: str, name: str, *params: str) -> tuple[str, ...]:
    """Node ids for one test, with its parametrizations spelled out.

    A parametrized test collects only under its full node id, so citing the bare name
    names nothing at all. Ten citations here were first written that way, from a grep over
    ``def test``, and the gate reported ten criteria as gaps with ``cited_test_missing``
    -- the right verdict, arrived at slowly, and invisible to any amount of reading.

    Building them here also keeps every citation on one line. That is not only tidiness:
    a node id split across two lines inside a tuple is implicit string concatenation, and
    the long parametrized ids in this module do not otherwise fit.
    """
    if not params:
        return (f"{module}::{name}",)
    return tuple(f"{module}::{name}[{param}]" for param in params)


def phase2_criteria() -> tuple[CriterionSpec, ...]:
    """The twenty-two Phase 2 acceptance criteria, in the phase plan's order."""
    specs = (
        CriterionSpec(
            number="1",
            statement="Lead self-authorization succeeds.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(ADMISSION, "test_a_correct_submission_through_the_right_gate_is_admitted"),
                *_ids(HANDLER, "test_the_handler_admits_a_routine_submission_a_lead_released"),
            ),
            gaps=(
                (
                    "The authorization half is proved: evaluate_authorization returns "
                    "routine_self_authorized for a lead releasing their own routine "
                    "submission, and the admission core admits it. What is unproved is the "
                    "end-to-end half the phase plan requires. A lead did submit and release "
                    "their own run on 2026-07-27, through the browser rather than the API, "
                    "and the decision record in the account reads routine_self_authorized "
                    "under policy v1."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="2",
            statement="Member submission without lead approval is rejected.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(WORKFLOW, "test_the_three_jobs_carry_exactly_these_permission_maps"),
                *_ids(WORKFLOW, "test_the_compile_job_cannot_request_a_token_by_any_spelling"),
                *_ids(
                    ADMISSION,
                    "test_a_submission_its_approver_may_not_release_is_refused",
                    "caiiris-nzhao721-approver_lacks_lead_or_admin_role",
                    "caiiris-None-self_approval_not_permitted_for_member",
                    "not-a-member-ericrcwu001-submitter_not_in_roster",
                    "caiiris-not-a-member-approver_not_in_roster",
                ),
            ),
            gaps=(
                (
                    "Two independent mechanisms, one proved and one not. The authorization "
                    "half holds: a submission whose approver may not release it is refused. "
                    "The GitHub half was observed rather than captured. A submission left "
                    "unapproved on 2026-07-27 sat in status waiting with its submit job "
                    "reporting no runner at all, and the state machine execution count did "
                    "not move while it sat there."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="3",
            statement=(
                "Any team lead approval succeeds while approval_scope is organization."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    AUTHZ,
                    "test_plain_member_routine_run_is_granted_by_any_lead_under_organization_scope",
                ),
                *_ids(
                    AUTHZ,
                    "test_admin_without_a_team_lead_role_may_approve_a_member_routine_run",
                ),
            ),
            gaps=(
                (
                    "The authorization matrix is proved across the lead roster. The live "
                    "corroboration is the one scenario in this phase that one person cannot "
                    "produce: it needs a lead other than the submitter to release the "
                    "deployment, and every run so far was released by the submitter, who is "
                    "also a lead. Closing this needs a second member of the team-leads team "
                    "to approve one routine submission."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="4",
            statement="Wrong-team lead approval is rejected.",
            status=CriterionStatus.DEFERRED,
            supporting_node_ids=(
                *_ids(
                    AUTHZ,
                    "test_a_lead_self_authorizing_cannot_attribute_the_run_to_a_foreign_team",
                ),
            ),
            deferral_reason=(
                "team_bindings.teams in config/organization.yaml is empty, so membership "
                "is unverifiable and enforcing this literally would reject every "
                "submission, including the ones that should succeed. Every decision "
                "records team_verified false in consequence, which is what makes the "
                "unverified attribution visible in the audit trail rather than silent. "
                "Carried forward from Phase 0's deferral of the same question."
            ),
            deferral_trigger=(
                "Populating team_bindings.teams in config/organization.yaml once sub-team "
                "assignments exist. Enforcement goes live with no code change, "
                "team_verified starts reporting true, and this must be re-recorded as "
                "covered or argued again."
            ),
        ),
        CriterionSpec(
            number="5",
            statement="Admin exception succeeds only through the admin path.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    ADMISSION,
                    "test_an_exception_released_by_an_admin_through_the_admin_gate_is_admitted",
                ),
                *_ids(ADMISSION, "test_an_exception_released_by_the_lead_gate_is_refused"),
                *_ids(
                    ADMISSION,
                    "test_a_routine_submission_released_by_the_admin_gate_is_refused",
                ),
                *_ids(
                    INFRA,
                    "test_admission_subject_condition_is_a_two_element_array_of_environment_subjects",
                ),
            ),
            gaps=(
                (
                    "Both directions are proved in the core: an exception released by the "
                    "lead gate is refused, and a routine submission released by the admin "
                    "gate is refused. The live half was observed. A submission overridden to "
                    "100 hours, priced at 567.20 dollars, classified as an exception and "
                    "routed to run-approval-admin, whose reviewers are the two roster admins "
                    "and not the leads team."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="6",
            statement=(
                "Wrong repository, ref, audience, or manifest hash cannot assume or use "
                "the role."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    INFRA,
                    "test_admission_trust_policy_uses_no_stringlike_and_no_wildcard_anywhere",
                ),
                *_ids(
                    ADMISSION,
                    "test_a_manifest_that_does_not_hash_to_what_was_approved_is_refused",
                ),
            ),
            gaps=(
                (
                    "Split by mechanism, and each half is short of what it needs. Repository "
                    "and audience are trust-policy conditions, and the citation for them "
                    "reads a template rather than the deployed role."
                ),
                TEMPLATE_NOT_CAPTURE,
                (
                    "Ref is proved live by criterion 7 and manifest hash by criterion 13, "
                    "both of which are themselves gaps for want of a capture. Minting a token "
                    "from another repository is not something this project can arrange, so "
                    "the repository and audience conditions will close on a deployed-role "
                    "comparison rather than on an attempt."
                ),
            ),
        ),
        CriterionSpec(
            number="7",
            statement=(
                "A job that omits the approval environment cannot assume the admission "
                "role, even from main."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    WORKFLOW,
                    "test_the_deny_probe_holds_a_token_and_deliberately_names_no_environment",
                ),
                *_ids(
                    INFRA,
                    "test_admission_role_trusts_exactly_the_two_protected_environment_subjects",
                ),
            ),
            gaps=(
                (
                    "The strongest evidence this phase has produced, and none of it is "
                    "committed. The deny-unapproved job lives in the submission workflow so "
                    "that the environment is the only variable, with the same repository, the "
                    "same workflow ref and the same branch, and it succeeded on every live "
                    "run, meaning STS refused the ref-based subject with AccessDenied. The "
                    "probe also refuses to report a refusal it cannot attribute: on the first "
                    "run the admission role ARN was unset and it failed with "
                    "admission_role_arn_unset rather than claiming a denial."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The artifact to capture is CloudTrail's AssumeRoleWithWebIdentity record, "
                    "carrying the ref-based subject in "
                    "responseElements.subjectFromWebIdentityToken. Design the retries around "
                    "the documented fifteen-minute delivery window rather than the roughly "
                    "three minutes Phase 1 observed."
                ),
            ),
        ),
        CriterionSpec(
            number="8",
            statement=(
                "A dispatched run obtains no OIDC token, credential, or secret before a "
                "reviewer approves."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(WORKFLOW, "test_the_compile_job_cannot_request_a_token_by_any_spelling"),
                *_ids(WORKFLOW, "test_the_three_jobs_carry_exactly_these_permission_maps"),
            ),
            supporting_node_ids=(
                *_ids(
                    WORKFLOW,
                    "test_the_submit_job_takes_its_gate_from_needs_and_never_from_the_form",
                ),
            ),
            scope_limits=(
                (
                    "Covered on the committed workflow rather than on a capture, and that is "
                    "sufficient here for a reason the other live criteria do not share: the "
                    "claim is about what a job is permitted to ask for, and permissions are "
                    "declared in the file GitHub reads. The compile job holds no id-token "
                    "permission by any spelling, so it cannot request a token rather than "
                    "being trusted not to."
                ),
                (
                    "The documented mechanics carry the rest. A job pending approval is never "
                    "dispatched to a runner, so ACTIONS_ID_TOKEN_REQUEST_URL exists in no "
                    "process. That was observed on every run, with the submit job reporting "
                    "no runner while the run sat in waiting, but the observation corroborates "
                    "the permission map rather than being what proves it."
                ),
                (
                    "Not claimed: that CloudTrail shows no AssumeRoleWithWebIdentity for a run "
                    "before its approval timestamp. That is a stronger statement resting on a "
                    "capture nobody has taken, and it belongs to criterion 7's evidence."
                ),
            ),
        ),
        CriterionSpec(
            number="9",
            statement="A member cannot approve their own submission.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    AUTHZ,
                    "test_case_variants_of_a_member_login_are_recognized_as_self_approval",
                    "caiiris-CAIIRIS",
                    "CAIIRIS-caiiris",
                    "CaIiRiS-caIIris",
                ),
                *_ids(AUTHZ, "test_plain_member_routine_run_approved_by_another_plain_member_is_denied"),
            ),
            gaps=(
                (
                    "Enforced twice and proved once. evaluate_authorization returns "
                    "self_approval_not_permitted_for_member, which is the mechanism that "
                    "holds regardless of GitHub's configuration. The second mechanism, that "
                    "members are not reviewers on either environment, rests on the "
                    "environment configuration, and nothing in this repository reads it."
                ),
                (
                    "Note that prevent_self_review is deliberately false on both "
                    "environments, because leads self-authorizing and admins approving their "
                    "own exceptions are both intended. The flag is not what enforces this, "
                    "and a reader must not be left thinking it is."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="10",
            statement=(
                "A submitter cannot influence their own classification to route to the "
                "weaker approval path."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(
                    WORKFLOW,
                    "test_the_submit_job_takes_its_gate_from_needs_and_never_from_the_form",
                ),
                *_ids(
                    ADMISSION,
                    "test_the_class_is_re_derived_rather_than_read_from_the_gate_that_released_it",
                    "run-approval-admin",
                    "run-approval-lead",
                ),
                *_ids(ADMISSION, "test_an_exception_released_by_the_lead_gate_is_refused"),
            ),
            supporting_node_ids=(
                *_ids(
                    RECORDS,
                    "test_policy_picks_the_gate_a_classification_must_pass_through",
                    "exception-run-approval-admin",
                    "routine-run-approval-lead",
                ),
            ),
            scope_limits=(
                (
                    "The security boundary of this phase, and it is covered on tests because "
                    "two independent mechanisms are both readable from committed artifacts. "
                    "The workflow's environment key resolves from needs, so the gate is named "
                    "by a credential-free job that computes it from policy. GitHub would "
                    "equally accept an expression over the dispatch inputs, and that spelling "
                    "is the one a later editor is likely to write, which is why it is pinned."
                ),
                (
                    "The second mechanism is that admission re-derives the classification "
                    "inside AWS and compares it against the gate that actually released the "
                    "run. The OIDC claim proves which gate was passed, not that it was the "
                    "right one, so without the comparison a submitter who could influence "
                    "routing would face no second check. Both directions are tested."
                ),
                (
                    "Corroborated live but not captured: a submission overridden to 100 hours "
                    "was observed routing to run-approval-admin. That observation adds nothing "
                    "this criterion rests on, and the capture belongs to criterion 5."
                ),
            ),
        ),
        CriterionSpec(
            number="11",
            statement=(
                "The approver sees submitter, team, repository, branch, short SHA, image "
                "digest, dataset release, compute profile and rate, the worst-case cost "
                "arithmetic, the classification, and the exceeded bound before the gate "
                "opens."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(WORKFLOW, "test_the_approver_context_survives_the_run_that_showed_it"),
            ),
            gaps=(
                (
                    "Until 2026-07-27 this criterion was unprovable by any tool. GitHub "
                    "renders the step summary in the run page, exposes it through no REST "
                    "endpoint, and hides it behind sign-in on the public page, so the only "
                    "available evidence was a person describing what they had read. The "
                    "compile job now uploads the same markdown as an artifact, copied from "
                    "the file the summary is written from rather than re-rendered, and the "
                    "cited test pins that upload."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The capture must come from a run that actually waited at a gate, and the "
                    "renderer's per-field unit tests are the other half. Worth stating so the "
                    "criterion is not read as more than it is: GitHub's approval notification "
                    "carries none of this content, so what can be checked is that the summary "
                    "exists and is complete, not that the reviewer read it."
                ),
            ),
        ),
        CriterionSpec(
            number="12",
            statement=(
                "Duplicate execution names do not create duplicate intent records."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(ADMISSION, "test_the_two_records_of_one_submission_are_keyed_the_same"),
                *_ids(
                    INFRA,
                    "test_every_lineage_write_is_conditional_and_lands_on_its_documented_key",
                ),
            ),
            gaps=(
                (
                    "Two independent mechanisms, both observed and neither captured. Step "
                    "Functions refused a second StartExecution under an existing execution "
                    "name with 400 ExecutionAlreadyExists on 2026-07-27. The S3 mechanism is "
                    "the conditional write: tools/probe_conditional_write.py established that "
                    "a second PutObject carrying IfNoneMatch star fails, and that Step "
                    "Functions surfaces it as S3.S3Exception. Either alone would suffice; "
                    "both are recorded because they fail differently and at different "
                    "moments."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "One property to carry into the capture rather than assume: "
                    "S3.S3Exception is the generic name for every unmodelled S3 error, so it "
                    "does not distinguish a genuine already-exists from a transient fault. "
                    "The 412 and its precondition message appear only in the Cause, which no "
                    "ErrorEquals can match, so RecordConflict means the write was refused "
                    "rather than that the key existed."
                ),
            ),
        ),
        CriterionSpec(
            number="13",
            statement="Edited manifests invalidate prior approvals.",
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    ADMISSION,
                    "test_a_manifest_that_does_not_hash_to_what_was_approved_is_refused",
                ),
                *_ids(ADMISSION, "test_a_manifest_swapped_for_another_after_approval_is_refused"),
                *_ids(
                    ADMISSION,
                    "test_the_hash_is_checked_before_any_fact_is_derived_from_the_manifest",
                ),
                *_ids(
                    HANDLER,
                    "test_a_manifest_that_does_not_hash_to_the_approved_value_is_refused",
                ),
            ),
            gaps=(
                (
                    "The core is proved thoroughly, ordering included. The hash is compared "
                    "before any fact is derived from the manifest, because an environment "
                    "gate releases a job rather than its content, and until the hash matches "
                    "the manifest is a document of unknown provenance. A mismatch outranks "
                    "every other finding the manifest would have produced."
                ),
                (
                    "The live half was observed. A probe execution carrying a deliberately "
                    "mismatched hash reached a terminal FAILED state with error "
                    "AdmissionRejected, and wrote a decision record reading "
                    "manifest_hash_mismatch with accepted false, so even the refusal is "
                    "attributable."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="14",
            statement=(
                "Admission failure does not create compute or partial accepted state."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    INFRA,
                    "test_admission_role_can_neither_stop_an_execution_nor_pass_a_role_nor_reach_s3",
                ),
                *_ids(
                    DENIALS,
                    "test_no_probe_can_launch_compute_or_start_anything_if_the_deny_were_missing",
                ),
            ),
            gaps=(
                (
                    "The record shape is proved. An intent record is written even for a "
                    "refused submission, nothing in an intent record marks a run as accepted, "
                    "and a payload that is not a manifest earns no records at all and fails "
                    "the execution instead."
                ),
                (
                    "The live denial matrix ran on 2026-07-27 and refused all six entries: "
                    "batch:SubmitJob, ec2:CreateKeyPair, s3:PutObject directly on the lineage "
                    "bucket, states:StartExecution on a different state machine, "
                    "states:StopExecution and iam:CreateRole. The workflow already uploads "
                    "the matrix as an admission-denials artifact, so the capture is a "
                    "download rather than a re-run."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The EC2 entry claims less than it looks like and the capture must say "
                    "so. ec2:RunInstances could not be made conclusive, because EC2 validates "
                    "image format, then looks the image up, and only then authorizes, so no "
                    "absent image reaches the question. The entry is ec2:CreateKeyPair, which "
                    "has no resource preconditions, and it establishes that the session is "
                    "refused EC2 mutation rather than that RunInstances specifically is "
                    "refused. The compute path this platform uses is Batch, and "
                    "batch:SubmitJob is conclusively denied beside it."
                ),
            ),
        ),
        CriterionSpec(
            number="15",
            statement=(
                "The environment reviewer lists match the roster in "
                "config/organization.yaml."
            ),
            status=CriterionStatus.GAP,
            gaps=(
                (
                    "Nothing in this repository reads the environment configuration, so this "
                    "criterion cites no test at all. The configuration exists and was set "
                    "deliberately. run-approval-lead lists the team-leads team as its single "
                    "reviewer, because eight leads exceed the six-slot cap and a team counts "
                    "as one slot. run-approval-admin lists the two roster admins rather than "
                    "the three GitHub org owners, because the third is the sandbox owner and "
                    "appears nowhere in this platform's role model."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The comparison to write is between the captured reviewer lists and the "
                    "loaded OrganizationInventory. Drift between the two is otherwise silent, "
                    "and the whole authorization model assumes they agree."
                ),
            ),
        ),
        CriterionSpec(
            number="16",
            statement=(
                "Both environments restrict deployment to main only, using the "
                "custom-branch form."
            ),
            status=CriterionStatus.GAP,
            gaps=(
                (
                    "Configured and unread. Both environments carry a deployment branch "
                    "policy with protected_branches false and custom_branch_policies true, "
                    "and a single branch policy naming main literally."
                ),
                (
                    "The custom form was chosen rather than the protected-branches form, "
                    "because the latter silently widens the moment a second branch is "
                    "protected, which is exactly the kind of change nobody would connect to "
                    "this control. The capture must assert the form specifically and not "
                    "merely that some branch restriction exists."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
            ),
        ),
        CriterionSpec(
            number="17",
            statement=(
                "The intent and decision records are schema-valid and join by run ID."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(ADMISSION, "test_the_two_records_of_one_submission_are_keyed_the_same"),
                *_ids(HANDLER, "test_the_records_are_mappings_rather_than_strings"),
            ),
            gaps=(
                (
                    "One of the three criteria with no live component, and still a gap, "
                    "because what it asks is that the committed captures validate against the "
                    "same models the Lambda uses. The models and their invariants are tested; "
                    "the captures do not exist. Four intent and decision pairs are in the "
                    "lineage bucket today, from an accepted routine run, an accepted admin "
                    "exception, a refused tampered hash, and an earlier accepted run."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "That earlier run is worth capturing rather than quietly skipping. "
                    "Records written before the encoding fix are stored double-encoded, as a "
                    "JSON string rather than an object, because the S3 SDK integration "
                    "JSON-encodes whatever the Body path yields and the handler was returning "
                    "canonical strings. Records written after are byte-identical to "
                    "canonical_json_bytes, verified by reading one back. A capture that "
                    "silently omitted the older shape would make the store look more uniform "
                    "than it is."
                ),
            ),
        ),
        CriterionSpec(
            number="18",
            statement=(
                "Each written object carries an S3-attested ChecksumSHA256 and a VersionId."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    INFRA,
                    "test_every_lineage_write_is_conditional_and_lands_on_its_documented_key",
                ),
            ),
            gaps=(
                (
                    "The write parameters are pinned in the template, with ChecksumAlgorithm "
                    "SHA256 and IfNoneMatch on every lineage write, and HeadObject with "
                    "checksum mode enabled returned both a ChecksumSHA256 and a VersionId for "
                    "every object in the bucket on 2026-07-27."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The capture must keep the two digests apart and say which is which. "
                    "ChecksumSHA256 attests the object's bytes; manifest_sha256 attests the "
                    "manifest's canonical serialization. They answer different questions, and "
                    "conflating them would be a lineage error rather than a wording slip."
                ),
            ),
        ),
        CriterionSpec(
            number="19",
            statement=(
                "The admission role holds no S3 permission, and the Lambda role holds "
                "none either."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    INFRA,
                    "test_admission_role_can_neither_stop_an_execution_nor_pass_a_role_nor_reach_s3",
                ),
                *_ids(INFRA, "test_lambda_role_holds_no_s3_action_whatsoever"),
                *_ids(
                    INFRA,
                    "test_service_roles_are_bounded_and_trusted_only_by_their_own_aws_service",
                ),
            ),
            gaps=(
                (
                    "The property is asserted against the templates and holds there: the "
                    "deciding component cannot write and the writing component cannot decide."
                ),
                TEMPLATE_NOT_CAPTURE,
                (
                    "Both roles were read back from IAM by hand on 2026-07-27 and matched. "
                    "The Lambda role carries CloudWatch Logs on its own log group and nothing "
                    "else, with no S3 action of any kind. Reading a role by hand is not a "
                    "comparison any test re-runs, so closing this means adding the three "
                    "Phase 2 roles to a Phase 2 capture and comparing them in both "
                    "directions, as Phase 1 does for its two."
                ),
            ),
        ),
        CriterionSpec(
            number="20",
            statement="The Lambda evaluates deployed policy, not caller-supplied policy.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                *_ids(
                    HANDLER,
                    "test_the_decision_cites_the_policy_on_disk_not_anything_in_the_event",
                ),
                *_ids(
                    ADMISSION,
                    "test_a_submission_cannot_smuggle_a_policy_version_past_the_deployed_one",
                ),
                *_ids(
                    ADMISSION,
                    "test_the_decision_cites_the_policy_version_aws_deployed",
                    "v1",
                    "v2",
                    "v41",
                ),
            ),
            supporting_node_ids=(
                *_ids(PACKAGE, "test_the_configuration_lands_where_the_handler_looks_for_it"),
            ),
            scope_limits=(
                (
                    "Two halves, and both are readable from committed artifacts, which is why "
                    "this is covered while its neighbours are not. The handler resolves its "
                    "configuration relative to its own file and the packaging tool copies the "
                    "config yaml files to exactly that location, so the policy the function "
                    "reads is the packaged one. And the admission core ignores any policy in "
                    "its input payload: an event carrying an attacker-supplied policy_version "
                    "produces a decision citing the deployed one."
                ),
                (
                    "That is what makes policy_version in a decision record a fact about the "
                    "platform rather than a claim by the caller."
                ),
                (
                    "Not claimed: that the deployed function is running the package this "
                    "repository last built. The code object is pinned by S3ObjectVersion, so "
                    "a template edit is what releases a change, but nothing compares the "
                    "running function's code to a locally built zip. A capture recording the "
                    "deployed S3ObjectVersion beside the build's sha256 would close that, and "
                    "it is not part of this criterion as stated."
                ),
            ),
        ),
        CriterionSpec(
            number="21",
            statement=(
                "The decision record carries the GitHub actor, the manifest hash, the "
                "policy version, the decision and the run ID."
            ),
            status=CriterionStatus.GAP,
            supporting_node_ids=(
                *_ids(
                    RECORDS,
                    "test_an_accepted_decision_must_carry_a_granted_authorization",
                    "no-authorization",
                    "refused-authorization",
                ),
                *_ids(
                    ADMISSION,
                    "test_the_decision_cites_the_policy_version_aws_deployed",
                    "v1",
                ),
            ),
            gaps=(
                (
                    "The master plan names these five explicitly, so a record missing one is "
                    "a gate failure rather than a cosmetic gap. The model enforces the shape "
                    "and the core populates it. What no test does is open a committed capture "
                    "and assert each of the five is present and non-empty."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The actor is the field to read carefully. The decision record's approver "
                    "reaches AWS because the submitting job read it from the GitHub approvals "
                    "API and passed it along; no OIDC claim names who approved. The gate "
                    "cannot be skipped, and the approver can be misreported by a compromised "
                    "runner. This criterion asserts the field is recorded, not that AWS "
                    "verified it."
                ),
            ),
        ),
        CriterionSpec(
            number="22",
            statement=(
                "No repository-level secret exists, and any credential is an environment "
                "secret with a main-only policy."
            ),
            status=CriterionStatus.GAP,
            gaps=(
                (
                    "Satisfied in fact and unread by anything. The repository holds zero "
                    "secrets at repository, organization and Dependabot level, and three "
                    "non-secret variables: AWS_REGION, AWS_INFRA_DEPLOYER_ROLE_ARN and "
                    "AWS_ADMISSION_ROLE_ARN."
                ),
                (
                    "Phase 2 introduced no credential. The approvals endpoint turned out to "
                    "be reachable with a GITHUB_TOKEN holding actions read, verified on a "
                    "real run, so the fallback of a stored fine-grained token was never "
                    "needed."
                ),
                NEEDS_A_COMMITTED_CAPTURE,
                (
                    "The capture records secret names only, never values, at repository, "
                    "organization and environment level. This check starts satisfied and "
                    "exists to keep it that way."
                ),
            ),
        ),
    )
    if len(specs) != PHASE2_CRITERION_COUNT:
        raise AssertionError(
            f"Phase 2 has {PHASE2_CRITERION_COUNT} acceptance criteria; the definition "
            f"lists {len(specs)}"
        )
    validate_criterion_specs(specs)
    return specs
