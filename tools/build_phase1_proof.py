from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
)
from edullm_platform.evidence import FRESHNESS_WINDOW
from edullm_platform.open_decisions import OpenDecision, open_decisions

# The criterion-to-test mapping is defined once, in the library, and imported here and by
# the acceptance gate. This module must never grow its own copy: the matrix below and
# tools/validate_phase1.py have to be the same claim, or the bundle is decoration.
from edullm_platform.phase1_capture import (
    CAPTURE_PARTITION,
    CAPTURE_REGION,
    ROLE_CAPTURE_DIR,
    RUN_CAPTURE_DIR,
    CaptureVerdict,
    CommittedRoleCapture,
    CommittedRunEvidence,
    captures_pending_a_deploy,
    captures_that_do_not_hold,
    read_committed_role_captures,
    read_committed_run_evidence,
)
from edullm_platform.phase1_criteria import phase1_criteria
from edullm_platform.proof_bundle import (
    CITATION_LEGEND,
    GENERATOR_TEST_PATHS,
    STATUS_LEGEND,
    STATUS_PROSE,
    BundleWaitingOnADeployError,
    GoldenDigestDriftError,
    MissingTestNodeError,
    ModelRecord,
    ProofBundleError,
    RecordedGolden,
    SuiteOutcome,
    assert_secret_free,
    bullets,
    collect_node_ids,
    command_block,
    contradicting_status_claims,
    count_naming,
    describe_drift,
    file_digest,
    golden_drift,
    golden_drift_guidance,
    load_recorded_goldens,
    model_records,
    recorded_status,
    render_check_detail,
    render_goldens_document,
    run_full_suite,
    run_test_selection,
    source_commit_sha,
    status_label,
    table,
)
from edullm_platform.publisher_denials import (
    PROBE_SELECTION_LESSONS,
    PUBLISHER_DENIED_ACTIONS,
    denial_probes,
)
from edullm_platform.rebuild_comparison import (
    NONDETERMINISM_CAUSES,
    LocalRebuildComparison,
    compare_builds,
    unexplained,
)
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    DriftDirection,
    TemplateRole,
    load_template_roles,
)
from edullm_platform.status_prose import spell

PHASE: Final = "phase-1"
BUNDLE_SCHEMA_VERSION: Final = 1
BUNDLE_RELATIVE_DIR: Final = Path("proof") / PHASE
GOLDENS_FILENAME: Final = "serialization-goldens.json"
GOLDENS_REPORT_FILENAME: Final = "serialization-goldens.md"

#: The two documents that describe the committed role templates and nothing else. Written
#: together, and before anything that reads the account; see :func:`write_goldens`.
GOLDENS_FILENAMES: Final = (GOLDENS_FILENAME, GOLDENS_REPORT_FILENAME)

BUNDLE_FILENAMES: Final = (
    "README.md",
    "deployed-role-drift.md",
    "image-rebuild-comparison.md",
    "negative-case-matrix.md",
    "open-decisions.md",
    "publisher-denial-matrix.md",
    "schema-compatibility.md",
    GOLDENS_FILENAME,
    GOLDENS_REPORT_FILENAME,
    "unit-test-report.md",
)
NESTED_RUN_ENV: Final = "EDULLM_PHASE1_PROOF_NESTED"
GENERATOR_TEST_PATH: Final = "tests/test_phase1_proof.py"
GENERATOR_COMMAND: Final = "uv run python tools/build_phase1_proof.py"

#: The committed artifacts Phase 1 owns, whose digests this bundle records so a reviewer
#: can confirm it describes the tree in front of them.
REBUILD_COMPARISON_PATH: Final = "fixtures/evidence/phase-1/rebuild/local-rebuild-comparison.json"

PHASE1_INPUTS: Final = (
    "infra/ecr-repositories.yaml",
    "infra/iam/ecr-publisher-role.yaml",
    "infra/iam/infra-deployer-role.yaml",
    ".github/workflows/build-research-image.yml",
    ".github/workflows/deploy-phase1-ecr.yml",
    "config/repositories.yaml",
)

#: The library modules Phase 1 added. The repository-wide contract inventory lives in the
#: Phase 0 bundle; repeating all forty rows here would be a second copy going stale.
PHASE1_CONTRACT_MODULES: Final = (
    "edullm_platform.phase1_evidence",
    "edullm_platform.phase1_gate",
    "edullm_platform.publisher_denials",
    "edullm_platform.rebuild_comparison",
    "edullm_platform.role_drift",
)

#: Test modules that carry Phase 1's evidence, by prefix. The reentrant ones are removed
#: rather than listed out, so a module added to that list is dropped from here too.
PHASE1_TEST_PREFIXES: Final = ("tests/test_phase1_", "tests/test_capture_phase1_")

VERIFICATION_COMMANDS: Final = (
    "uv run pytest -q",
    "uv run ruff check .",
    "uv run mypy",
    "uv run python tools/export_schemas.py",
    "uv run python tools/validate_phase1.py",
    GENERATOR_COMMAND,
)

GOLDENS_MISSING_GUIDANCE: Final = (
    "No recorded canonical digests were found at {path}. The Phase 1 proof bundle is the "
    f"source of this tripwire; generate it with `{GENERATOR_COMMAND}` and commit the result."
)


@dataclass(frozen=True)
class ModuleCoverage:
    module: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class Verification:
    collected_node_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    selected: SuiteOutcome
    full_suite: SuiteOutcome
    module_coverage: tuple[ModuleCoverage, ...]


def default_output_dir(repo_root: Path) -> Path:
    return repo_root / BUNDLE_RELATIVE_DIR


def goldens_path(output_dir: Path) -> Path:
    return output_dir / GOLDENS_FILENAME


# --------------------------------------------------------------------------------------
# What Phase 1 records golden digests for
# --------------------------------------------------------------------------------------


def committed_role(repo_root: Path, *, role_name: str, relative_path: str) -> TemplateRole:
    roles = load_template_roles(repo_root / relative_path)
    matching = [role for role in roles if role.role_name == role_name]
    if len(matching) != 1:
        raise ProofBundleError(f"{relative_path} does not declare exactly one {role_name}")
    return matching[0]


def compute_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    """One digest per committed role, over its projection rather than over the file.

    Phase 0's goldens are the canonical digest of a validated fixture. Phase 1's are the
    canonical digest of what a role template *grants*, which is the thing a comparison
    against the account acts on. The difference matters in both directions: a comment or a
    reordered key changes the file and not the projection, and a widened statement changes
    the projection whatever it does to the file's length.

    A drift here is therefore a change to what a role may do, and re-recording it is the
    same moment somebody has to go and re-capture the account, because the role that was
    compared clean against the old projection has not been compared against this one.
    """
    return tuple(
        RecordedGolden(
            fixture=role_name,
            relative_path=relative_path,
            contract=TemplateRole.__name__,
            canonical_json_bytes=len(
                canonical_json_bytes(
                    committed_role(repo_root, role_name=role_name, relative_path=relative_path)
                )
            ),
            digest=sha256_digest(
                committed_role(repo_root, role_name=role_name, relative_path=relative_path)
            ),
        )
        for role_name, relative_path in COMMITTED_ROLE_TEMPLATES
    )


