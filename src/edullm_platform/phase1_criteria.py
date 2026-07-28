"""The Phase 1 acceptance criteria and the tests that are cited for each one.

Phase 1 takes one clean, pushed research-branch commit through shared CI and produces
one immutable ECR image digest. This module records the eight checks the phase must
satisfy, against the contract in ``edullm_platform.criteria``.

All eight are covered, and four of them were gaps until the build path ran. What closed
them is not new machinery: OLMo-core gained a caller workflow and the registered
Dockerfile, the publish workflow completed against a real branch commit, and the records
of what it produced were captured and committed. Criteria 1, 6 and 7 rest on those
records; criterion 2 rests on a comparison of builds nobody could have made through the
workflow, for a reason the criterion's own scope limits explain.

**Three kinds of citation are in use here and they are worth telling apart.** A test that
reads a committed workflow file proves what runs, because GitHub reads the file exactly as
committed. A test that reads a committed capture proves that the capture says what it is
being read as saying — not that the account still looks like that, which is what the
freshness window is for. And a test that reads a committed template proves only what a
document declares, which is why those citations stay supporting even now that a capture
has been compared against them.

Where a criterion is covered, the split between proving and supporting citations follows
what the artifact is:

* A workflow file is read by GitHub exactly as committed, with no deployment step in
  between, so a test that pins it proves what runs. Those citations are proving.
* A CloudFormation template describes a role that was deployed once, by hand, from a
  laptop, and is not redeployed by CI. A capture has now been taken and compared:
  ``edullm_platform.role_drift`` found no divergence in trust conditions, permission
  statements, boundary, session duration or attached managed policies for either role,
  and the sanitized records are committed under ``fixtures/evidence/phase-1/roles/``.
  That closes the distance between the document and the account. It does not turn a
  template test into a proving one: what a trust policy refuses is still an argument from
  a policy rather than a refusal anybody observed, so those citations stay supporting, a
  second mechanism standing behind a criterion something else already proves.

**What a citation resting on captured evidence is worth, and when it stops.** Captured
evidence is a statement about one moment. Every record in
``edullm_platform.phase1_evidence`` is a ``FreshEvidenceModel``, which refuses to load at
all once it is more than ``FRESHNESS_WINDOW`` old and reports ``evidence_stale`` when it
does. So a citation resting on one is valid exactly while three things hold: a record is
committed for the role, its ``observed_at`` is inside the window, and the comparison
against the committed template reports no drift.
:data:`DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES` cites one test for each of the three, which
is why it is three citations rather than one.

It expires thirty days after the capture, and the expiry is not quiet. The freshness test
fails, the criteria citing it become gaps with reason ``cited_test_failed``, and
``tools/validate_phase1.py`` exits 1. Criteria 4 and 5 go red at that moment, and it is
worth being clear about what that does and does not mean: nothing has changed about a
pull-request job's permissions or about source-identity verification, which are what
prove those two criteria. What has lapsed is the second mechanism they also claim, and
the claim in their scope limits stops being supported. The two honest responses are to
re-capture, or to delete the records and remove these citations, which is a decision
somebody takes in writing. Nothing renews it automatically, and nothing should: the point
of the window is that a role deployed by hand can be widened by hand, and only somebody
going and looking again establishes that it has not been.

**The records of the live run expire on the same terms and it means something different.**
:data:`RUN_EVIDENCE_HOLDS` is the single citation criteria 1, 6 and 7 rest on, and it goes
red thirty days after the capture in exactly the same way. Here the lapse is purely one of
attention: the image, its scan, the session that pushed it and the five refusals it met are
all still in the registry and in CloudTrail, and none of them can change. What stops being
true is that somebody has recently confirmed the repository is still immutable, the role is
still refused, and the digest the tag resolves to is still the one this phase published.
Re-capturing costs a read of the account rather than another publish, and
``edullm_platform.phase1_capture.RUN_RECAPTURE_GUIDANCE`` says so where a reader will meet
it, because an expiry read as "publish again" would push a second image for no reason.

**Where criterion 2 is different from the other three.** It rests on builds nobody could
make through the workflow: the publish job's pre-flight tag lookup resumes to the published
digest rather than building a commit twice, which is correct and is why the shipped path
can never produce the comparison. The builds were therefore made locally and recorded, and
the criterion's scope limits say so plainly rather than leaving a reader to assume the
workflow produced them.

**Seven of the eight are pilot-blocking, and the mapping to the master plan's check list
is one to one.** Phase 1's checks and this module's criteria are the same eight statements
in the same order, so the pilot split needed no judgement at all: it is the plan's markers
carried across. The one that is not pilot-blocking is criterion 2, which is the only entry
in the list that asks for an explanation rather than a refusal, and its scope limits say
why. All eight are covered, so the pilot verdict for this phase is ready — retrospectively,
since the gate closed before the rung existed.
"""

