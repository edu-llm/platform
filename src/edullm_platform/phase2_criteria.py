"""The Phase 2 acceptance criteria and the tests that are cited for each one.

Phase 2 authorizes one run manifest behind a reviewed GitHub environment, assumes a
bounded AWS role whose trust requires that gate, executes Step Functions admission, and
writes one immutable intent record and one immutable decision record to S3. This module
records the twenty-two checks the phase must satisfy, against the contract in
``edullm_platform.criteria``.

**Eight of the twenty-two are gaps today, and the reason is worth being exact about.** The
path ran. On 2026-07-27 a submission went through the lead gate and produced records; an
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
intentionally not enforced while no member is bound to a team, exactly as in Phase 0.
Its trigger is a configuration change rather than a code change, which is the point of
having recorded it.

**The residual this phase cannot close by testing harder.** The OIDC token proves that an
approval happened and which gate it passed; it carries no claim naming the approver. The
identity in a decision record reaches AWS because the submitting job read it from the
GitHub API and passed it along. Criterion 21 states what the record carries; it does not
claim AWS verified it.

**Nineteen of the twenty-two are pilot-blocking, and eight of those nineteen had no
counterpart in the master plan's coarser list.** The plan resolves this phase into
fourteen checks and marks eleven of them; those fourteen are criteria 1 to 14 here, in
order, so that half of the split is transcription rather than judgement. The three the
plan does not mark are the interim-behaviour happy path, whose failure leaves members
stuck rather than harmed; the deferred wrong-team check, which the shared contract refuses
to let anybody mark at all; and the approver display, which the plan argues at length and
which is load-bearing for the team rung rather than the pilot one.

Criteria 15 to 22 are the eight the plan's list never reached, and every one of them is
pilot-blocking. That is a high proportion and it is the honest answer for this phase
rather than a lazy one: authorization is where the money and the attribution live, and
what these eight add to the plan's fourteen is the reviewer roster, the branch policy on
both gates, the shape and joinability of the lineage records, the attestation on the
objects that hold them, the separation of the deciding component from the writing one, the
refusal to evaluate a caller-supplied policy, the five fields a decision has to carry, and
the absence of any secret a branch could read. Each one's ``scope_limits`` records what
its absence would cost, in the ladder's own terms.
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
GITHUB = "tests/test_phase2_github_evidence.py"
LINEAGE = "tests/test_phase2_lineage_evidence.py"
PACKAGE = "tests/test_phase2_lambda_package.py"
AUTHZ = "tests/test_authorization.py"

#: What closes a criterion resting on a run that happened and was never captured. Written
#: once because it is the same sentence five times, and a reader who has met it once
#: should not have to check whether the fifth wording differs. ``gaps`` is a tuple the
#: gate joins with a space, so this is a sibling element rather than a concatenation.
#:
#: It used to say that no Phase 2 capture existed at all, which was true when it was
#: written and stopped being true the same day. The correction matters more than the
#: wording: a criterion that reads "nothing is captured" beside ten criteria covered on
#: captures tells a reader the opposite of what the definition records, and the artifact
#: each of these is actually short of is narrower and harder to find than "a capture".
#:
#: Two criteria carried this and should not have. Criterion 3 waits on a second lead
#: rather than on an artifact and still says so in its own words. Criterion 9 waited on a
#: capture this tool could have taken and had not; on 2026-07-31 it was taken, and that
#: criterion is now covered on it. Five criteria still carry the sentence below, and each
#: of them waits on somebody doing to its artifact what was done to that one.
NEEDS_A_COMMITTED_CAPTURE: Final = (
    "The capture that exists does not reach this. tools/capture_phase2_evidence.py "
    "records the GitHub environment and secret configuration, the Step Functions "
    "execution list and the lineage store's objects, and tests read all three from "
    "fixtures/evidence/phase-2/. None of them is the artifact named here, so no cited "
    "test opens evidence of it. Closing this means capturing that artifact, sanitizing "
    "it through the existing SecretFreeStr and account-id redaction machinery, "
    "committing it beside the others, and citing a test that reads it."
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
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(LINEAGE, "test_an_accepted_routine_run_was_released_by_the_lead_gate"),
            ),
            supporting_node_ids=(
                *_ids(ADMISSION, "test_a_correct_submission_through_the_right_gate_is_admitted"),
                *_ids(HANDLER, "test_the_handler_admits_a_routine_submission_a_lead_released"),
                *_ids(LINEAGE, "test_the_manifest_in_every_intent_still_hashes_to_its_recorded_value"),
            ),
            scope_limits=(
                (
                    "A lead submitted and released their own run through the browser on "
                    "2026-07-27, and the captured decision record reads routine_self_authorized "
                    "under policy v1 with the authorization granted. The core tests prove the same "
                    "outcome from the same inputs; the capture proves a run happened."
                ),
                (
                    "Rests on a capture, so it expires with the freshness window and is a statement "
                    "about one run rather than about the next one."
                ),
            ),
        ),
        CriterionSpec(
            number="2",
            statement="Member submission without lead approval is rejected.",
            status=CriterionStatus.GAP,
            pilot_blocking=True,
            supporting_node_ids=(
                *_ids(WORKFLOW, "test_the_three_jobs_carry_exactly_these_permission_maps"),
                *_ids(WORKFLOW, "test_the_compile_job_cannot_request_a_token_by_any_spelling"),
                *_ids(
                    ADMISSION,
                    "test_a_submission_its_approver_may_not_release_is_refused",
                    "GMatherne-nzhao721-approver_lacks_lead_or_admin_role",
                    "GMatherne-None-self_approval_not_permitted_for_member",
                    "not-a-member-ericrcwu001-submitter_not_in_roster",
                    "GMatherne-not-a-member-approver_not_in_roster",
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
            scope_limits=(
                (
                    "Not pilot-blocking, and this is the plan's own call rather than one taken "
                    "here. Everything else in the authorization group fails towards permitting "
                    "something that should have been refused; this one fails towards refusing "
                    "something that should have been permitted. Nobody loses money, data, "
                    "attribution or a lineage record when it breaks -- a member is stuck, and "
                    "a stuck member complains, which is the loudest failure mode in the phase."
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
                (
                    "What this waits on is a second person rather than a missing capture, and "
                    "it is the only criterion in the phase where that is true. The tooling is "
                    "already there: tools/capture_phase2_evidence.py records the lineage "
                    "store's objects, the decision record names the approver, and criterion 1 "
                    "is covered on exactly that reading of a run the submitter released. So "
                    "the day a lead other than the submitter releases one routine submission, "
                    "closing this is the ordinary re-capture and a citation -- and no amount "
                    "of work in this repository brings that day forward."
                ),
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
                "No team in config/organization.yaml records a member_logins entry, so no "
                "submitter's membership is knowable and the rule has nobody to reject. Every "
                "decision records team_verified false in consequence, which is what makes the "
                "unverified attribution visible in the audit trail rather than silent. The "
                "teams themselves are declared; it is who is in them that nothing has ever "
                "recorded. Carried forward from Phase 0's deferral of the same question."
            ),
            deferral_trigger=(
                "Recording member_logins in config/organization.yaml once each group's "
                "assignments exist. Enforcement goes live with no code change, "
                "team_verified starts reporting true, and this must be re-recorded as "
                "covered or argued again."
            ),
            scope_limits=(
                (
                    "This one cannot be marked pilot-blocking, and the shared contract refuses "
                    "the combination rather than leaving it to a reviewer. A deferral is a "
                    "decision that the criterion is intentionally false right now, so requiring "
                    "it before a pilot would make the rung unreachable rather than make it "
                    "safe. What it would have protected went on the limitations page instead, "
                    "in the words a user needs: the team recorded against a run is unverified, "
                    "and nothing stops somebody naming a team they do not belong to.\n\n"
                    "THE PAGE THAT EXCHANGE NAMED IS GONE, AND THE PROTECTION SURVIVED IT "
                    "SOMEWHERE ELSE. On 2026-07-31 the page was taken out of the README and "
                    "moved to a local, gitignored document, and for the hours between that "
                    "and the repair the sentence above was the whole of the record, where no "
                    "pilot user could reach it. It is reachable again: the same fact, in the "
                    "same words, is printed on the summary every accepted submission ends on "
                    "-- naming a team you are not on is a mis-routed request rather than a "
                    "refused one -- and Phase 5 criterion 11 is what holds it there, cited to "
                    "a test rather than to a page. So this deferral is paid for in substance "
                    "while its own text still points at something that no longer exists. It "
                    "is owed the re-cut Phase 5's equivalent deferral had the same day: cite "
                    "the summary and the test that guards it."
                ),
            ),
        ),
        CriterionSpec(
            number="5",
            statement="Admin exception succeeds only through the admin path.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(LINEAGE, "test_an_accepted_exception_was_released_by_the_admin_gate_and_priced"),
                *_ids(ADMISSION, "test_an_exception_released_by_the_lead_gate_is_refused"),
                *_ids(ADMISSION, "test_a_routine_submission_released_by_the_admin_gate_is_refused"),
            ),
            supporting_node_ids=(
                *_ids(INFRA, "test_admission_subject_condition_is_a_two_element_array_of_environment_subjects"),
                *_ids(GITHUB, "test_the_admin_gate_is_reviewed_by_the_roster_admins_and_nobody_else"),
            ),
            scope_limits=(
                (
                    "An exception priced at 567.20 dollars was observed classifying as an exception "
                    "and routing to run-approval-admin, whose reviewers are the two roster admins and "
                    "not the leads team, and the captured decision records that gate."
                ),
                (
                    "Both directions are proved in the core rather than only the one that happened: an "
                    "exception released by the lead gate is refused, and a routine submission released "
                    "by the admin gate is refused. Without the second, a gate that released everything "
                    "would still pass the first."
                ),
            ),
        ),
        CriterionSpec(
            number="6",
            statement=(
                "Wrong repository, ref, audience, or manifest hash cannot assume or use "
                "the role."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
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
                    "The manifest-hash half is settled: criterion 13 is covered, on the "
                    "committed decision records of two runs whose tampered hash was refused. "
                    "Ref is the live half, and criterion 7 carries it -- itself a gap, for "
                    "want of a CloudTrail capture. Minting a token from another repository is "
                    "not something this project can arrange, so the repository and audience "
                    "conditions will close on a deployed-role comparison rather than on an "
                    "attempt."
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
            pilot_blocking=True,
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
                "A dispatched run obtains nothing that can start, submit or write before a "
                "reviewer approves."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(WORKFLOW, "test_the_compile_job_cannot_request_a_token_by_any_spelling"),
                *_ids(WORKFLOW, "test_the_three_jobs_carry_exactly_these_permission_maps"),
            ),
            supporting_node_ids=(
                *_ids(
                    WORKFLOW,
                    "test_the_submit_job_takes_its_gate_from_needs_and_never_from_the_form",
                ),
                *_ids(
                    WORKFLOW,
                    "test_the_only_aws_a_dispatch_reaches_before_an_approval_is_a_read_and_a_refusal",
                ),
            ),
            scope_limits=(
                (
                    "THE STATEMENT WAS REWRITTEN RATHER THAN SCOPED, AND THE OLD ONE IS HERE "
                    "SO THE CHANGE IS LEGIBLE. It read: 'A dispatched run obtains no OIDC "
                    "token, credential, or secret before a reviewer approves.' That was "
                    "already false when it was written -- deny-unapproved holds id-token: "
                    "write and mints a token on every dispatch, spending it to prove the "
                    "admission role refuses a subject with no environment on it, where the "
                    "refusal is the check. Adding a resolve job that assumes "
                    "sbsandbox-intern-edullm-image-resolver made it false a second way. "
                    "Keeping the sentence and explaining underneath that it is narrower than "
                    "it reads would be a criterion whose scope limit contradicts its "
                    "statement, which is the shape the three-status rule exists to prevent. "
                    "The property that is true, tested and worth having is the one now "
                    "stated: what a dispatch can reach before approval starts nothing, "
                    "submits nothing and writes nothing -- no admission role, no state "
                    "machine, no queue, no lineage. Same move as Phase 5 item 5.5 makes on "
                    "Phase 4 criterion 7, and for the same reason."
                ),
                (
                    "The two pre-gate jobs, named so the statement can be checked against "
                    "them. deny-unapproved mints a token and is refused. resolve assumes a "
                    "role holding exactly ecr:DescribeImages and ecr:DescribeImageScanFindings, "
                    "which is how the image a commit published and its scan findings reach a "
                    "job that holds no credential of its own. infra/iam/image-resolver-role.yaml "
                    "argues that in full, and tests/test_phase5_infrastructure.py holds the "
                    "grant to exactly those two read actions."
                ),
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
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(
                    AUTHZ,
                    "test_case_variants_of_a_member_login_are_recognized_as_self_approval",
                    "caiiris-CAIIRIS",
                    "CAIIRIS-caiiris",
                    "CaIiRiS-caIIris",
                ),
                *_ids(GITHUB, "test_no_member_who_is_not_a_lead_or_admin_reviews_either_gate"),
                *_ids(
                    GITHUB,
                    "test_the_membership_captured_is_of_the_team_the_lead_gate_actually_names",
                ),
                *_ids(
                    GITHUB,
                    "test_only_a_lead_the_roster_declares_can_release_a_run_at_the_lead_gate",
                ),
                *_ids(GITHUB, "test_no_environment_lets_an_admin_release_without_a_reviewer"),
            ),
            supporting_node_ids=(
                *_ids(AUTHZ, "test_plain_member_routine_run_approved_by_another_plain_member_is_denied"),
                *_ids(
                    GITHUB,
                    "test_a_lead_the_roster_declares_is_never_locked_out_of_the_lead_gate",
                ),
            ),
            scope_limits=(
                (
                    "Enforced twice, and as of 2026-07-31 proved twice. evaluate_authorization "
                    "returns self_approval_not_permitted_for_member against the shipped "
                    "roster, which is the mechanism that holds regardless of GitHub's "
                    "configuration. The second mechanism is that members are not reviewers on "
                    "either environment, and that half was proved only for reviewers named as "
                    "users until the capture below existed."
                ),
                (
                    "What was missing was the membership of one team, and it is now captured. "
                    "test_no_member_who_is_not_a_lead_or_admin_reviews_either_gate establishes "
                    "that every reviewer named as a user on either gate is a lead or an admin "
                    "in config/organization.yaml. The lead gate names no users at all: its "
                    "single reviewer is the team-leads team, because eight leads exceed the "
                    "six reviewer slots and a team counts as one. Nothing recorded who was in "
                    "that team, so a member added to it on GitHub became a reviewer on the "
                    "lead gate and every test went on passing. "
                    "fixtures/evidence/phase-2/github/lead-team.sanitized.json is that record, "
                    "and the comparison against the roster is what now fails by name instead."
                ),
                (
                    "WHICH OF THOSE CITATIONS PROVE THE STATEMENT AND WHICH SUPPORT IT WAS "
                    "SETTLED BY MUTATING THE CAPTURE RATHER THAN BY READING IT. Point "
                    "team_slug at some other team and both roster comparisons stay green -- "
                    "they compare a list of logins against a list of logins, and any team of "
                    "leads satisfies them -- while only "
                    "test_the_membership_captured_is_of_the_team_the_lead_gate_actually_names "
                    "fails. That test is what ties the membership to this gate, so without it "
                    "the other two are about a team rather than about the lead gate, which is "
                    "why it is proving here rather than cited beside the proof. "
                    "test_a_lead_the_roster_declares_is_never_locked_out_of_the_lead_gate is "
                    "the one that stays supporting, and the reason is the statement rather "
                    "than the test's strength: it establishes that the two lists agree, which "
                    "is what licenses reading roster membership as gate membership, and it "
                    "bears on whether a lead can be kept out rather than on whether a member "
                    "can get in. Empty the captured membership and it is the only citation "
                    "that fails, which is the right result for this criterion: a lead gate "
                    "with nobody behind it releases nothing."
                ),
                (
                    "Note that prevent_self_review is deliberately false on both "
                    "environments, because leads self-authorizing and admins approving their "
                    "own exceptions are both intended. The flag is not what enforces this, "
                    "and a reader must not be left thinking it is."
                ),
                (
                    "THE OTHER FLAG IS WHAT THIS PROOF QUIETLY RESTS ON, SO IT IS CITED HERE "
                    "AND NOT ONLY WHERE IT WAS FIRST WRITTEN DOWN. Every citation above bounds "
                    "who may be asked to review. With can_admins_bypass true a repository "
                    "admin releases a waiting deployment through Start all waiting jobs "
                    "without being a reviewer at all and without leaving an approval record, "
                    "so the reviewer lists would be an accurate description of a control "
                    "nobody has to pass -- and GitHub grants repository admin in repository "
                    "settings, independently of the admins list in config/organization.yaml, "
                    "so nothing in the roster bounds who holds it. The capture records the "
                    "flag false on both environments and "
                    "test_no_environment_lets_an_admin_release_without_a_reviewer pins it. If "
                    "somebody turns it on, what still holds is the authorization half: the "
                    "bypass leaves no approver for the submitting job to read, so admission "
                    "refuses the run rather than attributing it to nobody."
                ),
                (
                    "How it was closed, since the previous entry here was a remedy rather "
                    "than a record. tools/capture_phase2_evidence.py gained a lead-team "
                    "target reading orgs/edu-llm/teams/team-leads/members, which writes the "
                    "team's slug beside its logins so that a capture of some other team "
                    "cannot be read as this one; a test pins that slug against the reviewer "
                    "the lead gate actually names. The roster comparison runs in both "
                    "directions as two tests rather than one, because the directions are "
                    "different incidents with different fixes: a login on GitHub and not in "
                    "the roster opens a gate admission will then refuse, and a login in the "
                    "roster and not on GitHub is a lead the gate will never release, even for "
                    "his own group's run. Both were live at once through the two-day window "
                    "that ended on 2026-07-30, which config/organization.yaml records."
                ),
                (
                    "What a capture cannot do is notice a change while nobody is looking. "
                    "This criterion rests on two captures rather than one and they were taken "
                    "four days apart -- the environments on 2026-07-27 and the team membership "
                    "on 2026-07-31 -- so it is a statement about two observed_at values, and "
                    "the earlier one governs: each expires on its own freshness window, the "
                    "environment capture lapses first, and the proof lapses with it rather "
                    "than surviving on the newer record. In the interval between two captures "
                    "a member added to the team-leads team is a reviewer on the lead gate and "
                    "no test here fails. "
                    "What stands behind the gate in that interval is unchanged: "
                    "evaluate_authorization refuses the submission after the approval, so the "
                    "run does not start, and the residual is that somebody held an approval "
                    "authority nobody granted for as long as it took to re-capture."
                ),
            ),
        ),
        CriterionSpec(
            number="10",
            statement=(
                "A submitter cannot influence their own classification to route to the "
                "weaker approval path."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
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
            scope_limits=(
                (
                    "Not pilot-blocking, and it is the closest call in the phase. Its absence "
                    "plainly lets somebody release a run whose cost they misjudged, which is "
                    "money. What saves it is the size of the rung: a pilot approver is one of "
                    "two or three named people who already know what was submitted and can "
                    "work the figure out once told the screen does not show it, which is a "
                    "limitation a reader can act on. The argument expires the moment the "
                    "approver stops being somebody who already knows the run, so this is "
                    "load-bearing for the team rung while not blocking the pilot one -- the "
                    "only entry in the phase where those two answers differ."
                ),
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
                (
                    "One field this criterion asks for is not there and cannot be. Reading a "
                    "real rendered context on 2026-07-27 accounts for every item in the "
                    "statement except the branch: submitter, team, linked repository, linked "
                    "short SHA, image digest, dataset release, compute profile with its "
                    "hourly rate, the arithmetic shown as rate times nodes times hours times "
                    "attempts times cells, and the classification as the first line of the "
                    "document. For an exception the renderer also names each ceiling that was "
                    "exceeded, in words, with the value beside the limit."
                ),
                (
                    "The branch is absent because RunManifest has no branch field and the "
                    "dispatch form never collects one. That follows from the global "
                    "constraint that every source revision uses a full commit SHA: a branch "
                    "is mutable and a commit is not, so the branch is not part of run "
                    "identity. It is still context an approver would use, and this criterion "
                    "inherited its wording from a draft written before the manifest settled. "
                    "Closing it means either carrying the branch as advisory metadata that "
                    "nothing authorizes on, or amending the criterion with that reason "
                    "written down. It is a decision rather than an omission, and until "
                    "somebody takes it this criterion cannot be honestly marked covered."
                ),
            ),
        ),
        CriterionSpec(
            number="12",
            statement=(
                "Duplicate execution names do not create duplicate intent records."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
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
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(LINEAGE, "test_a_refused_submission_still_earns_an_attributable_decision"),
            ),
            supporting_node_ids=(
                *_ids(ADMISSION, "test_a_manifest_that_does_not_hash_to_what_was_approved_is_refused"),
                *_ids(ADMISSION, "test_the_hash_is_checked_before_any_fact_is_derived_from_the_manifest"),
                *_ids(HANDLER, "test_a_manifest_that_does_not_hash_to_the_approved_value_is_refused"),
            ),
            scope_limits=(
                (
                    "Two probe executions carrying a deliberately mismatched hash reached a terminal "
                    "FAILED state with error AdmissionRejected, and each wrote a decision record reading "
                    "manifest_hash_mismatch with accepted false. A refusal that left no record would "
                    "make a rejected submission indistinguishable from one nobody made."
                ),
                (
                    "The ordering is what the core tests add. The hash is compared before any fact is "
                    "derived from the manifest, because an environment gate releases a job rather than "
                    "its content, so until the hash matches the manifest is a document of unknown "
                    "provenance and a mismatch outranks every other finding."
                ),
            ),
        ),
        CriterionSpec(
            number="14",
            statement=(
                "Admission failure does not create compute or partial accepted state."
            ),
            status=CriterionStatus.GAP,
            pilot_blocking=True,
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
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(GITHUB, "test_the_admin_gate_is_reviewed_by_the_roster_admins_and_nobody_else"),
                *_ids(GITHUB, "test_no_member_who_is_not_a_lead_or_admin_reviews_either_gate"),
                *_ids(
                    GITHUB,
                    "test_the_lead_gate_is_reviewed_by_the_leads_team_rather_than_by_named_people",
                ),
                *_ids(
                    GITHUB,
                    "test_the_membership_captured_is_of_the_team_the_lead_gate_actually_names",
                ),
                *_ids(GITHUB, "test_only_a_lead_the_roster_declares_can_release_a_run_at_the_lead_gate"),
                *_ids(GITHUB, "test_a_lead_the_roster_declares_is_never_locked_out_of_the_lead_gate"),
            ),
            supporting_node_ids=(
                *_ids(GITHUB, "test_both_approval_environments_exist_and_no_third_one_does"),
                *_ids(GITHUB, "test_self_review_is_deliberately_permitted_on_both_gates"),
            ),
            scope_limits=(
                (
                    "THIS WAS OVERCLAIMED UNTIL 2026-07-31, AND THE THREE CITATIONS ADDED THAT "
                    "DAY ARE WHAT MAKE THE STATEMENT TRUE RATHER THAN MERELY ASSERTED. The "
                    "statement is that the reviewer lists match the roster. One gate's reviewer "
                    "is a team, so its effective reviewer list is that team's membership -- and "
                    "nothing recorded who was in it. Criterion 9 reported that hole honestly and "
                    "this one, resting on the same capture, went green over it. Whoever reads "
                    "this next should take the pairing as the lesson: two criteria sharing "
                    "evidence can disagree about what the evidence proves, and the optimistic "
                    "one is not the one to trust."
                ),
                (
                    "Those three are recorded as proving, and for the first day they existed "
                    "they were not, which made this paragraph disagree with the table above "
                    "it: the bundle renders a supporting citation as evidence cited rather "
                    "than as proof, so it printed the three tests that close the overclaim as "
                    "not amounting to one. For a gate whose reviewer is a team, the statement "
                    "that the reviewer lists match the roster is exactly the two directions "
                    "plus the pin that says which team was captured. Nothing else here "
                    "reaches the lead gate's effective reviewer list at all."
                ),
                (
                    "The membership is compared through a capture of its own rather than by "
                    "flattening the reviewer list, which is what keeps the paragraph below true. "
                    "A reviewer test that expanded the team into its members would still pass "
                    "after somebody replaced the team with six named users, because the expanded "
                    "set would go on matching. Two records answering two questions -- who is "
                    "listed, and who that listing resolves to -- cannot collapse that way."
                ),
                (
                    "Pilot-blocking, and it has no counterpart in the master plan's list. The "
                    "plan's checks describe what the approval gate refuses; this one describes "
                    "who is standing at it. If GitHub's reviewer lists drift from the roster "
                    "the platform reasons about, a person the policy has no model of releases "
                    "spend and the decision record attributes it to somebody the model cannot "
                    "place. That is money and attribution in one, and no limitations page "
                    "helps: a pilot user cannot act on being told the reviewer list is "
                    "unchecked."
                ),
                (
                    "Compared against config/organization.yaml rather than against a list "
                    "written in the test, because drift between GitHub's reviewers and the "
                    "platform's roster is otherwise silent and the authorization model "
                    "assumes the two agree."
                ),
                (
                    "The lead gate's single reviewer is the team-leads team, and the "
                    "assertion pins the type as well as the name. Eight leads exceed the "
                    "six-slot cap and a team counts as one slot, so the team is the only way "
                    "to list them all -- and a test that flattened it into its members would "
                    "agree with the roster for the wrong reason, and would keep agreeing "
                    "after somebody replaced it with six named users."
                ),
                (
                    "The admin gate lists the two roster admins rather than the three GitHub "
                    "org owners. The third is the sandbox owner, who appears nowhere in this "
                    "platform's role model, and an exception released by somebody outside "
                    "the model would be attributable to a person the policy cannot reason "
                    "about."
                ),
                (
                    "This rests on two captures rather than one, and they were taken four "
                    "days apart: the environments on 2026-07-27 and the lead team's "
                    "membership on 2026-07-31. Each expires on its own freshness window and "
                    "the earlier one governs, so this is a statement about 2026-07-27 and it "
                    "lapses then rather than being carried by the newer record. A GitHub "
                    "setting changes in a browser in ten seconds and leaves no artifact in "
                    "any repository, which is exactly why the statement about one has to "
                    "lapse rather than stand."
                ),
            ),
        ),
        CriterionSpec(
            number="16",
            statement=(
                "Both environments restrict deployment to main only, using the "
                "custom-branch form."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(GITHUB, "test_every_environment_restricts_deployments_to_main_by_name"),
            ),
            supporting_node_ids=(
                *_ids(GITHUB, "test_both_approval_environments_exist_and_no_third_one_does"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it has no counterpart in the master plan's list "
                    "although the plan's Build section requires it. This is one of the two "
                    "escape routes the phase closes: off-main cannot reach the environment, "
                    "and skipping the environment cannot reach AWS. The plan's checks mark the "
                    "second and leave the first implicit, so it is marked here. Its absence "
                    "lets a workflow on any branch reach the approval environment and, through "
                    "it, the admission role, which is money."
                ),
                (
                    "The custom form is asserted specifically, and the protected-branches "
                    "form is asserted absent. They are not equivalent: protected_branches "
                    "follows whatever branch protection happens to cover, so it widens the "
                    "moment a second branch is protected -- a change nobody would connect to "
                    "this control -- while custom_branch_policies matches names that were "
                    "written down. A test asserting only that some restriction exists would "
                    "pass on the weaker one."
                ),
                (
                    "Asserted for every environment the capture found rather than for the "
                    "two expected by name, so an environment auto-created by naming it in a "
                    "workflow file is covered by the same assertion."
                ),
                (
                    "Rests on a capture, so it expires with the freshness window and is a "
                    "statement about 2026-07-27 rather than about now."
                ),
            ),
        ),
        CriterionSpec(
            number="17",
            statement=(
                "The intent and decision records are schema-valid and join by run ID."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(LINEAGE, "test_every_intent_and_decision_join_by_run_id"),
                *_ids(LINEAGE, "test_the_manifest_in_every_intent_still_hashes_to_its_recorded_value"),
            ),
            supporting_node_ids=(
                *_ids(LINEAGE, "test_records_written_after_the_encoding_fix_are_the_canonical_bytes"),
                *_ids(LINEAGE, "test_the_older_shape_is_recorded_rather_than_hidden"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it has no counterpart in the master plan's list. It is "
                    "the lineage record itself: records that do not load, or that do not join "
                    "by run id, are an audit trail nobody can audit. This is not hypothetical "
                    "here -- reading the real store found every decision record failing to "
                    "load, which is the exact shape the ladder marks, a record that looks fine "
                    "and is wrong."
                ),
                (
                    "Validated against the same models the Lambda used to write them, which is what "
                    "makes this more than a shape check: a record the writing model cannot read back is "
                    "an audit trail nobody can audit."
                ),
                (
                    "Reading the real records found exactly that. maximum_compute_cost_usd is a computed "
                    "field, so pydantic wrote it out and refused it on the way back in, and every "
                    "decision record in the store failed to load. CostInputs now accepts a recorded "
                    "total and refuses one that disagrees with the inputs beside it."
                ),
                (
                    "The store holds two shapes and both are captured. Records written before the "
                    "encoding fix are a JSON string containing the object; records after are the "
                    "canonical bytes. Hiding the older shape would make the store look uniform and "
                    "leave the next reader meeting a surprise nobody wrote down."
                ),
            ),
        ),
        CriterionSpec(
            number="18",
            statement=(
                "Each written object carries an S3-attested ChecksumSHA256 and a VersionId."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(LINEAGE, "test_every_stored_object_carries_a_checksum_and_a_version"),
            ),
            supporting_node_ids=(
                *_ids(LINEAGE, "test_the_object_checksum_is_not_the_manifest_hash"),
                *_ids(INFRA, "test_every_lineage_write_is_conditional_and_lands_on_its_documented_key"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and the closest of the eight calls the plan's list left to "
                    "be made here. The argument against is that the conditional write already "
                    "refuses an overwrite, so the version is a second line rather than the "
                    "first. What decides it is the ladder's other rule: a limitation only "
                    "substitutes for a check when a reader can act on it, and there is nothing "
                    "a pilot user can do with the sentence 'the objects holding your "
                    "authorization records are neither attested nor versioned'. An unattested "
                    "object that has been altered reads exactly like one that has not."
                ),
                (
                    "S3-attested rather than computed here. Every object in the store returned both a "
                    "ChecksumSHA256 and a VersionId from HeadObject with checksum mode enabled."
                ),
                (
                    "The two digests are asserted distinct. ChecksumSHA256 attests the object's bytes; "
                    "manifest_sha256 attests the manifest's canonical serialization and is the value an "
                    "approval was taken against. Conflating them would be a lineage error rather than a "
                    "wording slip, so a test asserts they differ."
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
            pilot_blocking=True,
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
            scope_limits=(
                (
                    "Pilot-blocking, and this is the criterion the master plan's own status "
                    "block names as having no counterpart in its check list at all. The "
                    "property is that the deciding component cannot write and the writing "
                    "component cannot decide. Lose it and the session that asked for a "
                    "submission can write its own decision record, which forges attribution "
                    "and corrupts the lineage store in the same act -- two of the four harms "
                    "at once, and both of them silent."
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
            pilot_blocking=True,
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
                    "Pilot-blocking, and it has no counterpart in the master plan's list "
                    "although the plan's Build section requires it. A caller who can supply "
                    "the policy the decision is taken against can supply one with no ceilings "
                    "in it, which routes around every cost bound the phase has. That is the "
                    "money harm in its most direct form, and it leaves a decision record that "
                    "looks properly authorized."
                ),
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
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(LINEAGE, "test_every_decision_carries_the_five_fields_the_master_plan_names"),
            ),
            supporting_node_ids=(
                *_ids(RECORDS, "test_an_accepted_decision_must_carry_a_granted_authorization", "no-authorization", "refused-authorization"),
                *_ids(ADMISSION, "test_the_decision_cites_the_policy_version_aws_deployed", "v1"),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it has no counterpart in the master plan's list "
                    "although the plan's Build section requires exactly these five fields. A "
                    "decision missing any of them is a run nobody can attribute afterwards, "
                    "which is the attribution harm stated as plainly as the ladder states it."
                ),
                (
                    "Read from committed records rather than from the model's declaration, because the "
                    "master plan names these five explicitly and a record missing one is a gate failure."
                ),
                (
                    "The actor is the field to read carefully. The approver reaches AWS because the "
                    "submitting job read it from the GitHub approvals API and passed it along; no OIDC "
                    "claim names who approved. The gate cannot be skipped, and a compromised runner "
                    "could still misreport who released it. This asserts the field is recorded, not "
                    "that AWS verified it."
                ),
            ),
        ),
        CriterionSpec(
            number="22",
            statement=(
                "No repository-level secret exists, and any credential is an environment "
                "secret with a main-only policy."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(GITHUB, "test_the_repository_holds_no_secret_a_branch_could_read"),
                *_ids(GITHUB, "test_phase_two_introduced_no_credential_at_all"),
            ),
            supporting_node_ids=(
                *_ids(
                    GITHUB,
                    "test_the_only_repository_variables_are_the_two_role_arns_and_the_region",
                ),
            ),
            scope_limits=(
                (
                    "Pilot-blocking, and it is the one of the eight that looks least like a "
                    "check because it starts satisfied. A repository-level secret is readable "
                    "by a workflow on any branch, so the moment one exists the branch "
                    "protections and the environment gate are both walked around, and what "
                    "leaks is a credential. A check that guards a state you are already in is "
                    "exactly the kind whose absence is invisible until it is expensive."
                ),
                (
                    "Names only, never values, and the model has no field a value could "
                    "occupy. That is a stronger guarantee than a capture tool that is "
                    "careful, and it matters because the evidence for "
                    "no-credentials-are-stored must not itself store one."
                ),
                (
                    "Phase 2 introduced no credential, and that was a live question rather "
                    "than a foregone conclusion. The fallback, had the approvals endpoint "
                    "needed a fine-grained token, was to store one as an environment secret. "
                    "The endpoint answered a GITHUB_TOKEN holding actions read, so nothing "
                    "was stored, and the environment secret lists are empty."
                ),
                (
                    "This check starts satisfied and exists to keep it that way, so it will "
                    "look uneventful for as long as it is working."
                ),
                (
                    "Rests on a capture, so it expires with the freshness window."
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