# --------------------------------------------------------------------------------------
# Verifying the tree
# --------------------------------------------------------------------------------------


def phase1_test_modules(collected: Sequence[str]) -> tuple[str, ...]:
    modules = {
        node_id.split("::", 1)[0]
        for node_id in collected
        if node_id.startswith(PHASE1_TEST_PREFIXES)
    }
    return tuple(sorted(modules - set(REENTRANT_TEST_MODULES)))


def module_scoped_node_ids(collected: Sequence[str]) -> tuple[ModuleCoverage, ...]:
    return tuple(
        ModuleCoverage(
            module=module,
            node_ids=tuple(node_id for node_id in collected if node_id.split("::", 1)[0] == module),
        )
        for module in phase1_test_modules(collected)
    )


def verify_repository(repo_root: Path) -> Verification:
    criteria = phase1_criteria()
    collected = collect_node_ids(repo_root, nested_env=NESTED_RUN_ENV)
    cited = {node_id for check in criteria for node_id in check.cited_node_ids}
    missing = sorted(cited - set(collected))
    if missing:
        raise MissingTestNodeError(
            "the negative-case matrix cites test node ids that pytest does not collect; "
            "a matrix may not claim coverage it cannot run:\n  " + "\n  ".join(missing)
        )
    coverage = module_scoped_node_ids(collected)
    selected = tuple(sorted(cited | {node_id for entry in coverage for node_id in entry.node_ids}))
    reentrant = sorted(
        node_id for node_id in selected if node_id.split("::", 1)[0] in REENTRANT_TEST_MODULES
    )
    if reentrant:
        raise ProofBundleError(
            "the proof generator must not select a test that invokes the generator or the "
            "acceptance gate, which would recurse:\n  " + "\n  ".join(reentrant)
        )
    outcome, failed = run_test_selection(repo_root, selected, nested_env=NESTED_RUN_ENV)
    return Verification(
        collected_node_ids=collected,
        selected_node_ids=selected,
        failed_node_ids=failed,
        selected=outcome,
        full_suite=run_full_suite(repo_root, nested_env=NESTED_RUN_ENV),
        module_coverage=coverage,
    )


# --------------------------------------------------------------------------------------
# The drift comparison, run against whatever capture has been committed
# --------------------------------------------------------------------------------------


def refuse_a_capture_that_no_longer_holds(captures: Sequence[CommittedRoleCapture]) -> None:
    """Stop rather than describe a comparison that has stopped being true.

    Criteria 4 and 5 are recorded as covered partly on the strength of these records, and
    the matrix prints the recorded status. So once a capture expires, drifts or fails to
    load, the gate fails those criteria and this bundle would still print them covered —
    which is the one defect a bundle cannot survive. Refusing is also what makes the
    expiry visible: somebody has to re-capture, or delete the records and the citations.

    A capture waiting on a laptop deploy is held back for
    :func:`refuse_a_bundle_waiting_on_a_deploy` rather than reported here, because the two
    have different owners and different remedies and are raised at different points in
    the build.
    """
    broken = [
        capture
        for capture in captures_that_do_not_hold(captures)
        if capture.verdict is not CaptureVerdict.PENDING_DEPLOY
    ]
    if not broken:
        return
    raise ProofBundleError(
        "a committed role capture no longer holds, and the criteria that rest on it are "
        "recorded as covered, so this bundle would state a status the acceptance gate "
        "does not reach:\n  "
        + "\n  ".join(f"{capture.verdict.value}: {capture.detail}" for capture in broken)
    )


def refuse_a_bundle_waiting_on_a_deploy(captures: Sequence[CommittedRoleCapture]) -> None:
    """Stop, last of all, because a committed amendment has not reached the account.

    Same verdict as every other refusal — nothing is written — and deliberately the last
    one raised. Everything before it is a defect in this tree that somebody can fix by
    editing a file, so a build has to reach all of them and report the one it found. This
    is not that: it is a laptop operation nobody in this process can perform, and it would
    otherwise mask every defect behind it for as long as the deploy is outstanding. That
    is exactly what happened, and the cost was three cases about *other* refusals that
    could not reach the refusal they were written for, and passed or failed on this one.

    The golden digests are recorded before this fires, for a related reason: they describe
    the committed templates and say nothing about the account, so a deploy nobody has run
    must not be able to stop the tripwire being re-armed against the template as amended.
    """
    waiting = captures_pending_a_deploy(captures)
    if not waiting:
        return
    raise BundleWaitingOnADeployError(
        "a committed template amendment has not been applied to the account yet, so the "
        "captures the criteria rest on describe a role this repository no longer declares; "
        "the golden digests above were re-recorded and no bundle document was written:\n  "
        + "\n  ".join(f"{capture.role_name}: {capture.detail}" for capture in waiting)
    )


def refuse_a_run_record_that_no_longer_holds(run: CommittedRunEvidence) -> None:
    """Stop rather than describe a run whose records have stopped establishing it.

    Criteria 1, 6 and 7 are recorded as covered on the strength of these records, so once
    one expires, drifts off the image or fails to load, the gate fails those criteria and
    this bundle would still print them covered. Refusing is also what makes the expiry
    visible.
    """
    if run.holds:
        return
    raise ProofBundleError(
        "the committed record of the publish run no longer holds, and three criteria rest "
        "on it, so this bundle would state a status the acceptance gate does not reach:\n  "
        + "\n  ".join(f"{problem.reason}: {problem.detail}" for problem in run.problems)
    )