from __future__ import annotations

from typing import Final

from .criteria import (
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
    validate_criterion_specs,
)

__all__ = [
    "DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES",
    "PHASE1_CRITERION_COUNT",
    "phase1_criteria",
]

PHASE1_CRITERION_COUNT: Final = 8

#: The three facts a committed capture has to establish before a citation may rest on it,
#: one test each: a record exists for every role a template declares, it is inside its
#: freshness window, and it matches the template. Cited as a set, because the third alone
#: would let a deleted or expired record read as agreement. The second is the one that
#: expires; see this module's docstring for what happens when it does.
DEPLOYED_ROLES_MODULE: Final = "tests/test_phase1_deployed_roles.py"
DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES: Final = (
    f"{DEPLOYED_ROLES_MODULE}::test_a_capture_is_committed_for_every_role_a_template_declares",
    f"{DEPLOYED_ROLES_MODULE}::test_every_committed_capture_is_inside_its_freshness_window",
    f"{DEPLOYED_ROLES_MODULE}::test_every_committed_capture_matches_the_template_that_declares_it",
)

TRUST_POLICY_MATCHES_THE_ACCOUNT: Final = (
    "The publisher role was deployed once from a laptop and is not redeployed by CI, so "
    "this template began as a claim about the account rather than a description of it. A "
    "capture has since been taken and compared, and the deployed role matches the template "
    "with no findings in either direction; the sanitized record it compared is committed "
    "under fixtures/evidence/phase-1/roles/ and the tests cited beside this one re-run the "
    "comparison. The citation is still supporting rather than proving, because what a "
    "trust policy refuses is an argument from a policy rather than a refusal anybody has "
    "observed, and because it expires with the capture."
)

#: What the drift comparison closes, and what it does not. It makes a widened role
#: detectable, and it detects a widening rather than preventing one.
DRIFT_COMPARISON_RAN: Final = (
    "edullm_platform.role_drift compares a captured DeployedRoleEvidence against the "
    "committed template and reports any divergence in trust conditions, permission "
    "statements, boundary, session duration or attached managed policies, in both "
    "directions. tools/capture_phase1_evidence.py ran it against the sandbox: two roles "
    "compared, no findings against the templates as they stood. The sanitized records are "
    "committed under fixtures/evidence/phase-1/roles/ and "
    "tests/test_phase1_deployed_roles.py re-runs the comparison on every test run, so a "
    "policy widened in the console would now be caught the next time either is executed "
    "rather than leaving every test green. Phase 2 amended the deployer template to reach "
    "the admission stacks, which put the account behind the template until somebody "
    "applied the stack by hand; that amendment was deployed and re-captured on 2026-07-27, "
    "the comparison reports no findings, and the pending record that carried the "
    "difference has been removed rather than left to become an exemption."
)

#: The CLI the workflow actually invokes, parametrised over one rejection reason each.
IDENTITY_CLI_REJECTION: Final = (
    "tests/test_verify_source_identity_cli.py"
    "::test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason"
)

#: The publish job re-derives source identity on its own runner before it configures AWS
#: credentials. Without this, the verifier would be a library nothing on the shipped path
#: calls, and every citation of it would prove only that the library works.
SOURCE_IDENTITY_RUNS_ON_THE_PUBLISH_PATH: Final = (
    "tests/test_build_research_image_workflow.py"
    "::test_publish_job_reverifies_the_source_before_it_holds_aws_credentials"
)

PUBLISHER_TRUST_POLICY: Final = (
    "tests/test_phase1_infrastructure.py"
    "::test_publisher_trusts_only_the_existing_github_oidc_provider"
)

