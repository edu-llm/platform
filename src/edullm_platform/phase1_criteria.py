"""The Phase 1 acceptance criteria and the tests that are cited for each one.

Phase 1 takes one clean, pushed research-branch commit through shared CI and produces
one immutable ECR image digest. This module records the eight checks the phase must
satisfy, against the contract in ``edullm_platform.criteria``.

Four of the eight are gaps, and they are gaps rather than deferrals. The build path has
never completed a run: ``OLMo-core`` has neither a caller workflow nor the registered
Dockerfile, so no digest has been produced, nothing has been rebuilt, no publisher
session has been denied anything, and no tag has been pushed twice. Nobody decided to
postpone those checks, which is what a deferral records; they are unfinished work, and a
deferral needs a written reason and a written trigger that would both be inventions here.

The four gaps cite no test at all. There are committed templates and workflow tests near
each one, and citing the nearest of them would put a green tick beside a claim nothing
executed. What each gap needs instead is written out in its ``gaps`` text.

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

NO_LIVE_RUN: Final = (
    "The reusable publish workflow has never completed a run. OLMo-core has neither a "
    "caller workflow nor the registered Dockerfile, so the build path has not executed once."
)

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
    "compared, no findings. The sanitized records are committed under "
    "fixtures/evidence/phase-1/roles/ and tests/test_phase1_deployed_roles.py re-runs the "
    "comparison on every test run, so a policy widened in the console would now be caught "
    "the next time either is executed rather than leaving every test green."
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


def phase1_criteria() -> tuple[CriterionSpec, ...]:
    """The eight Phase 1 acceptance criteria, in order."""
    specs = (
        CriterionSpec(
            number="1",
            statement="A pushed branch commit produces a digest.",
            status=CriterionStatus.GAP,
            gaps=(
                (
                    f"{NO_LIVE_RUN} No ECR digest exists for any commit, so the one thing "
                    "this criterion asserts has not happened."
                ),
                (
                    "This closes with evidence rather than with a test: a completed run of "
                    "the publish workflow against a real branch commit, and the digest the "
                    "registry returned for it. No test in this repository can substitute, "
                    "because every one of them stops at the edge of the AWS call."
                ),
            ),
        ),
        CriterionSpec(
            number="2",
            statement=(
                "Rebuilding identical inputs is explainable even if byte-level image "
                "reproducibility differs."
            ),
            status=CriterionStatus.GAP,
            gaps=(
                f"{NO_LIVE_RUN} Nothing has been built once, so nothing has been rebuilt.",
                (
                    "The claim is about two runs and a written account of the difference "
                    "between them, and neither the runs nor the account exists. The account "
                    "has to cover at least the image label carrying the run URL, which "
                    "differs per run and therefore changes the manifest digest by "
                    "construction, and the base image, which is pinned by digest and so "
                    "should not."
                ),
                (
                    "Producing the comparison at all takes a deliberate second build. The "
                    "pre-flight tag lookup makes an ordinary re-run of the same commit "
                    "short-circuit to the digest already in the registry, which is the "
                    "correct behaviour and is not a rebuild."
                ),
            ),
        ),
        CriterionSpec(
            number="3",
            statement="A dirty or unpushed commit is rejected.",
            status=CriterionStatus.COVERED,
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
            status=CriterionStatus.GAP,
            gaps=(
                (
                    "Two things close this and both are runs rather than tests. The first has "
                    "happened and the second has not, so read the halves separately."
                ),
                (
                    "The first was the distance between the template and the account. The "
                    "committed template grants one inline policy of nine ECR actions on one "
                    "repository, plus the authorization-token call that takes no resource, and "
                    "no Batch, S3, EC2 or IAM action appears anywhere in it — and the deployed "
                    "role has now been captured and compared, and matches. So that is a fact "
                    "about the account rather than about a document, which is what makes the "
                    "template's silence about Batch, S3 and IAM mean anything at all. "
                    f"{DRIFT_COMPARISON_RAN}"
                ),
                (
                    "The second is a denial observed rather than argued, and nothing about the "
                    "capture supplies it. A policy that grants no Batch action is a policy; a "
                    "session that tried to submit a Batch job and was refused is the claim. "
                    "Closing this needs a session issued to the publisher role attempting a "
                    "Batch submit, an S3 call and an IAM change, and the CloudTrail records of "
                    "those three refusals. edullm_platform.publisher_denials attempts exactly "
                    "that matrix and tools/verify_publisher_denials.py runs it. One session has "
                    "run it and none has completed it: the S3 probe read an object from a "
                    "bucket chosen not to exist, and S3 answers NoSuchBucket before it "
                    "authorizes anybody, so the run recorded nothing and refused the publish. "
                    "Until a session completes the matrix this stays a gap, and citing the "
                    "capture here would put a green tick beside the half that is missing."
                ),
                (
                    "The S3 half of this will stay narrower than the words above even once a "
                    "session completes the matrix. The probe is now ListBuckets, an "
                    "account-level call with no bucket to be absent, so a refusal proves the "
                    "role holds no account-wide S3 permission rather than that it cannot read "
                    "a dataset: a policy granting only s3:GetObject on one bucket would be "
                    "refused ListBuckets just the same. Closing that difference needs an object "
                    "read that reaches authorization, which needs a bucket this project owns "
                    "and an object in it that exists, and no such bucket is deployed."
                ),
                (
                    "The half that did move expires. The records under "
                    "fixtures/evidence/phase-1/roles/ stop loading thirty days after the "
                    "capture, tests/test_phase1_deployed_roles.py goes red when they do, and "
                    "this paragraph reverts to describing a template nobody has checked. See "
                    "this module's docstring for the two honest responses to that."
                ),
            ),
        ),
        CriterionSpec(
            number="7",
            statement="An immutable tag cannot be overwritten.",
            status=CriterionStatus.GAP,
            gaps=(
                (
                    "The committed ECR template declares IMMUTABLE tag mutability and the "
                    "repository was deployed from it. Neither fact is the criterion: what is "
                    "claimed here is that a second push to an existing tag is refused, and "
                    "that behaviour belongs to ECR at push time."
                ),
                (
                    "tools/capture_phase1_evidence.py records what the deployed repository's "
                    "tag mutability actually is, and no such record is committed here. The two "
                    "roles are committed because something compares them to a template; "
                    "nothing compares a repository to infra/ecr-repositories.yaml, so a "
                    "committed record would be a file that expires and that no test reads. It "
                    "would not close this in any case: a setting read back from a describe "
                    "call is not a push that was refused."
                ),
                f"{NO_LIVE_RUN} No image has been pushed once, let alone twice.",
                (
                    "Closing this needs a live second push of a different image under a tag "
                    "the registry already holds, and the error it returns. The pre-flight tag "
                    "lookup in the publish workflow exists because that refusal is real and "
                    "unrecoverable, so proving it also confirms the reason that lookup is "
                    "there."
                ),
            ),
        ),
        CriterionSpec(
            number="8",
            statement="A run manifest using a tag instead of a digest is rejected.",
            status=CriterionStatus.COVERED,
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