def render_run_evidence(run: CommittedRunEvidence) -> str:
    """What one publish run produced, and how each record is tied to the others."""
    image = run.image
    scan = run.scan
    session = run.session
    refusal = run.refusal
    repository = run.repository
    if image is None or scan is None or session is None or refusal is None or repository is None:
        raise ProofBundleError("the run records must all hold before this section is rendered")
    counts = scan.finding_counts
    if counts is None:
        raise ProofBundleError("a completed scan must record its finding counts")
    probes = {
        probe.action: probe
        for probe in denial_probes(
            region=image.region,
            ecr_repository=image.repository_name,
            role_name=session.role_name,
        )
    }
    sections = [
        "# Phase 1 publisher denial matrix and the run it came from",
        "",
        (
            "The publisher role is meant to hold nine ECR actions on one repository and "
            "nothing else. Everything else in this repository that says so reads a template "
            "or a capture, which is an argument from a policy. This is the other kind of "
            "evidence: a session issued to that role through OIDC attempted "
            f"{spell(len(PUBLISHER_DENIED_ACTIONS))} things it must not be able to do, and was "
            f"refused all {spell(len(PUBLISHER_DENIED_ACTIONS))}. The records are committed "
            f"under `{RUN_CAPTURE_DIR}/denials/` and each carries the CloudTrail event id of "
            "the refusal, so a reviewer can look up any of them in the account."
        ),
        "",
        "## The run",
        "",
        table(
            ["fact", "value"],
            [
                ["commit", f"`{image.source_commit_sha}`"],
                ["image tag", f"`{image.image_tag}`"],
                ["image digest", f"`{image.image_digest}`"],
                ["base image digest", f"`{image.base_image_digest}`"],
                ["pushed at", image.image_pushed_at.isoformat()],
                ["publisher session assumed at", session.assumed_at.isoformat()],
                ["publisher session expires at", session.expires_at.isoformat()],
                ["OIDC issuer", f"`{session.oidc_issuer}`"],
                ["OIDC subject", f"`{session.oidc_subject}`"],
                ["scan status", scan.scan_status],
                [
                    "scan findings",
                    (
                        f"{counts.critical} critical, {counts.high} high, "
                        f"{counts.medium} medium, {counts.low} low"
                    ),
                ],
                ["repository tag mutability", repository.image_tag_mutability],
            ],
        ),
        "",
        (
            "The session is the one that made the push rather than the most recent one the "
            "role held, and it is not found by proximity: two publisher sessions exist in "
            "every run, twenty-five seconds apart and overlapping. The `PutImage` event "
            "carries the creation instant of the session that made it, and exactly one "
            "`AssumeRoleWithWebIdentity` event has that instant."
        ),
        "",
        "## What the session was refused",
        "",
        table(
            ["action", "the call attempted", "error code", "CloudTrail event"],
            [
                [
                    f"`{denial.attempted_action}`",
                    f"`{denial.event_source.split('.')[0]} {denial.event_name}`",
                    denial.error_code,
                    f"`{denial.event_id}`",
                ]
                for denial in run.denials
            ],
        ),
        "",
        (
            "The record must hold one denial per matrix action, in matrix order. A run that "
            "refused four of the five proved the criterion for four of them, and a file able "
            "to hold the four would be read later as though it had proved all five."
        ),
        "",
        "## How each probe is aimed, and what a permitted call would have done",
        "",
        table(
            ["action", "resource", "why a permitted call changes nothing"],
            [
                [
                    f"`{action}`",
                    f"`{probes[action].resource_name}`" if probes[action].resource_name else "—",
                    PROBE_INERTNESS[action],
                ]
                for action in PUBLISHER_DENIED_ACTIONS
            ],
        ),
        "",
        "## What choosing a probe has cost",
        "",
        (
            "Read this before adding one. Each entry is a rule some probe in this matrix "
            "broke, with the run that broke it, because a rule with no incident attached "
            "reads as caution and gets skipped. The source of truth is "
            "`edullm_platform.publisher_denials.PROBE_SELECTION_LESSONS`."
        ),
        "",
    ]
    for lesson in PROBE_SELECTION_LESSONS:
        sections.extend(
            [
                f"### {lesson.rule}",
                "",
                f"**Learned from.** {lesson.learned_from}",
                "",
                lesson.detail,
                "",
            ]
        )
    return "\n".join(sections).rstrip() + "\n"


#: Why being permitted would have changed nothing, one line per probe. Written here
#: rather than on the probe because it is prose for a reviewer, and the probe definition
#: is read by the job that makes the call.
PROBE_INERTNESS: Final = {
    "batch:SubmitJob": "the queue and the job definition do not exist",
    "s3:ListAllMyBuckets": "the call only lists and names no bucket",
    "iam:CreateRole": "the role name is the caller's own, which IAM already holds",
    "batch:UpdateComputeEnvironment": "the compute environment does not exist",
    "ecr:DeleteRepository": "the repository name is one beside the registered one, and absent",
}


def recorded_rebuilds(repo_root: Path) -> tuple[str, ...]:
    """The labels of the builds recorded for the rebuild comparison."""
    path = repo_root / REBUILD_COMPARISON_PATH
    comparison = LocalRebuildComparison.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple(build.build for build in comparison.builds)


def render_rebuild_comparison(repo_root: Path) -> str:
    """Where a second build of one commit diverges from the first, and why."""
    path = repo_root / REBUILD_COMPARISON_PATH
    comparison = LocalRebuildComparison.model_validate_json(path.read_text(encoding="utf-8"))
    reference = comparison.builds[0]
    others = comparison.builds[1:]
    rows = []
    for build in others:
        differences = compare_builds(reference, build)
        missing = unexplained(differences)
        if missing:
            raise ProofBundleError(
                "two recorded builds differ in a field no cause explains, so the phase's "
                "account of its own nondeterminism is incomplete: " + ", ".join(missing)
            )
        rows.append(
            [
                f"`{reference.build}` vs `{build.build}`",
                build.description,
                str(len(differences)),
                ", ".join(f"`{difference.path}`" for difference in differences),
            ]
        )
    sections = [
        "# Phase 1 image rebuild comparison",
        "",
        (
            "Criterion 2 asks that rebuilding identical inputs be *explainable*, and is "
            "careful not to ask that it be reproducible. This is the explanation."
        ),
        "",
        (
            "The comparison could not come from the publish workflow and was never going to. "
            "That job looks the tag up before it builds, so a re-run of the same commit "
            "resumes to the digest already in the registry rather than building again — "
            "correct behaviour, because ECR tags are immutable and the run-URL label "
            "guarantees a second build would carry a different digest that the tag could "
            "never be moved to. So the builds below were made deliberately on one laptop, "
            "and the image the workflow published was fetched from the registry to compare "
            "against. Both the builder and the platform are recorded, because the answer "
            "depends on both."
        ),
        "",
        table(
            ["fact", "value"],
            [
                ["commit", f"`{comparison.source_commit_sha}`"],
                ["base image", f"`{comparison.base_image_digest}`"],
                ["dockerfile", f"`{comparison.dockerfile_path}`"],
                ["platform", f"`{comparison.platform}`"],
                ["builder", comparison.builder],
                ["configuration fields compared", str(len(reference.fields))],
                ["record", f"`{REBUILD_COMPARISON_PATH}`"],
            ],
        ),
        "",
        "## What differs, against the first build",
        "",
        table(["comparison", "what was varied", "fields differing", "which"], rows),
        "",
        (
            f"Every one of the {len(reference.fields)} fields not named above is identical in "
            "every build, and the ones derived from a pinned input are checked to be "
            "identical rather than merely observed to be: the environment, the command, the "
            "working directory, the architecture, the three content labels, every recorded "
            "build step, and all four layers inherited from the base image. Without that "
            "check the account below could be satisfied by widening the list of causes until "
            "it covered anything."
        ),
        "",
        "## Why each field differs",
        "",
        table(
            ["cause", "fields", "deliberate"],
            [
                [cause.name, f"`{cause.pattern.pattern}`", "yes" if cause.deliberate else "no"]
                for cause in NONDETERMINISM_CAUSES
            ],
        ),
        "",
    ]
    for cause in NONDETERMINISM_CAUSES:
        sections.extend([f"### {cause.name}", "", cause.detail, ""])
    sections.extend(
        [
            "## What this does and does not establish",
            "",
            bullets(
                [
                    (
                        "Two independent builds of identical inputs produce an image whose "
                        "filesystem is identical layer for layer, and whose identity is not. "
                        "Only two fields move, and both are clock readings."
                    ),
                    (
                        "One of the four causes is deliberate. The per-run label is what lets "
                        "somebody holding a digest find the run that produced it, and it is "
                        "also why no re-run of the workflow could ever reproduce a digest even "
                        "if every clock were pinned."
                    ),
                    (
                        "The other three are clocks, and `SOURCE_DATE_EPOCH` would pin them. "
                        "Nobody has asked for byte-level reproducibility and this criterion "
                        "does not, so nothing here proposes it."
                    ),
                    (
                        "This says nothing about a different builder. A BuildKit that wrote "
                        "layer metadata differently would produce a different answer, which is "
                        "why the builder is recorded in the file rather than assumed."
                    ),
                ]
            ),
        ]
    )
    return "\n".join(sections).rstrip() + "\n"