#: The one citation every claim about the live run rests on. It is one test rather than
#: three because a run's records hold or do not hold as a set: the reader checks that
#: each is committed, that each is inside its window, and that they are all about the
#: same image, and any of the three failing means the rest establish nothing. It expires
#: the same way the role captures do; see this module's docstring.
RUN_MODULE: Final = "tests/test_phase1_run_evidence.py"
RUN_EVIDENCE_HOLDS: Final = f"{RUN_MODULE}::test_the_committed_records_of_the_run_all_hold"

#: What every claim resting on the live run gives up thirty days after the capture, and
#: what is different about this expiry from the role captures'.
RUN_EVIDENCE_EXPIRES: Final = (
    "This rests on captured evidence and expires with it. The records under "
    "fixtures/evidence/phase-1/run/ stop loading thirty days after they were observed, "
    "tests/test_phase1_run_evidence.py goes red when they do, and this criterion is a gap "
    "again from that date. What has lapsed then is not the run — the image, its scan, the "
    "session and the refusals are all still in the account and in CloudTrail — but how "
    "recently anybody went and looked. Re-capturing costs a read of the account and not "
    "another publish."
)

REBUILD_MODULE: Final = "tests/test_phase1_rebuild_comparison.py"

#: One citation per recorded comparison, because each isolates a different variable and a
#: single parametrised name would let three of the four be deleted silently.
REBUILD_COMPARISONS: Final = tuple(
    f"{REBUILD_MODULE}::test_every_difference_from_the_first_build_has_a_recorded_cause[{build}]"
    for build in ("b", "c", "d", "published")
) + tuple(
    f"{REBUILD_MODULE}::test_no_field_derived_from_a_pinned_input_ever_differs[{build}]"
    for build in ("b", "c", "d", "published")
)

#: The workflow tests these criteria lean on, named here rather than spelled inside a
#: tuple: a node id split across two adjacent string literals inside a tuple is one
#: missing comma away from being two node ids, and neither pytest nor a reader would say so.
WORKFLOW_MODULE: Final = "tests/test_build_research_image_workflow.py"
DIGEST_READ_BACK_FROM_THE_REGISTRY: Final = f"{WORKFLOW_MODULE}::test_publish_job_takes_the_digest_from_an_ecr_read_back_not_the_local_build"
BUILD_USES_THE_REGISTERED_BASE: Final = f"{WORKFLOW_MODULE}::test_publish_job_builds_from_the_registered_base_digest_under_an_immutable_tag"
RERUN_RESUMES_RATHER_THAN_REBUILDS: Final = f"{WORKFLOW_MODULE}::test_a_published_tag_short_circuits_to_the_digest_the_registry_already_holds"
PUBLISHER_GRANTS_ECR_AND_NOTHING_ELSE: Final = (
    "tests/test_phase1_deployed_roles.py::test_the_deployed_publisher_grants_ecr_and_nothing_else"
)
PROBES_OBEY_THE_FIRST_LESSON: Final = (
    "tests/test_publisher_denials.py::test_every_probe_in_the_matrix_obeys_the_first_lesson"
)
ECR_REPOSITORY_IS_IMMUTABLE: Final = (
    "tests/test_phase1_infrastructure.py"
    "::test_ecr_repository_is_encrypted_scanned_immutable_and_retained"
)
REBUILD_OF_IDENTICAL_INPUTS: Final = (
    f"{REBUILD_MODULE}::test_two_builds_of_identical_inputs_differ_only_in_two_clock_readings"
)
TAG_WAS_NOT_OVERWRITTEN: Final = (
    f"{RUN_MODULE}::test_an_immutable_tag_was_not_overwritten_and_the_original_digest_survived"
)
DENIAL_MATRIX_WAS_REFUSED: Final = (
    f"{RUN_MODULE}::test_the_publisher_session_was_refused_every_action_the_matrix_attempts"
)


