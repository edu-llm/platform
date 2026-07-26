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
  laptop. No test in this repository compares the deployed role to the template, so a
  template test is evidence about the document rather than proof about the role. Those
  citations are supporting, and they are only ever a second mechanism standing behind a
  criterion that something else already proves.
"""

from __future__ import annotations

from typing import Final

from .criteria import (
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
    validate_criterion_specs,
)

__all__ = ["PHASE1_CRITERION_COUNT", "phase1_criteria"]

PHASE1_CRITERION_COUNT: Final = 8

NO_LIVE_RUN: Final = (
    "The reusable publish workflow has never completed a run. OLMo-core has neither a "
    "caller workflow nor the registered Dockerfile, so the build path has not executed once."
)

TRUST_POLICY_IS_A_DOCUMENT: Final = (
    "The publisher role was deployed once from a laptop and is not redeployed by CI, and "
    "nothing here compares the live role to the committed template, so this citation is "
    "supporting: it proves what the template says rather than what the role does."
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
            supporting_node_ids=(PUBLISHER_TRUST_POLICY,),
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
                    f"A second mechanism refuses the same thing further down. "
                    f"{TRUST_POLICY_IS_A_DOCUMENT} What the template says is that the "
                    "publisher role's trust policy pins repository_id and the OIDC subject to "
                    "OLMo-core, so a workflow running in another repository cannot assume the "
                    "role even if source-identity verification were bypassed entirely."
                ),
            ),
        ),
        CriterionSpec(
            number="5",
            statement="A pull-request test job cannot request AWS credentials.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_build_research_image_workflow.py::test_workflow_has_exactly_two_ordered_jobs_with_exact_permission_maps",
                "tests/test_build_research_image_workflow.py::test_verify_job_never_requests_an_oidc_token_by_any_spelling",
                "tests/test_build_research_image_workflow.py::test_nothing_lets_the_publish_job_run_after_the_gate_has_failed",
                "tests/test_build_research_image_workflow.py::test_workflow_is_reusable_with_exact_inputs_and_no_secrets",
            ),
            supporting_node_ids=(PUBLISHER_TRUST_POLICY,),
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
                    f"The second is the trust policy's subject condition. "
                    f"{TRUST_POLICY_IS_A_DOCUMENT} What the template says is that sub must "
                    "match ref:refs/heads/*, and a pull-request job's subject ends in "
                    ":pull_request, so the role refuses it."
                ),
                (
                    "What is not proved is the caller side. OLMo-core has no caller workflow "
                    "yet, so nothing here constrains what a future pull-request job in that "
                    "repository grants itself. The trust policy above is what stands between "
                    "such a job and this account, and it is supporting evidence rather than "
                    "proof."
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
                    "The criterion is about what the live role can do. The committed template "
                    "grants one inline policy of nine ECR actions on one repository, plus the "
                    "authorization-token call that takes no resource, and no Batch, S3, EC2, "
                    "or IAM action appears anywhere in it. That is a fact about a document."
                ),
                (
                    "Nothing compares the deployed role to that document. The role was created "
                    "once from a laptop and is not redeployed by CI, so a policy widened in "
                    "the console would leave every test in this repository green."
                ),
                (
                    "Closing this needs a denial observed rather than argued: a session issued "
                    "to the publisher role attempting a Batch submit, an S3 read, and an IAM "
                    "change, and the CloudTrail records of those three refusals."
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