def render_open_decisions(decisions: Sequence[OpenDecision]) -> str:
    """Questions this phase surfaced and deliberately did not answer."""
    sections = [
        "# Phase 1 open decisions",
        "",
        (
            "A criterion records something that must be true and whether it is. This records "
            "something nobody has decided. A gap means unfinished work and a deferral means a "
            "postponement with a trigger; neither fits a question whose answer is a policy "
            "choice, and a question like that has two fates if it is not written down. It is "
            "settled by accident by whoever first trips over it, or silently by whoever "
            "happens to be implementing near it."
        ),
        "",
        (
            "None of these has a recommendation, and none may have one. The source is "
            "`src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than "
            "two options, so an entry cannot become a decision by having its alternatives "
            "deleted. Answering one means removing it from there and putting the answer where "
            "it is enforced."
        ),
        "",
        table(
            ["#", "question", "has to be answered"],
            [[decision.number, decision.question, decision.lands_in] for decision in decisions],
        ),
        "",
    ]
    for decision in decisions:
        sections.extend(
            [
                f"## Decision {decision.number} — {decision.question}",
                "",
                f"**Raised by.** {decision.raised_by}",
                "",
                "**Why it matters.**",
                "",
                bullets(decision.why_it_matters),
                "",
                "**What is known.**",
                "",
                bullets(decision.what_is_known),
                "",
                "**The options, none of them chosen.**",
                "",
                bullets(decision.options),
                "",
                f"**Has to be answered.** {decision.lands_in}",
                "",
            ]
        )
    return "\n".join(sections).rstrip() + "\n"


def render_role_drift(
    repo_root: Path,
    captures: Sequence[CommittedRoleCapture],
) -> str:
    role_rows = [
        [
            role_name,
            f"`{relative_path}`",
            str(
                len(
                    committed_role(
                        repo_root, role_name=role_name, relative_path=relative_path
                    ).inline_policies
                )
            ),
            str(
                committed_role(
                    repo_root, role_name=role_name, relative_path=relative_path
                ).max_session_duration_seconds
            ),
        ]
        for role_name, relative_path in COMMITTED_ROLE_TEMPLATES
    ]
    sections = [
        "# Phase 1 deployed-role drift",
        "",
        (
            "Both Phase 1 roles were created once from a laptop and neither is redeployed by "
            "CI, so each committed template began as a claim about the account rather than a "
            "description of it. `edullm_platform.role_drift` is what turns the claim back into "
            "something checkable, `tools/capture_phase1_evidence.py` runs it against the live "
            "account as it captures, and the sanitized records it wrote are committed under "
            f"`{ROLE_CAPTURE_DIR}/` so the comparison can be re-run by anybody, with no "
            "credentials, as often as the suite runs."
        ),
        "",
        "## The roles compared",
        "",
        table(["role", "template", "inline policies", "max session (s)"], role_rows),
        "",
        "## What is reported, and in which direction",
        "",
        (
            "A deployed role that grants **more** than its template is a security finding. One "
            "that grants **less** is not — it is a role that will refuse a push nobody expected "
            "it to refuse. Only the first is dangerous and both mean the committed template has "
            "stopped describing the account, so every finding carries a direction and none of "
            "them passes silently."
        ),
        "",
        table(
            ["direction", "means"],
            [
                [
                    f"`{DriftDirection.WIDER.value}`",
                    "the deployed role grants something the template does not",
                ],
                [
                    f"`{DriftDirection.NARROWER.value}`",
                    "the template grants something the deployed role does not",
                ],
                [
                    f"`{DriftDirection.CHANGED.value}`",
                    (
                        "a difference with no direction: an edited condition value, a renamed "
                        "boundary, a statement selecting by exclusion where the template "
                        "selects by inclusion"
                    ),
                ],
            ],
        ),
        "",
        "## The normalisation, and what it cannot hide",
        "",
        (
            "A template spells a resource `arn:${AWS::Partition}:ecr:${AWS::Region}:"
            "${AWS::AccountId}:repository/x` and the account returns it expanded, with the "
            "account then masked by capture. Reconciling the two is the one place a comparison "
            "could quietly make a wider role look identical to a narrower one, so the folding "
            "is deliberately mean:"
        ),
        "",
        bullets(
            [
                (
                    "It is positional. An ARN is split into its six fields — and split on "
                    "colons *outside* a `${…}`, because `${AWS::Partition}` contains two of "
                    "them — and only the partition, region and account fields are ever touched."
                ),
                (
                    f"It is exact. A field folds only when it holds precisely the pseudo-"
                    f"parameter, or precisely `{CAPTURE_PARTITION}` and `{CAPTURE_REGION}`, "
                    "which the caller names. Another region, another partition, any wildcard, "
                    "and every character of the resource survive untouched and are still "
                    "compared."
                ),
                (
                    "It distinguishes accounts. Capture masks this account and any other "
                    "account to different placeholders, and only the former folds, so a grant "
                    "pointing at somebody else's account is reported rather than absorbed."
                ),
                (
                    "It refuses what it does not understand. A substitution that is not one of "
                    "those three raises rather than being guessed at or compared as a literal."
                ),
            ]
        ),
        "",
        "## What this comparison does not see",
        "",
        bullets(
            [
                (
                    "Statement order. IAM evaluates a document's statements as a set, so a "
                    "reordered document grants exactly what the template grants and produces "
                    "no finding. Every other difference does."
                ),
                (
                    "Wildcard containment. `repository/*` and `repository/x` are reported as "
                    "one resource gained and one lost rather than as one being wider than the "
                    "other. Reasoning about IAM's wildcard semantics is where a comparison gets "
                    "quietly wrong, and being wrong here is worse than being blunt."
                ),
                (
                    "Anything the boundary denies. `InternSandboxBoundary` is an account "
                    "policy this repository does not own; the comparison records that a role "
                    "is bounded by it and says nothing about what it permits."
                ),
                (
                    "Everything outside the projection: role tags, description, path, role id, "
                    "creation and last-used dates. None of it is comparable to a template this "
                    "repository commits."
                ),
            ]
        ),
        "",
        "## What this bundle compared",
        "",
        (
            "One capture per role, taken against the sandbox and committed after review. The "
            "generator refuses to write at all if any of them has expired, drifted or stopped "
            "loading, so this table can only ever report agreement — the interesting states "
            "are reported by the refusal instead."
        ),
        "",
        table(
            ["role", "observed", "matches its template", "findings", "expires"],
            [
                [
                    capture.role_name,
                    observation_date(capture),
                    # Read off the verdict rather than written as "yes". A cell that
                    # asserts agreement without consulting the comparison it is reporting
                    # is the shape of defect this whole document exists to catch, and a
                    # build that reached here with a capture in any other state would have
                    # printed it.
                    "yes" if capture.holds else capture.verdict.value,
                    str(len(capture.report.findings)) if capture.report else "0",
                    expiry_date(capture),
                ]
                for capture in captures
            ],
        ),
        "",
        (
            "**Expires** is thirty days after the observation, and it is not a formality. Every "
            "Phase 1 evidence record refuses to load past it, so on that date "
            "`tests/test_phase1_deployed_roles.py` goes red, every criterion resting on it "
            "reverts with reason `cited_test_failed`, `tools/validate_phase1.py` exits 1, and "
            "this bundle stops building. Nothing about the roles will have changed; what will "
            "have lapsed is anybody's knowledge of them. The two honest responses are to "
            "re-capture, or to delete the records and remove the citations resting on them, "
            "which is a decision somebody takes in writing."
        ),
    ]
    return "\n".join(sections).rstrip() + "\n"