def phase1_criteria() -> tuple[CriterionSpec, ...]:
    """The eight Phase 1 acceptance criteria, in order."""
    specs = (
        CriterionSpec(
            number="1",
            statement="A pushed branch commit produces a digest.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                RUN_EVIDENCE_HOLDS,
                f"{RUN_MODULE}::test_a_pushed_branch_commit_produced_a_digest",
                f"{RUN_MODULE}::test_the_digest_was_pushed_by_a_bounded_publisher_session",
            ),
            supporting_node_ids=(
                DIGEST_READ_BACK_FROM_THE_REGISTRY,
                BUILD_USES_THE_REGISTERED_BASE,
                SOURCE_IDENTITY_RUNS_ON_THE_PUBLISH_PATH,
            ),
            scope_limits=(
                (
                    "This is the one criterion that could only ever close with evidence. The "
                    "publish workflow ran against a real branch commit of OLMo-core, ECR "
                    "returned a digest, and the sanitized record of what the registry holds "
                    "is committed under fixtures/evidence/phase-1/run/. Every test in this "
                    "repository stops at the edge of the AWS call, so no test substitutes; "
                    "what the cited tests prove is that the committed record says what it is "
                    "read as saying."
                ),
                (
                    "The digest belongs to the commit rather than to whatever was last "
                    "pushed. The tag is the commit's first twelve characters and the contract "
                    "re-checks that, the recorded base image digest is the one "
                    "config/repositories.yaml registers, and the recorded push time falls "
                    "inside the window of a publisher session the capture tied to the push "
                    "through the session-creation instant the push itself carries."
                ),
                (
                    "One commit, one repository, one run. Nothing here says the next commit "
                    "will publish, and nothing here is a claim about a repository other than "
                    "the one registered."
                ),
                RUN_EVIDENCE_EXPIRES,
            ),
        ),
        CriterionSpec(
            number="2",
            statement=(
                "Rebuilding identical inputs is explainable even if byte-level image "
                "reproducibility differs."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                REBUILD_OF_IDENTICAL_INPUTS,
                *REBUILD_COMPARISONS,
                f"{REBUILD_MODULE}::test_the_differences_are_exactly_the_ones_recorded[b]",
                f"{REBUILD_MODULE}::test_the_filesystem_the_image_carries_is_identical_when_nothing_varies",
                f"{REBUILD_MODULE}::test_the_layers_inherited_from_the_pinned_base_never_move",
                f"{REBUILD_MODULE}::test_the_builds_were_made_from_the_base_this_repository_registers",
                f"{REBUILD_MODULE}::test_every_pinned_field_pattern_matches_something_that_was_recorded",
            ),
            supporting_node_ids=(RERUN_RESUMES_RATHER_THAN_REBUILDS,),
            scope_limits=(
                (
                    "What is claimed is explainability, and what closes it is an explanation "
                    "with an executable check behind it rather than a paragraph. The same "
                    "commit was built from the same digest-pinned base four times, the image "
                    "the workflow published was fetched from the registry to compare against, "
                    "and the five image configurations are committed under "
                    "fixtures/evidence/phase-1/rebuild/. Of seventy leaf fields, two "
                    "independent no-cache builds of identical inputs differ in exactly two: "
                    "the instant the image records for itself and the same instant against "
                    "the one step this Dockerfile executes."
                ),
                (
                    "Four causes account for every difference in all four comparisons, and "
                    "each is checked rather than asserted. Varying only the per-run label "
                    "adds that label and nothing else. Varying only the file modification "
                    "times of the checkout adds the copied layer's digest and nothing else. "
                    "The published image differs further in the layer the WORKDIR creates, "
                    "which carries the build's own clock. A field derived from a pinned "
                    "input — the environment, the command, the working directory, the "
                    "architecture, the three content labels, every recorded build step, and "
                    "all four layers inherited from the base — never moves in any comparison, "
                    "and that is asserted separately so the list of causes cannot be widened "
                    "until it covers anything."
                ),
                (
                    "The builds are local and are not workflow runs, and they could not have "
                    "been. The publish job looks the tag up before it builds, so a re-run of "
                    "the same commit resumes to the published digest rather than building "
                    "again — correct behaviour, and the reason the shipped path can never "
                    "produce this comparison. The comparison therefore describes one builder "
                    "on one machine, both recorded in the file, and says nothing about a "
                    "different BuildKit."
                ),
                (
                    "The one criterion of this phase that does not block a pilot, and the "
                    "reason is what it asks for. Every other check in the list is a refusal, "
                    "and a refusal that does not happen is money, data, attribution or "
                    "lineage lost. This one asks that a difference between two builds be "
                    "explainable, which is what a reviewer needs in order to accept the gate "
                    "and is not what stands between a pilot user and harm: an unexplained "
                    "difference in an image config costs somebody an afternoon, and the "
                    "digest pinning that makes the image trustworthy is proved elsewhere "
                    "in this list rather than here."
                ),
                (
                    "Byte-level reproducibility is not claimed and is not attempted. Three of "
                    "the four causes are clock readings that SOURCE_DATE_EPOCH could pin; the "
                    "fourth is the per-run label, which is deliberate and whose removal would "
                    "cost the provenance that lets somebody holding a digest find the run "
                    "that produced it. Deciding to pin the clocks is a change to the publish "
                    "workflow that nobody has asked for, and this criterion does not ask for "
                    "it."
                ),
            ),
        ),
        CriterionSpec(
            number="3",
            statement="A dirty or unpushed commit is rejected.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                "tests/test_source_identity.py::test_dirty_tracked_worktree_fails",
                "tests/test_source_identity.py::test_dirty_untracked_worktree_fails",
                "tests/test_source_identity.py::test_unpushed_commit_fails_branch_head_verification",
                "tests/test_source_identity.py::test_missing_remote_ref_fails",
                "tests/test_source_identity.py::test_checkout_head_mismatch_fails",
                "tests/test_verify_source_identity_cli.py::test_a_dirty_tree_is_rejected_without_leaking_paths_or_environment",
                f"{IDENTITY_CLI_REJECTION}[overrides4-remote_ref_missing]",
                SOURCE_IDENTITY_RUNS_ON_THE_PUBLISH_PATH,
            ),
            scope_limits=(
                (
                    "Rejection is proved against real git repositories rather than against "
                    "mocks: each cited test builds a bare origin and a checkout, dirties or "
                    "diverges it, and asserts the reason the verifier returns."
                ),
                (
                    "Four rejections are proved and they are not the same rejection. A "
                    "modified tracked file and an untracked file are both a dirty tree; a "
                    "local commit the remote has not seen is a remote-ref mismatch; a branch "
                    "the remote does not have at all is a missing remote ref; and a commit "
                    "that is no longer the checkout's own HEAD is a head mismatch."
                ),
                (
                    "The last citation is what puts the verifier on the shipped path. The "
                    "publish job re-derives source identity on its own runner before it "
                    "configures AWS credentials, so a branch head that moved while the gate "
                    "was running is caught after the gate passed and before anything is "
                    "pushed."
                ),
            ),
        ),
        CriterionSpec(
            number="4",
            statement="A commit from an unauthorized repository is rejected.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                "tests/test_source_identity.py::test_unknown_repository_fails",
                "tests/test_source_identity.py::test_wrong_repository_id_fails",
                f"{IDENTITY_CLI_REJECTION}[overrides0-unregistered_repository]",
                f"{IDENTITY_CLI_REJECTION}[overrides1-repository_id_mismatch]",
                "tests/test_repository_registry.py::test_shipped_repository_registry_contains_exact_olmo_core_registration",
                "tests/test_repository_registry.py::test_repository_registry_unknown_lookups_raise_domain_error",
                SOURCE_IDENTITY_RUNS_ON_THE_PUBLISH_PATH,
            ),
            supporting_node_ids=(PUBLISHER_TRUST_POLICY, *DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES),
            scope_limits=(
                (
                    "Authorization is by name and by GitHub's numeric repository id, and both "
                    "are checked. The name alone would be reusable: a repository can be "
                    "renamed and its old name claimed by another, while the numeric id it was "
                    "registered under cannot move."
                ),
                (
                    "The shipped registry authorizes exactly one repository, so the negative "
                    "case is everything else rather than a curated deny list."
                ),
                (
                    "A second mechanism refuses the same thing further down: the publisher "
                    "role's trust policy pins repository_id and the OIDC subject to OLMo-core, "
                    "so a workflow running in another repository cannot assume the role even "
                    "if source-identity verification were bypassed entirely. That is a fact "
                    f"about the deployed role and not only about the template. "
                    f"{TRUST_POLICY_MATCHES_THE_ACCOUNT}"
                ),
            ),
        ),
        CriterionSpec(
            number="5",
            statement="A pull-request test job cannot request AWS credentials.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                "tests/test_build_research_image_workflow.py::test_workflow_has_exactly_three_ordered_jobs_with_exact_permission_maps",
                "tests/test_build_research_image_workflow.py::test_verify_job_never_requests_an_oidc_token_by_any_spelling",
                "tests/test_build_research_image_workflow.py::test_nothing_lets_the_publish_job_run_after_a_gate_has_failed",
                "tests/test_build_research_image_workflow.py::test_workflow_is_reusable_with_exact_inputs_and_no_secrets",
            ),
            supporting_node_ids=(PUBLISHER_TRUST_POLICY, *DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES),
            scope_limits=(
                (
                    "Two independent mechanisms close this and both are cited, because citing "
                    "one would let the other be removed without anything going red."
                ),
                (
                    "The first is proved as stated. The job that runs untrusted branch code "
                    "holds contents: read and nothing else, so it has no id-token permission "
                    "to request a token with; it cannot rather than may not. A reusable "
                    "workflow can only narrow the permissions its caller grants, so no caller "
                    "can widen this, and the workflow accepts no secrets either."
                ),
                (
                    "The second is the trust policy's subject condition: sub must match "
                    "ref:refs/heads/*, and a pull-request job's subject ends in "
                    ":pull_request, so the role refuses it. The deployed role carries that "
                    f"condition and not merely the template. "
                    f"{TRUST_POLICY_MATCHES_THE_ACCOUNT}"
                ),
                (
                    "What is not proved is the caller side. OLMo-core has no caller workflow "
                    "yet, so nothing here constrains what a future pull-request job in that "
                    "repository grants itself. The trust policy above is what stands between "
                    "such a job and this account, and it is supporting evidence rather than "
                    "proof: a condition nobody has watched refuse anything."
                ),
            ),
        ),
        CriterionSpec(
            number="6",
            statement=(
                "The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                RUN_EVIDENCE_HOLDS,
                DENIAL_MATRIX_WAS_REFUSED,
                f"{RUN_MODULE}::test_every_service_criterion_six_names_was_refused",
            ),
            supporting_node_ids=(
                PUBLISHER_GRANTS_ECR_AND_NOTHING_ELSE,
                PROBES_OBEY_THE_FIRST_LESSON,
                *DEPLOYED_ROLES_MATCH_THEIR_TEMPLATES,
            ),
            scope_limits=(
                (
                    "Two mechanisms close this and they are different in kind, so read them "
                    "separately. The first is the distance between the template and the "
                    "account: the committed template grants one inline policy of nine ECR "
                    "actions on one repository plus the authorization-token call, no Batch, "
                    f"S3, EC2 or IAM action appears in it, and the deployed role matches. "
                    f"{DRIFT_COMPARISON_RAN}"
                ),
                (
                    "The second is what actually proves it: refusals observed rather than "
                    "argued. A session issued to the publisher role through OIDC attempted a "
                    "Batch job submission, an S3 listing, an IAM role creation, a Batch "
                    "compute-environment update and a deletion of an ECR repository, and was "
                    "refused all five. Each refusal is committed under "
                    "fixtures/evidence/phase-1/run/denials/ with the CloudTrail event id a "
                    "reviewer can look up, and the record must hold one denial per matrix "
                    "action in matrix order — four refusals would prove the criterion for four "
                    "actions, and a partial set read later would look like a run that was "
                    "refused them all."
                ),
                (
                    "The S3 half is narrower than the criterion's words and will stay so. The "
                    "probe is ListBuckets, an account-level call with no bucket to be absent, "
                    "so a refusal proves the role holds no account-wide S3 permission rather "
                    "than that it cannot read a dataset: a policy granting only s3:GetObject "
                    "on one bucket would be refused ListBuckets just the same. Closing that "
                    "difference needs an object read that reaches authorization, which needs a "
                    "bucket this project owns and an object in it that exists. No such bucket "
                    "is deployed, and pointing the probe at another team's bucket in the "
                    "shared account would read a refusal from their policy rather than ours."
                ),
                (
                    "Why the probe is ListBuckets at all is worth knowing before anybody adds "
                    "a sixth. The original S3 probe read an object from a bucket chosen not to "
                    "exist and answered AccessDenied on one run and NoSuchBucket on the next, "
                    "for the same role against the same absent bucket — a flake that fails "
                    "towards passing, since AccessDenied is what the matrix is looking for. "
                    "edullm_platform.publisher_denials.PROBE_SELECTION_LESSONS records the "
                    "rule and the run that taught it, and a cited test holds every probe in "
                    "the matrix to it."
                ),
                (
                    "Five refusals under one session at one moment. A role widened tomorrow "
                    "would be refused nothing tomorrow, and this record would still read as it "
                    "does now, which is what the freshness window is for. "
                    f"{RUN_EVIDENCE_EXPIRES}"
                ),
            ),
        ),
        CriterionSpec(
            number="7",
            statement="An immutable tag cannot be overwritten.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                RUN_EVIDENCE_HOLDS,
                TAG_WAS_NOT_OVERWRITTEN,
            ),
            supporting_node_ids=(
                ECR_REPOSITORY_IS_IMMUTABLE,
                RERUN_RESUMES_RATHER_THAN_REBUILDS,
            ),
            scope_limits=(
                (
                    "Three things are recorded and only one of them is the criterion. The "
                    "committed template declares IMMUTABLE; the deployed repository was "
                    "captured and is IMMUTABLE; and a second push of a different image under "
                    "a tag the registry already held was refused with "
                    "ImageTagAlreadyExistsException. The first is a document, the second is a "
                    "setting read back from a describe call, and only the third is a push that "
                    "was turned away."
                ),
                (
                    "The refusal and the survival are separate claims and both are recorded. "
                    "The committed refusal carries the digest the tag resolves to after the "
                    "attempt, and a test checks it against the digest of the image the run "
                    "published, so this says the original image is still there rather than "
                    "only that one push failed."
                ),
                (
                    "The second push was made by hand from a laptop, under an identity that is "
                    "not the publisher role, and the record says so in a field of its own. "
                    "That is a real limit and a small one: tag immutability is a property of "
                    "the repository rather than of the caller, so what was observed is that "
                    "ECR refuses the overwrite, which is the whole of what the criterion "
                    "claims. What was not observed is the publisher role meeting the same "
                    "refusal, and the reason nobody arranged that is that the publish workflow "
                    "deliberately cannot produce it: its pre-flight tag lookup resumes instead "
                    "of pushing again. The identity that attempted it is not named, because in "
                    "a shared sandbox account it is a person."
                ),
                RUN_EVIDENCE_EXPIRES,
            ),
        ),
        CriterionSpec(
            number="8",
            statement="A run manifest using a tag instead of a digest is rejected.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                "tests/test_manifest.py::test_manifest_rejects_mutable_image_digest",
                "tests/test_manifest.py::test_manifest_rejects_image_digest_with_trailing_tag",
                "tests/test_manifest.py::test_manifest_rejects_non_sha256_image_digest",
                "tests/test_manifest.py::test_manifest_rejects_bare_image_digest_without_algorithm_prefix",
            ),
            scope_limits=(
                (
                    "Rejection happens at contract validation, so it applies to every manifest "
                    "that is loaded at all rather than to a checked path a caller might skip."
                ),
                (
                    "Four ways of not being a digest are refused: a bare tag, a digest with a "
                    "tag appended, a digest under an algorithm other than sha256, and 64 hex "
                    "characters with no algorithm prefix. The last two matter because each one "
                    "looks like a digest to a human reader."
                ),
                (
                    "This is the one Phase 1 criterion that was already true before Phase 1 "
                    "began. The manifest contract is Phase 0 work and the digest it demands is "
                    "what Phase 1 produces; the criterion is recorded here because the phase "
                    "depends on it, not because the phase built it."
                ),
            ),
        ),
    )
    if len(specs) != PHASE1_CRITERION_COUNT:
        raise CriteriaDefinitionError(
            f"Phase 1 has {PHASE1_CRITERION_COUNT} acceptance criteria; the definition lists "
            f"{len(specs)}"
        )
    validate_criterion_specs(specs)
    return specs
