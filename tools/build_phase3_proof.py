"""The Phase 3 proof bundle, written to ``proof/phase-3/``.

Mirrors ``tools/build_phase1_proof.py``: the same golden-digest tripwire and the same
refusal to overwrite a drifted one, the same nested verification run, the same secret scan
over every document before it is written, and the same rule that no sentence may give a
criterion a status the gate did not reach.

**Most of this bundle is empty, deliberately and with a reason in each hole.** Wave 5 is
held: no Phase 3 stack has been applied, no Batch job has ever run in this account, and no
lifecycle record exists. Seven of the documents below describe live evidence, and each is
written as explicitly empty, naming the capture that would fill it and the criterion it
would close. That is the only honest shape for them. Omitting them would make a reader think
the phase had fewer claims than it has; filling them with the templates' intentions would
make the bundle say a container ran.

The two probes this phase depends on carry their controls into ``measurement-method.md``,
which is a document rather than a section for one reason: an earlier revision of the Phase 3
plan opened with a confidently wrong finding produced by a plausible, specific, uncontrolled
policy simulation, and the correction is worth more than the finding was. A probe this phase
introduces carries its controls into the bundle, or its result does not count.
"""

from __future__ import annotations

import argparse
import json
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

from edullm_platform.batch_denials import (
    ADMISSION_BATCH_DENIED_ACTIONS,
    BATCH_PROBE_LESSONS,
    WORKLOAD_DENIED_ACTIONS,
)
from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
)
from edullm_platform.ec2_authorization import CONTROL_OBSERVATIONS
from edullm_platform.open_decisions import OpenDecision, open_decisions
from edullm_platform.phase3_criteria import phase3_criteria
from edullm_platform.phase3_evidence import AccountMeasurements
from edullm_platform.proof_bundle import (
    CITATION_LEGEND,
    GENERATOR_TEST_PATHS,
    STATUS_LEGEND,
    STATUS_PROSE,
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
from edullm_platform.role_drift import (
    PHASE3_ROLE_TEMPLATES,
    TemplateRole,
    load_template_roles,
)
from edullm_platform.status_prose import spell

PHASE: Final = "phase-3"
BUNDLE_SCHEMA_VERSION: Final = 1
BUNDLE_RELATIVE_DIR: Final = Path("proof") / PHASE
GOLDENS_FILENAME: Final = "serialization-goldens.json"
GOLDENS_REPORT_FILENAME: Final = "serialization-goldens.md"
GOLDENS_FILENAMES: Final = (GOLDENS_FILENAME, GOLDENS_REPORT_FILENAME)

BUNDLE_FILENAMES: Final = (
    "README.md",
    "batch-denial-matrix.md",
    "batch-execution-evidence.md",
    "cancellation-and-timeout-evidence.md",
    "deployed-role-drift.md",
    "event-evidence.md",
    "lineage-record-evidence.md",
    "log-stream-evidence.md",
    "measurement-method.md",
    "negative-case-matrix.md",
    "networking-evidence.md",
    "open-decisions.md",
    "rollback-evidence.md",
    "schema-compatibility.md",
    GOLDENS_FILENAME,
    GOLDENS_REPORT_FILENAME,
    "unit-test-report.md",
)

NESTED_RUN_ENV: Final = "EDULLM_PHASE3_PROOF_NESTED"
GENERATOR_TEST_PATH: Final = "tests/test_phase3_proof.py"
GENERATOR_COMMAND: Final = "uv run python tools/build_phase3_proof.py"

MEASUREMENTS_PATH: Final = "fixtures/evidence/phase-3/account-measurements.sanitized.json"

#: The committed artifacts Phase 3 owns, whose digests this bundle records so a reviewer can
#: confirm it describes the tree in front of them.
PHASE3_INPUTS: Final = (
    "infra/batch-network.yaml",
    "infra/batch-compute.yaml",
    "infra/batch-events.yaml",
    "infra/outputs-bucket.yaml",
    "infra/admission-state-machine.yaml",
    "infra/iam/batch-roles.yaml",
    "infra/iam/lifecycle-lambda-role.yaml",
    "infra/iam/admission-service-roles.yaml",
    "infra/iam/infra-deployer-role.yaml",
    ".github/workflows/deploy-phase3-batch.yml",
    ".github/workflows/submit-run.yml",
    "config/execution-targets.yaml",
    "config/workload-catalog.yaml",
    "config/image-exceptions.yaml",
    MEASUREMENTS_PATH,
)

#: The library modules Phase 3 added. The repository-wide contract inventory lives in the
#: Phase 0 bundle; repeating every row here would be a second copy going stale.
PHASE3_CONTRACT_MODULES: Final = (
    "edullm_platform.contracts.execution",
    "edullm_platform.contracts.image_scan",
    "edullm_platform.ec2_authorization",
    "edullm_platform.phase3_evidence",
    "edullm_platform.phase3_gate",
)

#: Test modules that carry Phase 3's evidence, by prefix. The reentrant ones are removed
#: rather than listed out, so a module added to that list is dropped from here too.
PHASE3_TEST_PREFIXES: Final = ("tests/test_phase3_", "tests/test_capture_phase3_")

VERIFICATION_COMMANDS: Final = (
    "uv run pytest -q",
    "uv run ruff check .",
    "uv run mypy",
    "uv run python tools/export_schemas.py",
    "uv run python tools/validate_phase3.py",
    GENERATOR_COMMAND,
)

GOLDENS_MISSING_GUIDANCE: Final = (
    "No recorded canonical digests were found at {path}. The Phase 3 proof bundle is the "
    f"source of this tripwire; generate it with `{GENERATOR_COMMAND}` and commit the result."
)

#: What every empty document says about why it is empty. One sentence rather than seven, so
#: a reader who has met it once knows every hole in this bundle has the same cause.
NOTHING_RAN: Final = (
    "**This document is empty, and it is empty for one reason.** Wave 5 is held: no Phase 3 "
    "stack has been applied to this account, no compute environment or job queue exists, and "
    "no Batch job has ever run here. There is nothing to record. It is generated empty rather "
    "than omitted because a bundle missing a document reads as a phase with fewer claims, and "
    "a reviewer counting what is here should count this too."
)


@dataclass(frozen=True)
class EmptySection:
    """One document that will hold live evidence and holds none yet."""

    filename: str
    title: str
    #: What this document exists to record, in the words the plan uses.
    records: str
    #: The capture that fills it, named precisely enough to go and take.
    filled_by: tuple[str, ...]
    #: Which criteria close when it is filled. Read against the definition, so a document
    #: cannot claim to serve a criterion the phase does not have.
    closes: tuple[str, ...]


EMPTY_SECTIONS: Final[tuple[EmptySection, ...]] = (
    EmptySection(
        filename="batch-execution-evidence.md",
        title="Phase 3 Batch execution evidence",
        records=(
            "The successful and failed Batch job ids, their compute environment, queue and "
            "job definition, the attempts array, the container exit codes, and the instance "
            "each job actually ran on."
        ),
        filled_by=(
            (
                "One accepted run carried through to SUCCEEDED, and one whose command exits "
                "non-zero carried through to FAILED."
            ),
            (
                "`aws batch describe-jobs` for each, captured and sanitized by field projection "
                "rather than by scanning afterwards: a Batch job detail carries the full "
                "container command and environment."
            ),
        ),
        closes=("1", "4", "15", "16"),
    ),
    EmptySection(
        filename="log-stream-evidence.md",
        title="Phase 3 log stream evidence",
        records=(
            "The CloudWatch log group and stream reference for each job, and the retrieved "
            "line proving the stream resolves. References rather than contents, per D8: the "
            "lineage store is immutable and a workload's stdout is the least predictable text "
            "this platform handles."
        ),
        filled_by=(
            (
                "The log stream name recorded on a captured binding, fetched back and returning "
                "the line the container printed."
            ),
        ),
        closes=("2", "19"),
    ),
    EmptySection(
        filename="event-evidence.md",
        title="Phase 3 EventBridge delivery evidence",
        records=(
            "The EventBridge deliveries, the event ids derived from them, and the captured "
            "refusal of the replayed duplicate."
        ),
        filled_by=(
            (
                "The delivery record for at least one job state change, with EventBridge's own "
                "event id beside the `evt_`-prefixed id derived from it."
            ),
            (
                "One event redelivered, and the conditional write's refusal captured as the error "
                "S3 returned."
            ),
        ),
        closes=("11", "18"),
    ),
    EmptySection(
        filename="lineage-record-evidence.md",
        title="Phase 3 lineage record evidence",
        records=(
            "The binding, event, attempt and result URIs with their VersionId and "
            "ChecksumSHA256, joined to the Phase 2 intent and decision for the same run id."
        ),
        filled_by=(
            (
                "`aws s3api head-object --checksum-mode ENABLED` for the binding, one event, the "
                "attempt and the result."
            ),
            (
                "The intent and decision records Phase 2 wrote for the same run id, so the join "
                "is shown rather than asserted."
            ),
        ),
        closes=("3", "17", "19"),
    ),
    EmptySection(
        filename="cancellation-and-timeout-evidence.md",
        title="Phase 3 cancellation and timeout evidence",
        records=(
            "The cancelled single job, the cancelled fan-out with both children, and the "
            "timed-out job, each with the reason Batch recorded."
        ),
        filled_by=(
            (
                "A cancellation path that can terminate a job. There is none: every Phase 3 role "
                "deliberately excludes `batch:TerminateJob`, and the state machine the plan routes "
                "cancellation through has not been written. This section needs a component built "
                "before it needs a run."
            ),
            (
                "A two-cell array job terminated at the parent, with both child job ids observed "
                "terminal rather than the parent alone."
            ),
            (
                "A job whose command sleeps past `attemptDurationSeconds`, observed FAILED with "
                "the timeout reason."
            ),
        ),
        closes=("5", "6", "7", "8"),
    ),
    EmptySection(
        filename="deployed-role-drift.md",
        title="Phase 3 deployed-role drift",
        records=(
            "The four new roles and the two amendments, compared against the templates that "
            "declare them."
        ),
        filled_by=(
            (
                "The four Phase 3 roles deployed from a laptop, then captured with "
                "`tools/capture_phase3_evidence.py` and committed."
            ),
            (
                "The two amended roles re-captured. The Phase 1 deployer capture is behind its "
                "template today and the difference is recorded as a pending amendment in "
                "`edullm_platform.pending_amendments`; that record has to be deleted in the same "
                "change as the re-capture, because its findings are compared for equality."
            ),
        ),
        closes=("13", "14"),
    ),
    EmptySection(
        filename="rollback-evidence.md",
        title="Phase 3 rollback rehearsal",
        records=(
            "The rollback executed rather than argued: the job queue disabled, the compute "
            "environment observed at zero desired vCPUs, the reviewers removed from both "
            "GitHub environments, and the states role redeployed without `batch:SubmitJob`."
        ),
        filled_by=(
            (
                "The rehearsal, recording the four things that make it a rehearsal rather than a "
                "description: that a submission dispatched after step 1 creates no Batch job; that "
                "a job running at step 1 still reaches a terminal state and still lands its result "
                "record; that `desiredvCpus` is observed at 0 after step 2 rather than assumed; and "
                "that a record written before step 1 is still readable afterwards."
            ),
        ),
        closes=("16",),
    ),
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
# What Phase 3 records golden digests for
# --------------------------------------------------------------------------------------


def committed_role(repo_root: Path, *, role_name: str, relative_path: str) -> TemplateRole:
    roles = load_template_roles(repo_root / relative_path)
    matching = [role for role in roles if role.role_name == role_name]
    if len(matching) != 1:
        raise ProofBundleError(f"{relative_path} does not declare exactly one {role_name}")
    return matching[0]


def compute_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    """One digest per Phase 3 role, over its projection rather than over the file.

    The same tripwire Phase 1 records, aimed at the four roles this phase adds. A comment
    or a reordered key changes the file and not the projection; a statement that grants one
    more action changes the projection whatever it does to the file.

    It is worth more here than it was there, for a reason particular to this moment. None
    of these roles is deployed, so there is no capture to compare any of them against and
    the drift comparison has nothing to run on. The recorded digest is the only thing
    standing between a template widened between now and the deploy and nobody noticing.
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
        for role_name, relative_path in PHASE3_ROLE_TEMPLATES
    )


# --------------------------------------------------------------------------------------
# Verifying the tree
# --------------------------------------------------------------------------------------


def phase3_test_modules(collected: Sequence[str]) -> tuple[str, ...]:
    modules = {
        node_id.split("::", 1)[0]
        for node_id in collected
        if node_id.startswith(PHASE3_TEST_PREFIXES)
    }
    return tuple(sorted(modules - set(REENTRANT_TEST_MODULES)))


def module_scoped_node_ids(collected: Sequence[str]) -> tuple[ModuleCoverage, ...]:
    return tuple(
        ModuleCoverage(
            module=module,
            node_ids=tuple(node_id for node_id in collected if node_id.split("::", 1)[0] == module),
        )
        for module in phase3_test_modules(collected)
    )


def verify_repository(repo_root: Path) -> Verification:
    criteria = phase3_criteria()
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
# The documents
# --------------------------------------------------------------------------------------


def read_measurements(repo_root: Path) -> AccountMeasurements:
    """The committed account measurements, or a refusal naming why they do not load.

    A stale record does not load at all, which is the point of it being a
    ``FreshEvidenceModel``. Two documents in this bundle are rendered from it, so the
    refusal happens here rather than as a half-rendered table.
    """
    path = repo_root / MEASUREMENTS_PATH
    try:
        return AccountMeasurements.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise ProofBundleError(
            "the committed account measurements no longer load, and this bundle's networking "
            "and measurement-method documents are rendered from them, so it would describe "
            "premises nobody has confirmed lately: "
            f"{error}"
        ) from error


def render_empty_section(section: EmptySection, checks: Sequence[CriterionSpec]) -> str:
    """One live-evidence document, empty, saying what would fill it.

    The criteria it names are read back against the definition, so a section cannot claim
    to serve a criterion this phase does not have, and the status word beside each is taken
    from the recorded status rather than typed -- which is what stops a sentence here
    disagreeing with the gate.
    """
    rows = [
        [number, STATUS_PROSE[recorded_status(checks, number)]] for number in section.closes
    ]
    return (
        "\n".join(
            [
                f"# {section.title}",
                "",
                NOTHING_RAN,
                "",
                "## What this document records",
                "",
                section.records,
                "",
                "## What would fill it",
                "",
                bullets(section.filled_by),
                "",
                "## Criteria waiting on it",
                "",
                table(["criterion", "status today"], rows),
                "",
                (
                    "Each of those is recorded in `src/edullm_platform/phase3_criteria.py` with "
                    "the same account of what is missing, and "
                    "`uv run python tools/validate_phase3.py` reports it. This document and "
                    "that definition are two views of one fact rather than two claims."
                ),
            ]
        )
        + "\n"
    )


def render_measurement_method(repo_root: Path) -> str:
    """The two probes this phase depends on, and the controls that make them believable."""
    measurements = read_measurements(repo_root)
    control_rows = [
        [
            f"`{control.action}`",
            control.region,
            control.expected.value,
            control.established_by,
        ]
        for control in CONTROL_OBSERVATIONS
    ]
    captured_rows = [
        [
            f"`{control.action}`",
            control.region,
            control.classified,
            control.expected,
            "yes" if control.agrees else "**no**",
        ]
        for control in measurements.controls
    ]
    return (
        "\n".join(
            [
                "# Phase 3 measurement method",
                "",
                (
                    "This document exists because an earlier revision of the Phase 3 plan opened "
                    "with a finding that was wrong. It reported that a service control policy "
                    "denies ten EC2 actions in both regions; seven of them are authorized in "
                    "`us-east-1`, and a peer principal under the same permissions boundary had "
                    "performed three of them hours earlier. The source was "
                    "`iam:SimulatePrincipalPolicy`, with `aws:RequestedRegion` supplied and "
                    "`--resource-arns` supplied, and it was believed because it was specific and "
                    "plausible."
                ),
                "",
                (
                    "The correction is worth less than the method that caught it, which is why "
                    "the method is a document rather than a paragraph. **A specific, plausible, "
                    "uncontrolled measurement is the shape of a confidently wrong answer.** Any "
                    "probe this phase introduces carries its controls here, or its result does "
                    "not count."
                ),
                "",
                "## Probe one: EC2 authorization, read with `--dry-run`",
                "",
                (
                    "EC2's dry run evaluates authorization and then stops, so it is the service's "
                    "own answer rather than a model of one, and nothing is created either way. "
                    "It distinguishes four outcomes where the simulator distinguishes two:"
                ),
                "",
                table(
                    ["what EC2 answers", "what it means"],
                    [
                        ["`DryRunOperation`", "authorized -- the request would have succeeded"],
                        ["`UnauthorizedOperation`", "denied"],
                        [
                            "`<Thing>LimitExceeded`",
                            (
                                "authorized, and there is no room. A quota is a support request; "
                                "a denial is not fixable by us. The simulator cannot tell these "
                                "apart at all."
                            ),
                        ],
                        [
                            "anything else",
                            (
                                "the request never reached authorization, so it says nothing "
                                "about the caller"
                            ),
                        ],
                    ],
                ),
                "",
                "### The controls, and how each verdict was established some other way",
                "",
                (
                    "Four captured answers, one per verdict, kept as literal CLI stderr in "
                    "`edullm_platform.ec2_authorization.CONTROL_OBSERVATIONS` so that a change to "
                    "the parsing is covered too. Each verdict is known independently of the "
                    "classifier being checked."
                ),
                "",
                table(
                    ["action", "region", "verdict", "established by"],
                    control_rows,
                ),
                "",
                (
                    "The fourth is the one worth reading twice. A `RunInstances` dry run naming "
                    "an AMI that does not exist is rejected before anybody is authorized, and "
                    "reading that as a denial would have reported an authorized action as "
                    "refused -- which is the same failure as the headline this document is about, "
                    "arriving by a different road."
                ),
                "",
                "### The same controls as the capture recorded them",
                "",
                (
                    f"Read from `{MEASUREMENTS_PATH}`, which is a "
                    "`FreshEvidenceModel` and refuses to load once it is older than the "
                    "freshness window. A matrix whose controls disagree is not a matrix with one "
                    "bad row; it is a matrix whose classifier is wrong, and the record says so in "
                    "a field rather than leaving a reader to notice."
                ),
                "",
                table(
                    ["action", "region", "classified", "expected", "agrees"],
                    captured_rows,
                ),
                "",
                "### The method, as the capture itself records it",
                "",
                measurements.method,
                "",
                "## Probe two: does an action support resource-level permissions?",
                "",
                (
                    "The Operating Environment's rule is that an action whose service "
                    "authorization reference lists no resource type can only be granted on `\"*\"`. "
                    "The reference page is currently a redirect stub, so the answer was measured "
                    "rather than read: grant the action on exactly one ARN in a custom policy, "
                    "then `iam:SimulateCustomPolicy` that action with `--resource-arns` naming "
                    "that same ARN. Resource-level support means the grant matches and the answer "
                    "is `allowed`; no resource type means IAM evaluates against `*`, the "
                    "ARN-scoped grant never matches, and the answer is `implicitDeny`."
                ),
                "",
                (
                    "This is still a simulator result, and the section above is a recent argument "
                    "for not trusting one. The difference is the controls, run on every "
                    "invocation:"
                ),
                "",
                table(
                    ["control", "answer", "known from"],
                    [
                        [
                            "`cloudformation:ValidateTemplate` against its own stack ARN",
                            "`implicitDeny`",
                            "a live Phase 1 deploy failure naming the action",
                        ],
                        [
                            "`cloudformation:DescribeStacks` against the same ARN",
                            "`allowed`",
                            "scopable, and scoped in the deployer today",
                        ],
                        [
                            "`logs:DescribeLogGroups` against a log-group ARN",
                            "`implicitDeny`",
                            "the second Phase 2 deploy failure",
                        ],
                    ],
                ),
                "",
                (
                    "All three behaved correctly, which is the reason to believe the rest. The "
                    "backstop is that the deploy fails closed: a missing grant surfaces as a "
                    "`CREATE_FAILED` naming the action, which is how this repository learned the "
                    "first two controls in the first place."
                ),
                "",
                "### Two corrections this method invites, both recorded rather than fixed quietly",
                "",
                bullets(
                    [
                        (
                            "`logs:GetLogEvents` first read as having no resource type. It has "
                            "one, and the wrong answer came from an ARN in the wrong form: "
                            "`log-group:<name>:log-stream:<stream>` returns `implicitDeny` and "
                            "`log-group:<name>:*` returns `allowed`. When this probe says "
                            "`implicitDeny`, check the ARN form before believing it."
                        ),
                        (
                            "IAM Access Analyzer's `validate-policy` looks like the right tool "
                            "and does not detect this class. Given a policy granting "
                            "`cloudformation:ValidateTemplate` on a stack ARN and "
                            "`logs:DescribeLogGroups` on a log-group ARN -- both known wrong -- it "
                            "returned zero findings, while correctly flagging "
                            "`ARN_REGION_NOT_ALLOWED` on a regional ARN for a global action in "
                            "the same document."
                        ),
                    ]
                ),
                "",
                "## What neither probe was allowed to be used for",
                "",
                bullets(
                    [
                        (
                            "`OrganizationsDecisionDetail` from `simulate-principal-policy` is "
                            "not a usable signal here. It reported `AllowedByOrganizations: "
                            "false` for actions that are demonstrably allowed, and returned the "
                            "same answer for both regions when the regions genuinely differ."
                        ),
                        (
                            "Neither probe says anything about quota, capacity or placement. A "
                            "compute environment reporting `VALID` is not evidence that a job "
                            "can run: Batch does not fail a job it cannot place, it waits."
                        ),
                    ]
                ),
            ]
        )
        + "\n"
    )


def request_id_note(request_id: str | None) -> str:
    """Why the quota request id is written with hyphens in it.

    A presentation change rather than a redaction, and worth a sentence because the obvious
    readings of it are both wrong: it is not masked, and the scanner was not widened.
    """
    if request_id is None:
        return (
            "No increase request id is recorded, so nothing here can be looked up in the "
            "console. That is a gap in the capture rather than a fact about the quota."
        )
    return (
        "The request id is written with a hyphen every "
        f"{len(request_id.split('-')[0])} characters, which is a presentation change rather "
        "than a redaction: every character AWS issued is still here and "
        "`edullm_platform.phase3_evidence.ungroup_opaque_identifier` reverses it exactly. A "
        "service-quotas request id is forty characters of `[A-Za-z0-9]`, which is precisely "
        "the shape the evidence secret scan refuses. Masking it would throw away the one "
        "field that lets a reader open the request; widening the scanner to admit "
        "forty-character runs would weaken the check everywhere to admit one identifier."
    )


def render_networking(repo_root: Path) -> str:
    """What the compute environment will run on, what it will not, and on whose terms."""
    measurements = read_measurements(repo_root)
    quota = measurements.vpc_quota
    request_id = quota.increase_request_id
    placement = measurements.placement
    region_rows = [
        [
            f"`{verdict.action}`",
            region.region,
            verdict.verdict,
            f"`{verdict.error_code}`" if verdict.error_code else "—",
        ]
        for region in measurements.regions
        for verdict in region.verdicts
    ]
    subnet_rows = [
        [
            subnet.availability_zone,
            "yes" if subnet.instance_type_offered else "**no**",
            "yes" if subnet.map_public_ip_on_launch else "no",
            str(subnet.available_ip_address_count),
        ]
        for subnet in placement.subnets
    ]
    return (
        "\n".join(
            [
                "# Phase 3 networking evidence",
                "",
                (
                    "The plan this phase was built from assumed the compute environment would "
                    "run in somebody else's VPC, and called that the phase's largest known "
                    "limitation. It does not, and this document records the terms it ended up "
                    "with instead -- which are better, and are different enough that reading the "
                    "plan's wording here would mislead."
                ),
                "",
                "## The quota, which was the longest pole and is closed",
                "",
                table(
                    ["fact", "value"],
                    [
                        ["quota", f"`{quota.quota_code}` VPCs per Region, {quota.region}"],
                        ["in use when measured", str(quota.in_use)],
                        ["value requested", str(quota.quota_value)],
                        ["request state", quota.increase_request_status or "none requested"],
                        ["request id", f"`{request_id}`" if request_id else "—"],
                        ["adjustable", "yes" if quota.adjustable else "no"],
                    ],
                ),
                "",
                request_id_note(request_id),
                "",
                (
                    "`us-east-1` held five VPCs against a quota of five on the morning of "
                    "2026-07-27 and a real `create-vpc` returned `VpcLimitExceeded`. That is an "
                    "entirely different kind of problem from an authorization denial, and telling "
                    "the two apart is what kept this phase in the only region that works. The "
                    "increase was filed, applied the same day, and confirmed by creating a VPC "
                    "and deleting it again, so `infra/batch-network.yaml` creates our own "
                    "unconditionally and nothing here is borrowed."
                ),
                "",
                "## The authorization matrix, both regions",
                "",
                (
                    "Measured with `--dry-run` against the real EC2 API. See "
                    "`measurement-method.md` for why, and for the four controls that make these "
                    "verdicts believable."
                ),
                "",
                table(["action", "region", "verdict", "error code"], region_rows),
                "",
                (
                    "**`us-east-2` is not a fallback and looks like one.** The master plan's "
                    "region lock permits both, so the obvious response to a full `us-east-1` is "
                    "to move. An EC2 compute environment there is not possible at all."
                ),
                "",
                "## The zones, and the one that would produce a job that waits",
                "",
                (
                    "Recorded per subnet rather than as a list of ids, because the fact that "
                    "matters is not which subnets exist but which of them can hold the instance "
                    "type the compute environment asks for. A subnet in a zone that cannot "
                    "produces a job stuck in `RUNNABLE` and no error anywhere, which is the least "
                    "debuggable failure this phase can have."
                ),
                "",
                table(
                    ["availability zone", "offers the instance type", "public", "free addresses"],
                    subnet_rows,
                ),
                "",
                "## Whose network this is",
                "",
                placement.borrowing_terms,
                "",
                "## What is still open",
                "",
                (
                    "The network stack is not deployed. Nothing here records the VPC, subnet or "
                    "security-group ids the compute environment actually uses, because there is "
                    "no compute environment, and criterion 21 is a gap for exactly that reason. "
                    "The measurements above describe the account and the candidate placement "
                    "these probes were aimed at; they are premises rather than a description of a "
                    "running system."
                ),
            ]
        )
        + "\n"
    )


def render_denial_matrix() -> str:
    """The two Phase 3 denial matrices, and what choosing a probe has cost."""
    sections = [
        "# Phase 3 denial matrices",
        "",
        (
            "Two matrices, one per identity, and neither has ever run. The admission matrix "
            "needs a real admission session, which needs a dispatched submission through a "
            "protected environment; the workload matrix runs from inside the container under "
            "the job role, so it cannot run before a job does. Both are written, wired and "
            "tested against recorded CLI output, and both are claims about templates until a "
            "session answers them."
        ),
        "",
        (
            "That distinction is the whole reason these matrices exist. Every other test of "
            "these roles reads a committed CloudFormation template, which is what the account "
            "was asked for rather than what it holds -- and a role widened in the console "
            "leaves every one of them green."
        ),
        "",
        "## The admission session, attempted before the one call it may make",
        "",
        (
            "Run in `submit-run.yml` under the environment-scoped session, after the approval "
            "gate and immediately before `StartExecution`. `batch:SubmitJob` was probed in "
            "Phase 1 against a queue that did not exist; the other three were hypothetical "
            "until this phase gave the account a queue, a job definition and jobs to describe."
        ),
        "",
        table(
            ["action", "why a permitted call would still change nothing"],
            [
                [
                    "`batch:SubmitJob`",
                    "the queue and the job definition named do not exist",
                ],
                ["`batch:TerminateJob`", "the job id is well formed and nothing minted it"],
                [
                    "`batch:RegisterJobDefinition`",
                    (
                        "it would create a definition under this project's own denial-probe "
                        "name, which nothing submits to"
                    ),
                ],
                ["`batch:DescribeJobs`", "a describe of an absent job reads nothing"],
            ],
        ),
        "",
        "## The workload session, attempted from inside the container",
        "",
        table(
            ["action", "what a refusal establishes"],
            [
                [
                    "`s3:PutObject` on the lineage bucket",
                    (
                        "the workload cannot forge an intent, a decision or a binding. Aimed at "
                        "the real bucket with `--if-none-match '*'`, because an invented bucket "
                        "is answered `NoSuchBucket` before anybody is authorized"
                    ),
                ],
                ["`batch:SubmitJob`", "a workload cannot launch compute outside admission"],
                [
                    "`states:StartExecution`",
                    "a workload cannot start an admission execution of its own",
                ],
                [
                    "`ecr:PutImage`",
                    (
                        "a workload cannot publish an image. Aimed at a repository beside the "
                        "registered one, never at it"
                    ),
                ],
            ],
        ),
        "",
        (
            "Both lists are read from `edullm_platform.batch_denials` rather than written here, "
            "so adding a probe or renaming an action changes this document rather than leaving "
            "it behind. Today they are "
            f"{spell(len(ADMISSION_BATCH_DENIED_ACTIONS))} and "
            f"{spell(len(WORKLOAD_DENIED_ACTIONS))} actions respectively."
        ),
        "",
        "## What choosing a probe has cost",
        "",
        (
            "Read this before adding one. Each entry is a rule some probe broke, with what "
            "taught it, because a rule with no incident attached reads as caution and gets "
            "skipped. Phase 1's list and Phase 2's both still apply; these are what the Batch "
            "and workload matrices added. Neither was learned from a run -- there are no "
            "credentials in the environment they were written in -- so each records what the "
            "templates and the services' documented behaviour say, and names the way it would "
            "fail if that turns out to be wrong."
        ),
        "",
    ]
    for lesson in BATCH_PROBE_LESSONS:
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
    sections.extend(
        [
            "## Why this document is not evidence yet",
            "",
            NOTHING_RAN,
            "",
            (
                "Criteria 12 and 13 rest on it and are gaps. What fills it is one live run of "
                "each matrix, its record uploaded as a workflow artifact, committed under "
                "`fixtures/evidence/phase-3/`, and a test that reads it -- with the CloudTrail "
                "event id of each refusal, so a reviewer can look any of them up in the account."
            ),
        ]
    )
    return "\n".join(sections).rstrip() + "\n"


def render_open_decisions(decisions: Sequence[OpenDecision]) -> str:
    """Questions this phase surfaced or inherited, and the one it answered."""
    sections = [
        "# Phase 3 open decisions",
        "",
        (
            "A criterion records something that must be true and whether it is. This records "
            "something nobody has decided. A gap means unfinished work and a deferral means a "
            "postponement with a trigger; neither fits a question whose answer is a policy "
            "choice, and a question like that has two fates if it is not written down. It is "
            "settled by accident by whoever first trips over it, or silently by whoever happens "
            "to be implementing near it."
        ),
        "",
        "## The one this phase answered",
        "",
        (
            "Decision 1, on whether a registry scan result may block a publish, was Phase 1's "
            "and landed here: the only image this platform has ever published carries four "
            "critical and eight high findings, all inherited from the pinned base, and blocking "
            "on a severity threshold would have refused this phase's own workload."
        ),
        "",
        (
            "It is gone from the register rather than edited to agree with what was built, "
            "which is what the register's own rule requires. The answer went a way the options "
            "did not list as obvious: block unless an exception is recorded against the exact "
            "digest, enforced at admission rather than at publish -- because ECR scans after the "
            "push, so a publish-time refusal would leave that commit permanently unpublishable. "
            "It lives in `contracts/image_scan.py`, in `config/policy.yaml`'s `image_scan` block "
            "and its `image_scan_findings_unreviewed` condition, in `config/image-exceptions.yaml`, "
            "and in `tests/test_phase3_image_scan.py`. Criterion 22 cites the absence and the "
            "enforcement together, because either alone would be satisfied by the other's "
            "failure."
        ),
        "",
        "## The ones still open",
        "",
        (
            "None of these has a recommendation, and none may have one. The source is "
            "`src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than two "
            "options, so an entry cannot become a decision by having its alternatives deleted."
        ),
        "",
        table(
            ["#", "question", "has to be answered"],
            [[decision.number, decision.question, decision.lands_in] for decision in decisions],
        ),
    ]
    if not decisions:
        sections.extend(
            [
                "",
                (
                    "**The register is empty, and that is a state rather than an absence.** "
                    "An empty table here does not mean nobody looked; it means every question "
                    "this repository surfaced has been answered and the answer put where it "
                    "is enforced. `src/edullm_platform/open_decisions.py` names each one that "
                    "has left and where its answer now lives, which is the only place that "
                    "history is kept."
                ),
            ]
        )
    sections.append("")
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


def render_unit_test_report(verification: Verification) -> str:
    full = verification.full_suite
    selected = verification.selected
    rows = [
        [entry.module, str(len(entry.node_ids)), "pass" if selected.green else "see below"]
        for entry in verification.module_coverage
    ]
    sections = [
        "# Phase 3 unit-test report",
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
            "modules Phase 3 added, executed as one selection."
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
            "The test modules Phase 3 added, excluding the ones that invoke a gate or this "
            "generator; those run in the reviewer's own `uv run pytest -q`."
        ),
        "",
        table(["module", "tests", "result"], rows),
        "",
        (
            "**A green suite is not evidence that the path works.** Phase 1 shipped one over a "
            "workflow that could not complete a run and Phase 2 shipped one over a state "
            "machine that could not complete an execution, both times because both sides of a "
            "seam were asserted and neither compared to the other. The counts above say the "
            "tests pass; `negative-case-matrix.md` says what they establish, which for most of "
            "this phase's criteria is not the criterion."
        ),
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
                "# Phase 3 golden canonical digests",
                "",
                (
                    f"The canonical JSON digest of each of the {spell(len(goldens))} roles Phase "
                    "3 adds, taken over the projection the drift comparison acts on rather than "
                    "over the file."
                ),
                "",
                (
                    "A comment, a reordered key or a whitespace change alters the file and not "
                    "the projection, and does not land here. A statement that grants one more "
                    "action alters the projection whatever it does to the file, and does. The "
                    "digest is `sha256` over `canonical_json_bytes(TemplateRole)`, the same "
                    "function that produces manifest digests in lineage records."
                ),
                "",
                (
                    "This tripwire is worth more in Phase 3 than it was in Phase 1, for a reason "
                    "particular to this moment: none of these roles is deployed, so there is no "
                    "capture to compare any of them against and the drift comparison has nothing "
                    "to run on. Until the laptop deploy lands, the recorded digest is the only "
                    "thing standing between a template widened in the meantime and nobody "
                    "noticing."
                ),
                "",
                table(["role", "template", "canonical bytes", "digest"], rows),
                "",
                "## How this fails",
                "",
                (
                    f"`{GOLDENS_FILENAME}` in this directory is the machine-readable copy. "
                    "`tests/test_phase3_golden.py` reprojects each template, recomputes its "
                    "digest and compares it to the recorded value, one test per role so a "
                    "failure names the role rather than the batch."
                ),
                "",
                (
                    f"`{GENERATOR_COMMAND}` refuses to overwrite a drifted digest. Re-recording "
                    "requires `--regenerate-goldens`, so a change to what a role may do cannot be "
                    "absorbed by re-running the generator."
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


def phase3_models(repo_root: Path) -> tuple[ModelRecord, ...]:
    return tuple(
        record for record in model_records(repo_root) if record.module in PHASE3_CONTRACT_MODULES
    )


def render_schema_report(models: Sequence[ModelRecord]) -> str:
    rows = [
        [
            record.name,
            record.module,
            "base" if record.base else "record",
            record.schema_version,
            "yes" if record.exported else "no",
            record.structural_digest,
        ]
        for record in models
    ]
    return (
        "\n".join(
            [
                "# Phase 3 schema compatibility report",
                "",
                (
                    f"The {spell(len(models))} contract models Phase 3 added. The structural "
                    "digest is `sha256` over the model's JSON schema with sorted keys, so it "
                    "changes when a field is added, removed, retyped or reconstrained, and does "
                    "not change when unrelated code moves."
                ),
                "",
                (
                    "Phase 3 also exported six models that Phase 0 defined and nothing had ever "
                    "constructed: `LogicalRun`, `SchedulerAttempt`, `LifecycleEvent`, "
                    "`CheckpointManifest`, `ResultManifest` and `BatchJobBinding`. They are not "
                    "repeated here -- the repository-wide inventory is in "
                    "`proof/phase-0/schema-compatibility.md`, and a second copy is a copy that "
                    "goes stale -- but the export is what makes them reviewable by somebody who "
                    "does not read Python."
                ),
                "",
                table(
                    ["model", "module", "kind", "schema_version", "exported", "structural digest"],
                    rows,
                ),
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
        "# Phase 3 negative-case matrix",
        "",
        (
            f"The {spell(len(criteria))} Phase 3 acceptance criteria, mapped to the tests cited "
            "for each one by node id. Each cited node id was collected and executed by this "
            "generator before the bundle was written; a citation pytest cannot collect aborts "
            "generation rather than being printed."
        ),
        "",
        (
            "This mapping is defined once, in `src/edullm_platform/phase3_criteria.py`. The "
            "acceptance gate reads the same definition and executes the same node ids, so this "
            "matrix and `tools/validate_phase3.py` cannot disagree."
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
                    "of these. Relabelling them would turn the gate green without anything "
                    "changing in the account, which is the one thing this matrix exists to make "
                    "impossible to do quietly."
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


def known_limitations(
    checks: Sequence[CriterionSpec],
    goldens: Sequence[RecordedGolden],
) -> tuple[str, ...]:
    """What this bundle does not establish, read off the tree rather than remembered.

    No entry states a criterion status of its own: where one names a check, the status word
    comes from ``checks``, so a limitation cannot disagree with the verdict the gate
    reached. ``contradicting_status_claims`` refuses the bundle if one ever does.
    """

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    return (
        (
            "Nothing has been deployed and nothing has run. This is the limitation every other "
            "one below is a consequence of: no Phase 3 stack has been applied to the account, "
            "no Batch job has ever run in it, and no lifecycle record exists. Seven documents "
            "in this bundle are therefore empty with a reason rather than absent."
        ),
        (
            f"Check 1 -- that a valid run reaches SUCCEEDED -- is {status_of('1')}, and it is "
            "the phase's central claim. A reviewer should read this bundle as a description of "
            "a system that has been built and not yet operated."
        ),
        (
            f"Check 20 is {status_of('20')} on a committed CloudFormation template, which is "
            "what the repository asks the account for rather than what the account holds. The "
            "four Phase 3 roles have no capture at all, so the comparison that catches a role "
            "widened in the console does not run for any of them, and check 14 is "
            f"{status_of('14')} for that reason."
        ),
        (
            f"Check 22 is {status_of('22')} because the open-decisions entry is gone and the "
            "answer is enforced in code and configuration this repository commits. Nothing here "
            "says the enforcement has ever refused a real submission."
        ),
        (
            "A compute environment reporting VALID would not be evidence that a job can run. "
            "Batch does not fail a job it cannot place; it waits. Only a job observed in "
            "RUNNING and then SUCCEEDED establishes placement, egress and the image pull, which "
            "is why checks 1 and 15 are separate."
        ),
        (
            "The account measurements this bundle's networking and method documents are "
            "rendered from expire thirty days after they were observed, and this generator "
            "refuses to build once they do. Nothing about the account will have changed on that "
            "date; what will have lapsed is anybody's knowledge of it."
        ),
        (
            "The recorded role digests are over four templates nobody has deployed. They catch "
            "a template widened between now and the deploy, and they say nothing about the "
            "account, because there is no account state to say anything about yet."
        ),
        (
            "The two denial matrices are written and have never run. Until a real session "
            "answers them, every claim about what these roles cannot do rests on a document "
            "rather than on a refusal."
        ),
        (
            "The nested verification run excludes every test module that builds a proof bundle "
            f"({', '.join(GENERATOR_TEST_PATHS)}), because those tests invoke a generator and "
            "would recurse. They run in the reviewer's own `uv run pytest -q`."
        ),
        (
            "This bundle describes the working tree at generation time, which may differ from "
            "the commit named above. The input digests recorded in the bundle index identify "
            "exactly what was measured."
        ),
        (
            "Nothing forces this bundle to stay current. It is a snapshot, and its counts go "
            f"stale as soon as a test is added or a template changes. Re-run "
            f"`{GENERATOR_COMMAND}` and read the diff before accepting a phase gate."
        ),
    )


def standing(gap_numbers: Sequence[str]) -> str:
    if gap_numbers:
        return "It is not done, and the Result table below says by how much."
    return (
        "Every criterion is covered and the gate is green, which is the state in which a "
        "bundle is most worth reading carefully: the Known limitations below say what each "
        "criterion does not cover, and `open-decisions.md` says what this phase surfaced and "
        "did not settle."
    )


def gate_verdict(gap_numbers: Sequence[str]) -> str:
    if not gap_numbers:
        return (
            "`tools/validate_phase3.py` exits 0 against this tree: every phase criterion is "
            "covered or explicitly deferred."
        )
    if len(gap_numbers) == 1:
        subject = f"criterion {gap_numbers[0]} is a GAP"
    else:
        subject = f"criteria {', '.join(gap_numbers)} are GAPs"
    return (
        "`tools/validate_phase3.py` exits 1 against this tree. Phase 3 is not accepted: "
        f"{subject}. That is the honest state of the phase, not a broken gate. Read the Gaps "
        "section of `negative-case-matrix.md` for what closes it."
    )


def input_digest_table(repo_root: Path) -> tuple[tuple[str, str], ...]:
    return tuple((path, file_digest(repo_root / path)) for path in sorted(PHASE3_INPUTS))


def render_index(
    *,
    generated_at: datetime,
    commit_sha: str,
    criteria: Sequence[CriterionSpec],
    verification: Verification,
    goldens: Sequence[RecordedGolden],
    models: Sequence[ModelRecord],
    decisions: Sequence[OpenDecision],
    input_digests: Sequence[tuple[str, str]],
    limitations: Sequence[str],
) -> str:
    covered_numbers = [check.number for check in criteria if check.status is CriterionStatus.COVERED]
    deferred_numbers = [
        check.number for check in criteria if check.status is CriterionStatus.DEFERRED
    ]
    gap_numbers = [check.number for check in criteria if check.status is CriterionStatus.GAP]
    return (
        "\n".join(
            [
                "# Phase 3 proof bundle",
                "",
                f"Phase: {PHASE}",
                f"Bundle schema version: {BUNDLE_SCHEMA_VERSION}",
                f"Source commit: {commit_sha}",
                f"Generated: {generated_at.astimezone(UTC).isoformat(timespec='seconds')}",
                "",
                (
                    "This bundle exists so that a reviewer can decide whether Phase 3 is done "
                    "without reading the test suite. Everything it claims was executed by "
                    f"`{GENERATOR_COMMAND}` at generation time. {standing(gap_numbers)}"
                ),
                "",
                (
                    "**Read this first.** Phase 3's claim is that one manifest becomes one "
                    "container that runs on AWS Batch and lands its records in lineage. The "
                    "software for that is built and tested. None of it has been deployed, and no "
                    "Batch job has ever run in this account, because Wave 5 -- the laptop IAM "
                    "stacks, the CI stacks and the live matrix -- is held. Seven of the documents "
                    "below are therefore empty with a reason in each, and the Result table shows "
                    "how few criteria that leaves standing."
                ),
                "",
                "## Contents",
                "",
                bullets(
                    [
                        (
                            f"`negative-case-matrix.md` — each of the {spell(len(criteria))} "
                            "Phase 3 acceptance criteria mapped to the tests cited for it, by "
                            "node id, with every gap stated. Read this one first."
                        ),
                        (
                            "`measurement-method.md` — the two probes this phase depends on and "
                            "the controls that make them believable. Included because an earlier "
                            "revision of the plan opened with a confidently wrong finding from "
                            "an uncontrolled simulation, and the correction is worth less than "
                            "the method that caught it."
                        ),
                        (
                            "`networking-evidence.md` — the dry-run authorization matrix for both "
                            "regions, the VPC quota and its increase, the availability zones, and "
                            "whose network the compute environment will run on."
                        ),
                        (
                            "`batch-denial-matrix.md` — the two matrices, what each probe is aimed "
                            "at so a permitted call changes nothing, and what choosing a probe has "
                            "cost. Neither has run."
                        ),
                        (
                            "`open-decisions.md` — the question this phase answered and moved to "
                            "where it is enforced, and the ones still open."
                        ),
                        (
                            "`serialization-goldens.md` and `"
                            + GOLDENS_FILENAME
                            + "` — the recorded canonical digest of what each Phase 3 role "
                            "template grants, and the tripwire that fails when one drifts."
                        ),
                        (
                            "`schema-compatibility.md` — the contract models Phase 3 added, with "
                            "their structural digests."
                        ),
                        (
                            "`unit-test-report.md` — summarised pass and fail counts, per module "
                            "and for the whole suite, with the commands to reproduce them."
                        ),
                        (
                            "`batch-execution-evidence.md`, `log-stream-evidence.md`, "
                            "`event-evidence.md`, `lineage-record-evidence.md`, "
                            "`cancellation-and-timeout-evidence.md`, `deployed-role-drift.md` and "
                            "`rollback-evidence.md` — empty. Each says what it records, what "
                            "would fill it, and which criteria are waiting on it."
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
                        ["role templates with recorded digests", str(len(goldens))],
                        ["roles compared to a capture", "0"],
                        ["Batch jobs run", "0"],
                        ["lineage records written by this phase", "0"],
                        ["denial matrices executed", "0"],
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
    """Record what each Phase 3 role template grants, before anything else is written.

    Written first and as a pair, on the same terms Phase 1's are and for the same reason:
    both documents are derived from templates this repository commits, neither says anything
    about the account, and re-recording one without the other leaves two committed documents
    in one directory disagreeing about what a role grants.
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
    criteria = phase3_criteria()
    goldens = compute_goldens(repo_root)
    goldens_written = write_goldens(output_dir, goldens, criteria, regenerate=regenerate_goldens)

    resolved = verify_repository(repo_root) if verification is None else verification
    models = phase3_models(repo_root)
    decisions = open_decisions()
    documents = {
        "unit-test-report.md": render_unit_test_report(resolved),
        "negative-case-matrix.md": render_matrix(criteria, resolved),
        "measurement-method.md": render_measurement_method(repo_root),
        "networking-evidence.md": render_networking(repo_root),
        "batch-denial-matrix.md": render_denial_matrix(),
        "open-decisions.md": render_open_decisions(decisions),
        "schema-compatibility.md": render_schema_report(models),
    }
    for section in EMPTY_SECTIONS:
        documents[section.filename] = render_empty_section(section, criteria)
    documents["README.md"] = render_index(
        generated_at=generated_at,
        commit_sha=source_commit_sha(repo_root),
        criteria=criteria,
        verification=resolved,
        goldens=goldens,
        models=models,
        decisions=decisions,
        input_digests=input_digest_table(repo_root),
        limitations=known_limitations(criteria, goldens),
    )
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
    written = list(goldens_written)
    for filename, text in sorted(documents.items()):
        path = output_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(sorted(written))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Phase 3 proof bundle under proof/phase-3/."
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