def observation_date(capture: CommittedRoleCapture) -> str:
    return "—" if capture.evidence is None else capture.evidence.observed_at.date().isoformat()


def expiry_date(capture: CommittedRoleCapture) -> str:
    return "—" if capture.expires_at is None else capture.expires_at.date().isoformat()


# --------------------------------------------------------------------------------------
# The rest of the bundle
# --------------------------------------------------------------------------------------


def render_unit_test_report(verification: Verification) -> str:
    full = verification.full_suite
    selected = verification.selected
    rows = [
        [entry.module, str(len(entry.node_ids)), "pass" if selected.green else "see below"]
        for entry in verification.module_coverage
    ]
    sections = [
        "# Phase 1 unit-test report",
        "",
        (
            "Summarised counts only. Raw pytest output is not copied here; the commands below "
            "reproduce it in full."
        ),
        "",
        "## Commands a reviewer can re-run",
        "",
        command_block(VERIFICATION_COMMANDS),
        "",
        "## Whole suite",
        "",
        table(
            ["measure", "count"],
            [
                ["collected by pytest", str(len(verification.collected_node_ids))],
                [f"executed (excluding {', '.join(GENERATOR_TEST_PATHS)})", str(full.tests)],
                ["passed", str(full.passed)],
                ["failed", str(full.failures)],
                ["errored", str(full.errors)],
                ["skipped", str(full.skipped)],
                ["pytest exit code", str(full.exit_code)],
            ],
        ),
        "",
        "## Targeted verification run",
        "",
        (
            "Every test node id cited by the negative-case matrix, plus every test in the "
            "modules Phase 1 added, executed as one selection."
        ),
        "",
        table(
            ["measure", "count"],
            [
                ["selected node ids", str(len(verification.selected_node_ids))],
                ["executed", str(selected.tests)],
                ["passed", str(selected.passed)],
                ["failed", str(selected.failures)],
                ["errored", str(selected.errors)],
                ["skipped", str(selected.skipped)],
                ["pytest exit code", str(selected.exit_code)],
            ],
        ),
        "",
        "## Per-module coverage",
        "",
        (
            "The test modules Phase 1 added, excluding the two that invoke a gate or this "
            "generator; those run in the reviewer's own `uv run pytest -q`."
        ),
        "",
        table(["module", "tests", "result"], rows),
    ]
    if verification.failed_node_ids:
        sections.extend(["", "## Failures", "", bullets(verification.failed_node_ids)])
    return "\n".join(sections) + "\n"


def render_goldens_report(goldens: Sequence[RecordedGolden]) -> str:
    rows = [
        [record.fixture, record.relative_path, str(record.canonical_json_bytes), record.digest]
        for record in goldens
    ]
    return (
        "\n".join(
            [
                "# Phase 1 golden canonical digests",
                "",
                (
                    f"The canonical JSON digest of each of the {len(goldens)} committed role "
                    "templates, taken over the projection the drift comparison acts on rather "
                    "than over the file."
                ),
                "",
                (
                    "That is the difference worth understanding. A comment, a reordered key or "
                    "a whitespace change alters the file and not the projection, and does not "
                    "land here. A statement that grants one more action alters the projection "
                    "whatever it does to the file, and does. The digest is `sha256` over "
                    "`canonical_json_bytes(TemplateRole)`, the same function that produces "
                    "manifest digests in lineage records."
                ),
                "",
                table(["role", "template", "canonical bytes", "digest"], rows),
                "",
                "## How this fails",
                "",
                (
                    f"`{GOLDENS_FILENAME}` in this directory is the machine-readable copy. "
                    "`tests/test_phase1_golden.py` reprojects each template, recomputes its "
                    "digest and compares it to the recorded value, one test per role so a "
                    "failure names the role rather than the batch."
                ),
                "",
                (
                    f"`{GENERATOR_COMMAND}` refuses to overwrite a drifted digest. Re-recording "
                    "requires `--regenerate-goldens`, so a change to what a role may do cannot "
                    "be absorbed by re-running the generator."
                ),
                "",
                (
                    "Re-recording is also the moment to re-capture. A role that compared clean "
                    "against the old projection has not been compared against the new one, so "
                    "any drift report in this bundle is about a template that no longer exists."
                ),
                "",
                "```",
                golden_drift_guidance(command=GENERATOR_COMMAND).format(
                    fixture="<role>",
                    contract="<contract>",
                    recorded="<recorded digest>",
                    live="<live digest>",
                ),
                "```",
            ]
        )
        + "\n"
    )


def phase1_models(repo_root: Path) -> tuple[ModelRecord, ...]:
    return tuple(
        record for record in model_records(repo_root) if record.module in PHASE1_CONTRACT_MODULES
    )


def render_schema_report(models: Sequence[ModelRecord]) -> str:
    rows = [
        [
            record.name,
            record.module,
            "base" if record.base else "record",
            record.schema_version,
            record.structural_digest,
        ]
        for record in models
    ]
    return (
        "\n".join(
            [
                "# Phase 1 schema compatibility report",
                "",
                (
                    f"The {len(models)} contract models Phase 1 added. The structural digest is "
                    "`sha256` over the model's JSON schema with sorted keys, so it changes when "
                    "a field is added, removed, retyped or reconstrained, and does not change "
                    "when unrelated code moves."
                ),
                "",
                (
                    "None of these is exported to `schemas/`. Those files describe what a human "
                    "authors — the organization, the workload catalog, the policy, a run "
                    "manifest — and nobody writes an evidence record by hand. The "
                    "repository-wide inventory, including every Phase 0 contract, is in "
                    "`proof/phase-0/schema-compatibility.md`; it is not repeated here, because "
                    "a second copy is a copy that goes stale."
                ),
                "",
                table(["model", "module", "kind", "schema_version", "structural digest"], rows),
            ]
        )
        + "\n"
    )


def render_matrix(criteria: Sequence[CriterionSpec], verification: Verification) -> str:
    summary_rows = [
        [
            check.number,
            status_label(check),
            str(len(check.proving_node_ids)),
            str(len(check.supporting_node_ids)),
            check.statement,
        ]
        for check in criteria
    ]
    gaps = [check for check in criteria if check.status is CriterionStatus.GAP]
    sections = [
        "# Phase 1 negative-case matrix",
        "",
        (
            f"The {len(criteria)} Phase 1 acceptance criteria, mapped to the tests cited for "
            "each one by node id. Each cited node id was collected and executed by this "
            "generator before the bundle was written; a citation pytest cannot collect aborts "
            "generation rather than being printed."
        ),
        "",
        (
            "This mapping is defined once, in `src/edullm_platform/phase1_criteria.py`. The "
            "acceptance gate reads the same definition and executes the same node ids, so this "
            "matrix and `tools/validate_phase1.py` cannot disagree."
        ),
        "",
        (
            f"Verification run: {verification.selected.tests} tests executed, "
            f"{verification.selected.passed} passed, {verification.selected.failures} failed, "
            f"{verification.selected.errors} errored, pytest exit code "
            f"{verification.selected.exit_code}."
        ),
        "",
        STATUS_LEGEND,
        "",
        CITATION_LEGEND,
        "",
        table(["#", "status", "proving", "supporting", "check"], summary_rows),
        "",
    ]
    if gaps:
        sections.extend(
            [
                "## Gaps",
                "",
                (
                    "Read these first. A matrix that overstates coverage is worse than no "
                    "matrix. Every gap here fails the acceptance gate, and each one is "
                    "unfinished work rather than a recorded decision to postpone: a deferral "
                    "needs a written reason and a written trigger, and neither exists for any "
                    "of these."
                ),
                "",
            ]
        )
        for check in gaps:
            sections.extend(
                [
                    f"### Check {check.number} (GAP) — {check.statement}",
                    "",
                    bullets(check.gaps),
                    "",
                ]
            )
    sections.extend(["## Checks", ""])
    for check in criteria:
        sections.extend(render_check_detail(check))
    return "\n".join(sections).rstrip() + "\n"


def run_expiry(run: CommittedRunEvidence) -> str:
    """When the earliest of the run's records stops loading."""
    observed = [
        record.observed_at
        for record in (run.image, run.scan, run.session, run.refusal, run.repository, *run.denials)
        if record is not None
    ]
    if not observed:
        raise ProofBundleError("a run with no records has no expiry to report")
    return (min(observed) + FRESHNESS_WINDOW).date().isoformat()


def known_limitations(
    repo_root: Path,
    checks: Sequence[CriterionSpec],
    captures: Sequence[CommittedRoleCapture],
    run: CommittedRunEvidence,
) -> tuple[str, ...]:
    """What this bundle does not establish, read off the tree rather than remembered.

    No entry states a criterion status of its own: where one names a check, the status
    word comes from ``checks``, so a limitation cannot disagree with the verdict the gate
    reached. ``contradicting_status_claims`` refuses the bundle if one ever does.
    """

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    limitations: list[str] = []
    counts = run.scan.finding_counts if run.scan is not None else None
    if counts is None:
        raise ProofBundleError("a completed scan must record its finding counts")
    limitations.append(
        "Whether an image scan result should be able to block a publish is an open question "
        "and this bundle does not answer it. The published image scanned "
        f"{spell(counts.critical)} critical and {spell(counts.high)} high findings, all of them "
        "inherited from the base image this repository pins, and blocked nothing, because "
        "nothing is wired to the scan. That is harmless while "
        "nothing runs a Phase 1 image and stops being harmless the day something does. See "
        "`open-decisions.md`; it is recorded there rather than settled here."
    )
    limitations.append(
        "One run, one commit, one repository. Everything the live half of this phase claims "
        f"comes from a single publish of one branch commit, and check 1 is {status_of('1')} on "
        "the strength of it. Nothing here says the next commit will publish, and nothing here "
        "is a claim about any repository other than the one config/repositories.yaml registers."
    )
    limitations.append(
        "The second push that ECR refused was made by hand from a laptop, under an identity "
        f"that is not the publisher role, which is why check 7 is {status_of('7')} on a "
        "narrower observation than a reader might assume. Tag immutability belongs to the "
        "repository rather than to the caller, so the refusal stands; what was not observed "
        "is the publisher role meeting it, and the publish workflow deliberately cannot "
        "produce that, because its pre-flight lookup resumes rather than pushing again."
    )
    limitations.append(
        "The S3 half of check 6 is narrower than the criterion's words. The probe is "
        "ListBuckets, an account-level call with no bucket to be absent, so a refusal proves "
        "the role holds no account-wide S3 permission rather than that it cannot read a "
        "dataset. Closing that difference needs a bucket this project owns and an object in "
        "it that exists, and no such bucket is deployed."
    )
    limitations.append(
        "The rebuild comparison behind check 2 was made locally rather than by the workflow, "
        "on one builder and one platform, both recorded in the record it reads. The workflow "
        "cannot produce it: a re-run of the same commit resumes to the published digest "
        "instead of building. A different BuildKit could produce a different answer."
    )
    limitations.append(
        "A capture is a statement about one moment. The records under "
        f"`{ROLE_CAPTURE_DIR}/` stop loading thirty days after they were observed — "
        + ", ".join(f"{capture.role_name} on {expiry_date(capture)}" for capture in captures)
        + " — and every claim resting on them is a gap again from that date. Nothing renews it, "
        "and nothing should."
    )
    limitations.append(
        f"The records of the publish run under `{RUN_CAPTURE_DIR}/` expire the same way and it "
        f"means something different. They stop loading on {run_expiry(run)}, and checks 1, 6 "
        "and 7 revert to gaps on that date. Nothing about the run will have changed — the "
        "image, its scan, the session and the five refusals are all still in the registry and "
        "in CloudTrail — but nobody will have confirmed lately that the repository is still "
        "immutable, the role is still refused, and the tag still resolves to this digest. "
        "Re-capturing costs a read of the account rather than another publish."
    )
    limitations.append(
        "The drift comparison does not reason about IAM wildcards. A deployed resource of "
        "`repository/*` against a template's `repository/x` is reported as one resource gained "
        "and one lost, not as one being wider than the other."
    )
    limitations.append(
        "The secret scan applied to this bundle masks its own content digests before scanning. "
        "A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA "
        "both match the generic long-credential patterns in evidence.py, so the two exact token "
        "shapes this bundle emits are replaced with placeholders and everything else is scanned "
        "unchanged. No other exemption is applied."
    )
    limitations.append(
        "The nested verification run excludes every test module that builds a proof bundle "
        f"({', '.join(GENERATOR_TEST_PATHS)}), because those tests invoke a generator and would "
        "recurse. They run in the reviewer's own `uv run pytest -q`."
    )
    limitations.append(
        "This bundle describes the working tree at generation time, which may differ from the "
        "commit named above. The input digests recorded in the bundle index identify exactly "
        "what was measured."
    )
    limitations.append(
        "Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale "
        f"as soon as a test is added or a template changes. Re-run `{GENERATOR_COMMAND}` and "
        "read the diff before accepting a phase gate. The recorded role digests are the one "
        "part that fails loudly on its own when it goes stale."
    )
    return tuple(limitations)


def standing(gap_numbers: Sequence[str]) -> str:
    """How the bundle opens, which cannot be a fixed sentence about being unfinished.

    The first version of this said "It is not done", which was true when it was written
    and would have gone on being printed after it stopped being true. A reviewer who
    trusts the bundle would have been told the opposite of what the table below says.
    """
    if gap_numbers:
        return "It is not done, and the Result table below says by how much."
    return (
        "Every criterion is covered and the gate is green, which is the state in which a "
        "bundle is most worth reading carefully: the Known limitations below say what each "
        "criterion does not cover, and `open-decisions.md` says what this phase surfaced "
        "and did not settle."
    )


def gate_verdict(gap_numbers: Sequence[str]) -> str:
    if not gap_numbers:
        return (
            "`tools/validate_phase1.py` exits 0 against this tree: every phase criterion is "
            "covered or explicitly deferred."
        )
    if len(gap_numbers) == 1:
        subject = f"criterion {gap_numbers[0]} is a GAP"
    else:
        subject = f"criteria {', '.join(gap_numbers)} are GAPs"
    return (
        f"`tools/validate_phase1.py` exits 1 against this tree. Phase 1 is not accepted: "
        f"{subject}. That is the honest state of the phase, not a broken gate. Read the Gaps "
        "section of `negative-case-matrix.md` for what closes it."
    )


def input_digest_table(
    repo_root: Path,
    captures: Sequence[CommittedRoleCapture],
) -> tuple[tuple[str, str], ...]:
    """Everything this bundle was generated from, including every committed record.

    Read off the tree for the run records rather than listed, so a record added to the
    run directory is measured without a second edit here — and so one deleted stops
    appearing rather than being reported at a digest nobody can reproduce.
    """
    run_records = sorted(
        str(path.relative_to(repo_root)) for path in (repo_root / RUN_CAPTURE_DIR).rglob("*.json")
    )
    paths = [
        *PHASE1_INPUTS,
        *(capture.capture_path for capture in captures if capture.capture_path is not None),
        *run_records,
        REBUILD_COMPARISON_PATH,
    ]
    return tuple((path, file_digest(repo_root / path)) for path in sorted(paths))


def render_index(
    *,
    generated_at: datetime,
    commit_sha: str,
    criteria: Sequence[CriterionSpec],
    verification: Verification,
    goldens: Sequence[RecordedGolden],
    models: Sequence[ModelRecord],
    captures: Sequence[CommittedRoleCapture],
    run: CommittedRunEvidence,
    rebuilds: Sequence[str],
    decisions: Sequence[OpenDecision],
    input_digests: Sequence[tuple[str, str]],
    limitations: Sequence[str],
) -> str:
    covered_numbers = [
        check.number for check in criteria if check.status is CriterionStatus.COVERED
    ]
    deferred_numbers = [
        check.number for check in criteria if check.status is CriterionStatus.DEFERRED
    ]
    gap_numbers = [check.number for check in criteria if check.status is CriterionStatus.GAP]
    findings = sum(
        len(capture.report.findings) for capture in captures if capture.report is not None
    )
    return (
        "\n".join(
            [
                "# Phase 1 proof bundle",
                "",
                f"Phase: {PHASE}",
                f"Bundle schema version: {BUNDLE_SCHEMA_VERSION}",
                f"Source commit: {commit_sha}",
                f"Generated: {generated_at.astimezone(UTC).isoformat(timespec='seconds')}",
                "",
                (
                    "This bundle exists so that a reviewer can decide whether Phase 1 is done "
                    "without reading the test suite. Everything it claims was executed by "
                    f"`{GENERATOR_COMMAND}` at generation time. {standing(gap_numbers)}"
                ),
                "",
                "## Contents",
                "",
                bullets(
                    [
                        (
                            f"`negative-case-matrix.md` — each of the {spell(len(criteria))} "
                            "Phase 1 acceptance criteria mapped to the tests cited for it, by "
                            "node id, with every gap stated. Read this one first."
                        ),
                        (
                            "`publisher-denial-matrix.md` — the run this phase turns on, the "
                            f"{spell(len(PUBLISHER_DENIED_ACTIONS))} refusals the publisher "
                            "session met with the CloudTrail event "
                            "id of each, how every probe is aimed so that being permitted "
                            "would change nothing, and what choosing a probe has cost so far."
                        ),
                        (
                            "`image-rebuild-comparison.md` — the same commit built four times "
                            "from the same pinned base, field by field, and the four causes "
                            "that account for every difference."
                        ),
                        (
                            "`open-decisions.md` — questions this phase surfaced and did not "
                            "answer. One so far: whether a scan result may block a publish."
                        ),
                        (
                            "`deployed-role-drift.md` — how a role in the account is compared "
                            "to the template that claims to describe it, what the comparison "
                            "cannot see, and what it found. Phase 0 has no counterpart: it "
                            "deployed nothing."
                        ),
                        (
                            "`unit-test-report.md` — summarised pass and fail counts, per "
                            "module and for the whole suite, with the commands to reproduce "
                            "them."
                        ),
                        (
                            "`serialization-goldens.md` and `"
                            + GOLDENS_FILENAME
                            + "` — the recorded canonical digest of what each committed role "
                            "template grants, and the tripwire that fails when one drifts."
                        ),
                        (
                            "`schema-compatibility.md` — the contract models Phase 1 added, "
                            "with their structural digests."
                        ),
                    ]
                ),
                "",
                "## Result",
                "",
                table(
                    ["measure", "value"],
                    [
                        ["suite tests collected", str(len(verification.collected_node_ids))],
                        ["suite tests executed", str(verification.full_suite.tests)],
                        ["suite passed", str(verification.full_suite.passed)],
                        ["suite failed", str(verification.full_suite.failures)],
                        ["suite errored", str(verification.full_suite.errors)],
                        ["suite skipped", str(verification.full_suite.skipped)],
                        ["matrix node ids executed", str(verification.selected.tests)],
                        ["matrix node ids passed", str(verification.selected.passed)],
                        ["matrix node ids failed", str(verification.selected.failures)],
                        ["phase criteria", str(len(criteria))],
                        ["criteria COVERED", count_naming(covered_numbers)],
                        ["criteria DEFERRED", count_naming(deferred_numbers)],
                        ["criteria GAP (each one fails the gate)", count_naming(gap_numbers)],
                        ["roles compared to their template", str(len(captures))],
                        ["role drift findings", str(findings)],
                        ["role templates with recorded digests", str(len(goldens))],
                        ["publish runs captured", "1"],
                        ["actions the publisher session was refused", str(len(run.denials))],
                        ["image configurations compared", str(len(rebuilds))],
                        ["open decisions recorded", str(len(decisions))],
                        ["contract models added by this phase", str(len(models))],
                    ],
                ),
                "",
                "## Verification commands",
                "",
                "Run these from the repository root.",
                "",
                command_block(VERIFICATION_COMMANDS),
                "",
                gate_verdict(gap_numbers),
                "",
                "## Inputs measured",
                "",
                (
                    "Digests of the files this bundle was generated from, so a reviewer can "
                    "confirm the bundle describes the tree in front of them. Verify with "
                    "`shasum -a 256 <file>`."
                ),
                "",
                table(["file", "digest"], [[path, digest] for path, digest in input_digests]),
                "",
                "## Known limitations",
                "",
                bullets(limitations),
                "",
                "## Reviewer sign-off",
                "",
                (
                    "Reviewed by: ______________________  Date: ______________  "
                    "Accept / Reject: ______________"
                ),
            ]
        )
        + "\n"
    )


def write_goldens(
    output_dir: Path,
    goldens: Sequence[RecordedGolden],
    criteria: Sequence[CriterionSpec],
    *,
    regenerate: bool,
) -> tuple[Path, ...]:
    """Record what each committed role template grants, before anything reads the account.

    The pair is written together and first, on its own terms. Both documents are derived
    from the templates this repository commits and neither says anything about the
    account, so neither depends on a capture holding, on a run record loading, or on a
    verification run having happened.

    Writing them first is what makes ``--regenerate-goldens`` usable in the state this
    repository is actually in. A template amendment lands before the laptop deploy that
    realises it; the capture is then legitimately behind the template and the bundle is
    legitimately refused. If re-recording the digest were downstream of that refusal, the
    tripwire would report a drift that nobody could clear until a deploy no test can
    perform, and the suite would carry a red test saying, in a second voice, exactly what
    the pending-amendment record already says.

    They are written as a pair rather than the ``.json`` alone, which is the shape this
    had and the reason it was worth changing: the ``.md`` renders the same digests for a
    human, and re-recording one without the other leaves two committed documents in the
    same directory disagreeing about what a role grants.
    """
    goldens_file = goldens_path(output_dir)
    if goldens_file.exists() and not regenerate:
        drift = golden_drift(load_recorded_goldens(goldens_file), goldens)
        if drift:
            raise GoldenDigestDriftError(describe_drift(drift, command=GENERATOR_COMMAND))

    documents = {
        GOLDENS_FILENAME: render_goldens_document(goldens, phase=PHASE),
        GOLDENS_REPORT_FILENAME: render_goldens_report(goldens),
    }
    contradictions = contradicting_status_claims(documents, criteria)
    if contradictions:
        raise ProofBundleError(
            "the recorded golden digests state a criterion status the acceptance gate did "
            "not reach:\n  " + "\n  ".join(contradictions)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in sorted(documents.items()):
        assert_secret_free(filename, text)
        path = output_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(written)


def build_bundle(
    repo_root: Path,
    output_dir: Path,
    *,
    generated_at: datetime,
    regenerate_goldens: bool = False,
    verification: Verification | None = None,
) -> tuple[Path, ...]:
    criteria = phase1_criteria()
    goldens = compute_goldens(repo_root)
    goldens_written = write_goldens(output_dir, goldens, criteria, regenerate=regenerate_goldens)

    resolved = verify_repository(repo_root) if verification is None else verification
    models = phase1_models(repo_root)
    captures = read_committed_role_captures(repo_root)
    refuse_a_capture_that_no_longer_holds(captures)
    run = read_committed_run_evidence(repo_root)
    refuse_a_run_record_that_no_longer_holds(run)
    decisions = open_decisions()
    rebuild_document = render_rebuild_comparison(repo_root)
    documents = {
        "unit-test-report.md": render_unit_test_report(resolved),
        "negative-case-matrix.md": render_matrix(criteria, resolved),
        "publisher-denial-matrix.md": render_run_evidence(run),
        "image-rebuild-comparison.md": rebuild_document,
        "open-decisions.md": render_open_decisions(decisions),
        "deployed-role-drift.md": render_role_drift(repo_root, captures),
        "schema-compatibility.md": render_schema_report(models),
        "README.md": render_index(
            generated_at=generated_at,
            commit_sha=source_commit_sha(repo_root),
            criteria=criteria,
            verification=resolved,
            goldens=goldens,
            models=models,
            captures=captures,
            run=run,
            rebuilds=recorded_rebuilds(repo_root),
            decisions=decisions,
            input_digests=input_digest_table(repo_root, captures),
            limitations=known_limitations(repo_root, criteria, captures, run),
        ),
    }
    if set(documents) | set(GOLDENS_FILENAMES) != set(BUNDLE_FILENAMES):
        raise ProofBundleError("the bundle wrote a different file set than it declares")
    contradictions = contradicting_status_claims(documents, criteria)
    if contradictions:
        raise ProofBundleError(
            "the bundle states a criterion status the acceptance gate did not reach; a "
            "reviewer who trusts this bundle without reading the suite would be misled:\n  "
            + "\n  ".join(contradictions)
        )
    for filename, text in sorted(documents.items()):
        assert_secret_free(filename, text)
    refuse_a_bundle_waiting_on_a_deploy(captures)
    written = list(goldens_written)
    for filename, text in sorted(documents.items()):
        path = output_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(sorted(written))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Phase 1 proof bundle under proof/phase-1/."
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--regenerate-goldens", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get(NESTED_RUN_ENV):
        print(
            "refusing to build the proof bundle from inside its own verification run",
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)
    repo_root = PROJECT_ROOT
    output_dir = default_output_dir(repo_root) if args.output_dir is None else Path(args.output_dir)
    generated_at = (
        datetime.now(tz=UTC)
        if args.generated_at is None
        else datetime.fromisoformat(args.generated_at)
    )
    try:
        written = build_bundle(
            repo_root,
            output_dir,
            generated_at=generated_at,
            regenerate_goldens=args.regenerate_goldens,
        )
    except (ProofBundleError, CriteriaDefinitionError) as error:
        print(str(error), file=sys.stderr)
        return 1
    for path in written:
        print(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
