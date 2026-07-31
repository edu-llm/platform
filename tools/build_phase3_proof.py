"""The Phase 3 proof bundle, written to ``proof/phase-3/``.

Mirrors ``tools/build_phase1_proof.py``: the same golden-digest tripwire and the same
refusal to overwrite a drifted one, the same nested verification run, the same secret scan
over every document before it is written, and the same rule that no sentence may give a
criterion a status the gate did not reach.

**Six documents that were empty now hold what four completed runs left behind.** They are
rendered from the captures committed under ``fixtures/evidence/phase-3/`` and read through
:mod:`edullm_platform.phase3_capture`, which holds those records to agreeing with one
another; if they stop agreeing, or expire, this generator refuses to build rather than
describing runs nobody has confirmed lately. The same rule the account measurements have
always carried, applied to the runs.

**One document is still empty, and the machinery for that stays.** ``event-evidence.md``
serves two checks that need a redelivered EventBridge event and an inventory of the whole
lineage store, neither of which an ordinary run produces. It is written as explicitly empty,
naming what would fill it and the criteria waiting on it. That is the only honest shape for
it: omitting it would make a reader think the phase had fewer claims than it has, and
filling it with the templates' intentions would make the bundle say something was observed.

The two probes this phase depends on carry their controls into ``measurement-method.md``,
which is a document rather than a section for one reason: an earlier revision of the Phase 3
plan opened with a confidently wrong finding produced by a plausible, specific, uncontrolled
policy simulation, and the correction is worth more than the finding was. A probe this phase
introduces carries its controls into the bundle, or its result does not count.
"""

from __future__ import annotations

import json
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
    CriterionSpec,
    CriterionStatus,
)
from edullm_platform.ec2_authorization import CONTROL_OBSERVATIONS
from edullm_platform.open_decisions import OpenDecision, open_decisions
from edullm_platform.phase1_capture import read_committed_role_captures
from edullm_platform.phase3_capture import (
    PHASE3_CAPTURE_DIR,
    TRACEABLE_ARTIFACTS,
    CommittedPhase3Evidence,
    CommittedPhase3Run,
    read_committed_phase3_evidence,
)
from edullm_platform.phase3_criteria import phase3_criteria
from edullm_platform.phase3_evidence import AccountMeasurements
from edullm_platform.proof_bundle import (
    CITATION_LEGEND,
    GENERATOR_TEST_PATHS,
    SCOPE_IS_NOT_AUTHORSHIP,
    STATUS_LEGEND,
    STATUS_PROSE,
    GoldenDigestDriftError,
    ModelRecord,
    ProofBundleError,
    RecordedGolden,
    assert_secret_free,
    bullets,
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
    source_commit_sha,
    status_label,
    table,
)
from edullm_platform.proof_generator import (
    Coherence,
    Verification,
    bundle_directory,
    gate_verdict,
    goldens_path,
    run_generator_cli,
    standing,
)
from edullm_platform.proof_generator import (
    establish_coherence as shared_establish_coherence,
)
from edullm_platform.proof_generator import (
    render_unit_test_report as shared_render_unit_test_report,
)
from edullm_platform.proof_generator import (
    verify_repository as shared_verify_repository,
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

#: What an empty document says about why it is empty. Six of the seven that once carried
#: this now hold evidence; the sentence stayed general rather than being deleted with them,
#: because the next phase will open with holes of its own.
NOTHING_RAN: Final = (
    "**This document is empty, and it is empty because nothing has produced what it "
    "records.** The stacks are applied and four runs have completed, so the reason is no "
    "longer that the phase is undeployed -- it is that the observations this document exists "
    "to hold are not observations an ordinary run produces. What would produce them is listed "
    "below. It is generated empty rather than omitted because a bundle missing a document "
    "reads as a phase with fewer claims, and a reviewer counting what is here should count "
    "this too."
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


#: The one live-evidence document still empty, and the criteria waiting on it. The
#: other six were empty until four runs completed; each is now rendered from the
#: committed captures, and this machinery stays for the next phase that needs it.
EMPTY_SECTIONS: Final[tuple[EmptySection, ...]] = (
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
)


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
                "## What the compute environment actually landed on",
                "",
                (
                    "Everything above is a premise: it describes the account and the placement "
                    "these probes were aimed at, measured before any stack was applied. The "
                    "table below is different in kind -- it is read back from the deployed "
                    "compute environment, so it says where this project's jobs actually run "
                    "rather than where a template asked for them to. A stack applied from a "
                    "laptop can land somewhere other than its template says, and a record "
                    "copied from the template would agree with itself forever."
                ),
                "",
                deployed_placement_table(repo_root),
            ]
        )
        + "\n"
    )


def deployed_placement_table(repo_root: Path) -> str:
    """The deployed environment's own networking, or a note saying nothing is captured.

    Rendered from the committed capture rather than from the template. Absent rather than
    invented when no capture is committed: a bundle that filled this in from the template
    would be asserting a placement nobody observed.
    """
    evidence = read_committed_phase3_evidence(repo_root)
    found = evidence.compute_environment
    if found is None:
        return (
            "No capture of the deployed compute environment is committed under "
            f"`{PHASE3_CAPTURE_DIR}/`, so nothing here records the networking it uses."
        )
    return table(
        ["fact", "value"],
        [
            ["compute environment", f"`{found.compute_environment_name}`"],
            ["status", f"{found.status}, {found.state}"],
            ["VPC", f"`{found.vpc_id}`"],
            ["subnets", ", ".join(f"`{subnet}`" for subnet in found.subnet_ids)],
            [
                "security groups",
                ", ".join(f"`{group}`" for group in found.security_group_ids),
            ],
            ["instance types", ", ".join(f"`{shape}`" for shape in found.instance_types) or "—"],
            [
                "vCPUs, min / desired / max",
                f"{found.minimum_vcpus} / {found.desired_vcpus} / {found.maximum_vcpus}",
            ],
            ["observed", found.observed_at.date().isoformat()],
        ],
    )


# --------------------------------------------------------------------------------------
# What the completed runs left behind
# --------------------------------------------------------------------------------------


def read_runs(repo_root: Path) -> CommittedPhase3Evidence:
    """The committed run captures, or a refusal to build a bundle that would describe them.

    Held to the same rule as the account measurements: five documents below are rendered
    from these records, so a bundle built on captures that have expired or stopped
    agreeing with each other would describe a system nobody has confirmed lately. Refusing
    here is what stops that being discovered by a reader instead.
    """
    evidence = read_committed_phase3_evidence(repo_root)
    if not evidence.holds:
        problems = [
            f"{problem.record}: {problem.reason}"
            for run in evidence.runs
            for problem in run.problems
        ] + [f"{problem.record}: {problem.reason}" for problem in evidence.problems]
        raise ProofBundleError(
            "the committed Phase 3 run captures no longer hold, and five of this bundle's "
            "documents are rendered from them, so it would describe runs nobody has "
            "confirmed lately: " + "; ".join(sorted(problems))
        )
    return evidence


def _run_label(run: CommittedPhase3Run) -> str:
    return f"`{run.run_id}`"


def render_batch_execution(repo_root: Path) -> str:
    """The Batch jobs themselves, as the service describes them rather than as we recorded."""
    evidence = read_runs(repo_root)
    environment = evidence.compute_environment
    rows = []
    for run in evidence.runs:
        job = run.job
        if job is None:
            rows.append(
                [_run_label(run), "—", "no job", "—", "refused before submission"]
            )
            continue
        rows.append(
            [
                _run_label(run),
                f"`{job.batch_job_id}`",
                job.status,
                "—" if job.container_exit_code is None else str(job.container_exit_code),
                job.status_reason or "—",
            ]
        )
    return (
        "\n".join(
            [
                "# Phase 3 Batch execution evidence",
                "",
                (
                    "What Batch says about each job this platform submitted, read back with "
                    "`describe-jobs` and projected field by field rather than scanned "
                    "afterwards -- a Batch job detail carries the full container command and "
                    "environment, so a capture that sanitized by scanning would be one "
                    "unrecognised field away from committing a workload's arguments."
                ),
                "",
                (
                    "The exit code column is the one that earns its place. A result record "
                    "says a run failed; only the exit code separates a command that returned "
                    "non-zero, which has one, from a job the scheduler killed, which does not."
                ),
                "",
                table(
                    ["run", "Batch job id", "status", "container exit", "reason Batch gave"],
                    rows,
                ),
                "",
                "## The compute environment these ran on",
                "",
                (
                    "Read from the deployed environment after every run above had finished. "
                    "`desiredvCpus` is the reading that matters: `minvCpus` is what the "
                    "template asks for and cannot catch an environment that scaled up and did "
                    "not come back down."
                ),
                "",
                table(
                    ["fact", "value"],
                    (
                        [
                            ["compute environment", f"`{environment.compute_environment_name}`"],
                            ["status", f"{environment.status}, {environment.state}"],
                            ["job queues routing to it", ", ".join(f"`{q}`" for q in environment.job_queue_names)],
                            [
                                "vCPUs, min / desired / max",
                                (
                                    f"{environment.minimum_vcpus} / "
                                    f"{environment.desired_vcpus} / "
                                    f"{environment.maximum_vcpus}"
                                ),
                            ],
                            ["observed", environment.observed_at.date().isoformat()],
                        ]
                        if environment is not None
                        else [["compute environment", "no capture is committed"]]
                    ),
                ),
            ]
        )
        + "\n"
    )


def render_log_streams(repo_root: Path) -> str:
    """The streams each job recorded, and the lines fetched back out of them."""
    evidence = read_runs(repo_root)
    sections = [
        "# Phase 3 log stream evidence",
        "",
        (
            "The stream each job recorded, fetched back and returning the line its container "
            "printed. The stream and not the group: a group name reads as complete and "
            "resolves to every job on the queue, so a record carrying one looks healthy and "
            "locates nothing."
        ),
        "",
        (
            "The lines are reproduced here because these are smoke commands whose output this "
            "repository wrote. That is a deliberate exception to D8's rule that references "
            "travel rather than contents, and it does not generalise: a research workload's "
            "stdout is the least predictable text this platform handles and belongs behind a "
            "reference."
        ),
        "",
    ]
    for run in evidence.runs:
        logs = run.logs
        sections.append(f"## {run.run_id}")
        sections.append("")
        if logs is None:
            sections.append(
                "Refused before submission, so no container ran and no stream exists."
            )
            sections.append("")
            continue
        sections.extend(
            [
                table(
                    ["fact", "value"],
                    [
                        ["log group", f"`{logs.log_group_name}`"],
                        ["log stream", f"`{logs.log_stream_name}`"],
                        ["lines retrieved", str(len(logs.lines))],
                        ["truncated", "yes" if logs.truncated else "no"],
                    ],
                ),
                "",
                "```",
                *logs.lines,
                "```",
                "",
            ]
        )
    return "\n".join(sections) + "\n"


def render_lineage_records(repo_root: Path) -> str:
    """Every object the runs wrote, with what S3 attests, and the joins between them."""
    evidence = read_runs(repo_root)
    sections = [
        "# Phase 3 lineage record evidence",
        "",
        (
            "What S3 attests about every object these runs wrote. The writers asking for a "
            "checksum and the store having computed one are different claims: the first is "
            "read from the state machine definition elsewhere in this bundle, and only "
            "`head-object --checksum-mode ENABLED` establishes the second."
        ),
        "",
        (
            "`loads` is the column worth reading twice. Three bindings here are attested, "
            "versioned and intact -- S3 holds exactly the bytes it was sent -- and are refused "
            "by the contract that defines what a binding is, because they were written before "
            "the `\"Result\": null` fix in the admission state machine and carry a whole "
            "admission payload where a fan-out size belongs. The lineage store is write-once, "
            "so those objects are permanent and no future capture repairs them."
        ),
        "",
    ]
    for run in evidence.runs:
        attestation = run.lineage
        sections.append(f"## {run.run_id}")
        sections.append("")
        if attestation is None:
            sections.extend(["No attestation is committed for this run.", ""])
            continue
        sections.extend(
            [
                table(
                    ["key", "kind", "bytes", "canonical", "loads", "VersionId", "ChecksumSHA256"],
                    [
                        [
                            f"`{record.key.rsplit('/', 1)[-1]}`",
                            record.record_kind,
                            str(record.content_length),
                            "yes" if record.canonical else "**no**",
                            "yes" if record.loads_as_contract else "**no**",
                            f"`{record.version_id}`",
                            f"`{record.checksum_sha256}`",
                        ]
                        for record in attestation.objects
                    ],
                ),
                "",
                (
                    f"Traceable end to end: **{'yes' if run.traceable else 'no'}**"
                    + (
                        ""
                        if run.traceable
                        else f" — unresolved: {', '.join(run.unresolved_artifacts)}."
                    )
                ),
                "",
            ]
        )
    traceable = [run for run in evidence.runs if run.traceable]
    sections.extend(
        [
            "## The eleven artifacts one run id has to resolve to",
            "",
            (
                "Named rather than counted, in `phase3_capture.TRACEABLE_ARTIFACTS`, because a "
                "check that counted eleven would go on passing after somebody removed one and "
                "added another."
            ),
            "",
            ", ".join(f"`{name}`" for name in TRACEABLE_ARTIFACTS) + ".",
            "",
            (
                f"{spell(len(traceable))} of the {spell(len(evidence.runs))} captured runs "
                "resolve all eleven. The others are the runs holding a binding that will never "
                "load, and they are reported as not traceable rather than as nearly traceable: "
                "an unbroken chain is the claim, and a chain missing a link is not a chain."
            ),
        ]
    )
    return "\n".join(sections) + "\n"


def render_cancellation_and_timeout(repo_root: Path) -> str:
    """The timeout, which fired, beside the cancellation path, which does not exist."""
    evidence = read_runs(repo_root)
    timed_out = [run for run in evidence.runs if run.job is not None and run.job.timed_out]
    sections = [
        "# Phase 3 cancellation and timeout evidence",
        "",
        (
            "Two halves of one document and they are in opposite states. The timeout has been "
            "observed stopping a real job. Cancellation has not been observed at all, because "
            "there is nothing to observe: no component in this account may terminate a job."
        ),
        "",
        "## The timeout, which fired",
        "",
    ]
    if not timed_out:
        sections.extend(["No captured run was stopped by its timeout.", ""])
    for run in timed_out:
        job = run.job
        binding = run.body("binding")
        assert job is not None
        sections.extend(
            [
                table(
                    ["fact", "value"],
                    [
                        ["run", _run_label(run)],
                        ["attempt duration sent to Batch", f"{binding['attempt_duration_seconds']}s" if binding else "—"],
                        [
                            "ran for",
                            f"{int((job.stopped_at - job.started_at).total_seconds())}s"
                            if job.started_at and job.stopped_at
                            else "—",
                        ],
                        ["status", job.status],
                        ["reason Batch gave", job.status_reason or "—"],
                        ["container exit", "none — the scheduler stopped it"],
                    ],
                ),
                "",
                (
                    "The absent exit code is the load-bearing part. A job the scheduler killed "
                    "never got to return a status, so anything in that field would mean the "
                    "command finished on its own and the timeout was a coincidence."
                ),
                "",
            ]
        )
    sections.extend(
        [
            "## Cancellation, which does not exist",
            "",
            (
                "Every Phase 3 role deliberately excludes `batch:TerminateJob`, and the state "
                "machine the plan routes cancellation through has not been written. So this "
                "half needs a component built before it needs a run, which is why the three "
                "checks that used to wait on it are no longer Phase 3's. They moved to the "
                "phase that will build cancellation, on the reasoning that a check nobody in "
                "this phase can close is a gate held permanently red rather than a measurement, "
                "and that cancellation is better owned by the work that will deliver it."
            ),
            "",
            (
                "The bound that makes the absence survivable is the timeout above. With a "
                "mandatory attempt duration in force and demonstrably enforced, the cost of "
                "being unable to cancel is the remainder of one job rather than an open-ended "
                "amount."
            ),
            "",
            (
                "Cancelling the GitHub workflow does not stop the Batch job. The submit job "
                "records that where an operator will read it rather than implying otherwise by "
                "silence, and the tests over that notice are still in the suite. Where it does "
                "not yet appear is a pilot limitations page, which this repository does not "
                "have -- and with no check in this phase left to fail over it, that page is now "
                "the only thing that would put the absence in front of a user."
            ),
        ]
    )
    return "\n".join(sections) + "\n"


def render_deployed_role_drift(repo_root: Path) -> str:
    """The four Phase 3 roles as deployed, compared to the templates that declare them."""
    captures = read_committed_role_captures(
        repo_root,
        capture_dir=repo_root / PHASE3_CAPTURE_DIR / "roles",
        role_templates=PHASE3_ROLE_TEMPLATES,
    )
    return (
        "\n".join(
            [
                "# Phase 3 deployed-role drift",
                "",
                (
                    "The four roles this phase creates, captured from the account and compared "
                    "to the templates that declare them. This is the only check in the bundle "
                    "that can see a role widened in a console: every other test of these roles "
                    "reads a committed template, which is what the account was asked for rather "
                    "than what it holds."
                ),
                "",
                table(
                    ["role", "template", "verdict"],
                    [
                        [
                            f"`{capture.role_name}`",
                            f"`{capture.template_path}`" if capture.template_path else "—",
                            capture.verdict.value,
                        ]
                        for capture in captures
                    ],
                ),
                "",
                "## What this does not cover",
                "",
                (
                    "Two roles the checks about separation of authority are actually about are "
                    "not here. `sbsandbox-intern-edullm-admission-lambda` and "
                    "`sbsandbox-intern-edullm-admission-states` are registered in "
                    "`PHASE2_ROLE_TEMPLATES`, so a capture of them belongs to Phase 2's evidence "
                    "and Phase 2's freshness window rather than being copied here. Until they "
                    "are captured, the claim that the validator could not have submitted the "
                    "job rests on a template."
                ),
                "",
                (
                    "A policy declining to permit an action is also not AWS refusing one. The "
                    "workload role's deployed policy grants no lineage write and no way to start "
                    "anything, and that is what these captures establish; the denial matrix is "
                    "the only thing that shows a call being turned down, and the workload half "
                    "of it has not run."
                ),
                "",
                (
                    "One thing these captures did find, recorded here rather than left for a "
                    "later phase to discover: the deployed workload role permits `s3:PutObject` "
                    "under `teams/*/runs/*` rather than under one team's prefix. The template "
                    "agrees, so it is deliberate rather than drift, and for a single-team pilot "
                    "nothing is misattributed -- but the cross-team isolation the `teams/` "
                    "segment exists to make expressible is not expressed yet."
                ),
            ]
        )
        + "\n"
    )


def render_rollback(repo_root: Path) -> str:
    """The rehearsal, which has not been performed, and why no check is waiting on it now."""
    evidence = read_runs(repo_root)
    environment = evidence.compute_environment
    return (
        "\n".join(
            [
                "# Phase 3 rollback rehearsal",
                "",
                (
                    "**The rehearsal has not been performed.** Rolling back this phase means "
                    "disabling the job queue, letting the compute environment drain to zero "
                    "desired vCPUs, removing the reviewers from both GitHub environments, and "
                    "redeploying the states role without `batch:SubmitJob`. Each of those has "
                    "been written down; none has been executed, and a rollback nobody has run "
                    "is a plan rather than a rehearsal."
                ),
                "",
                (
                    "What would make it a rehearsal rather than a description is recording four "
                    "things: that a submission dispatched after the queue is disabled creates no "
                    "Batch job; that a job already running still reaches a terminal state and "
                    "still lands its result record; that `desiredvCpus` is observed at zero "
                    "afterwards rather than assumed; and that a record written before the "
                    "rollback is still readable after it."
                ),
                "",
                "## Why no check is waiting on this",
                "",
                (
                    "This document used to carry the check that the compute environment holds no "
                    "capacity when idle, on the reasoning that draining it was part of the "
                    "rollback. That check closed a different way: the environment was observed "
                    "at zero desired vCPUs after four runs had finished, in the ordinary course "
                    "of running them, which is the same reading taken without tearing anything "
                    "down."
                ),
                "",
                (
                    "So the rehearsal is still worth doing, and nothing in the acceptance list "
                    "is waiting for it. That is recorded here rather than quietly dropped, "
                    "because work nobody is blocked on is exactly the kind that stops being "
                    "done and then stops being remembered."
                ),
                "",
                table(
                    ["fact", "value"],
                    [
                        ["rehearsal performed", "**no**"],
                        [
                            "desired vCPUs when last observed",
                            str(environment.desired_vcpus) if environment else "—",
                        ],
                        [
                            "observed",
                            environment.observed_at.date().isoformat() if environment else "—",
                        ],
                    ],
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
            "Two matrices, one per identity, and they are in different states. The admission "
            "matrix has run: it executes inside the submit job against a real admission "
            "session issued through a protected environment, before the one call that session "
            "makes, and every completed submission passed it. The workload matrix has not, "
            "because it runs from inside the container under the job role, and every command "
            "run there so far has printed a line and exited."
        ),
        "",
        (
            "Having run is not the same as being recorded here. The admission matrix writes "
            "its result to a GitHub Actions artifact with a thirty-day retention, which is "
            "somewhere this repository does not read and cannot cite, so the check that rests "
            "on it stays open until the artifact is captured into the evidence tree and a "
            "test reads it."
        ),
        "",
        (
            "That distinction is the whole reason these matrices exist. Every other test of "
            "these roles reads a committed CloudFormation template, which is what the account "
            "was asked for rather than what it holds -- and a role widened in the console "
            "leaves every one of them green. The four roles this phase creates are now also "
            "captured from the account and compared, which closes that gap for them; the "
            "matrices remain the only thing that shows AWS refusing a call rather than a "
            "policy declining to permit one."
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
            (
                "The two halves fall short for different reasons and neither is that the phase "
                "is undeployed. The admission matrix has run against real sessions and its "
                "result is a GitHub Actions artifact this repository cannot cite; the workload "
                "matrix has never run, because it executes inside the container and no command "
                "run there has invoked it."
            ),
            "",
            (
                "Criteria 12 and 13 rest on this and are gaps. What fills it is each matrix's "
                "record committed under `fixtures/evidence/phase-3/` and a test that reads it -- "
                "with the CloudTrail event id of each refusal, so a reviewer can look any of "
                "them up in the account. For the admission half that is a capture of an "
                "artifact that already exists; for the workload half it is a container image "
                "carrying the probe, which this repository does not build."
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
                    f"The {spell(len(models))} contract models defined by the modules this "
                    "bundle's evidence is built from, so that a reviewer can check a shape "
                    "without reading the whole inventory. The structural digest is `sha256` "
                    "over the model's JSON schema with sorted keys, so it changes when a field "
                    "is added, removed, retyped or reconstrained, and does not change when "
                    "unrelated code moves."
                ),
                "",
                SCOPE_IS_NOT_AUTHORSHIP,
                "",
                (
                    "Six models that Phase 0 defined and nothing had ever constructed were "
                    "exported during Phase 3: `LogicalRun`, `SchedulerAttempt`, "
                    "`LifecycleEvent`, `CheckpointManifest`, `ResultManifest` and "
                    "`BatchJobBinding`. They are not repeated here -- they are in the complete "
                    "inventory, and a second copy is a copy that goes stale -- but the export "
                    "is what makes them reviewable by somebody who does not read Python."
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
            "Everything live here rests on four runs, and four is a small number. One "
            "succeeded, one exited non-zero deliberately, one was stopped by its timeout and "
            "one was refused before submission. That is enough to establish that each path "
            "works once; it is not a sample from which anything about reliability follows."
        ),
        (
            f"Check 1 -- that a valid run reaches SUCCEEDED -- is {status_of('1')}, and it is "
            "the phase's central claim. A reviewer should read this bundle as a description of "
            "a system that has been operated a handful of times rather than one in service."
        ),
        (
            "This phase still cannot stop a job it has started, and nothing in the list of "
            "checks below says so any more. No component in the account holds "
            "`batch:TerminateJob`, and the three checks that used to record the absence "
            "moved to the phase that will build cancellation -- so the acceptance list is a "
            "measure of what Phase 3 can be held to, and this sentence is the only thing in "
            "the bundle that tells a reviewer the capability is missing. What bounds the "
            "exposure is the mandatory attempt duration, which has been observed stopping a "
            "real job."
        ),
        (
            f"Check 20 is {status_of('20')} on a committed CloudFormation template, which is "
            "what the repository asks the account for rather than what the account holds. The "
            "four Phase 3 roles are now captured from the account and compared, so that gap is "
            "closed for them; the two roles the validator and the state machine hold belong to "
            f"Phase 2's registry and are not, which is why check 14 is {status_of('14')}."
        ),
        (
            f"Check 22 is {status_of('22')} because the open-decisions entry is gone and the "
            "answer is enforced in code and configuration this repository commits. Every run so "
            "far named an image whose findings are carried by a recorded exception, so the gate "
            "has been evaluated and passed and has never had to refuse anything."
        ),
        (
            "A compute environment reporting VALID is not on its own evidence that a job can "
            "run. Batch does not fail a job it cannot place; it waits. Only a job observed in "
            "RUNNING and then SUCCEEDED establishes placement, egress and the image pull, which "
            "is why checks 1 and 15 are separate and are cited separately."
        ),
        (
            "Three lineage bindings will never load. They were written before the "
            '`"Result": null` fix in the admission state machine and carry an admission payload '
            "where a fan-out size belongs; the store is write-once, so they are permanent. The "
            "runs holding one are reported as not traceable end to end rather than as nearly "
            "traceable, and the corrupt bodies are described in the attestation rather than "
            "committed, because they carry an approver's name and a full image scan."
        ),
        (
            "The captures every live check rests on expire thirty days after they were "
            "observed, and this generator refuses to build once they do. Nothing about the runs "
            "will have changed on that date -- every object is still in a write-once store -- "
            "and what will have lapsed is anybody's knowledge of the account they are in."
        ),
        (
            "The admission denial matrix has run against a real session and its result lives in "
            "a GitHub Actions artifact with a thirty-day retention, which is somewhere this "
            "repository cannot cite. The workload matrix has not run at all: it executes inside "
            "the container, and no command run there has ever invoked it. So every claim about "
            f"what the workload role cannot do rests on a policy, and check 13 is {status_of('13')}."
        ),
        (
            "The deployed workload role permits writes under `teams/*/runs/*` rather than under "
            "one team's prefix, so it can write into any team's output location. The template "
            "agrees, so this is deliberate rather than drift, and for a single-team pilot "
            "nothing is misattributed -- but the cross-team isolation the `teams/` segment "
            "exists to make expressible is not expressed yet."
        ),
        (
            "The rollback rehearsal has not been performed. It is written down and no "
            "acceptance check is waiting on it, which is exactly the condition in which work "
            "stops being done and then stops being remembered."
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
                    f"`{GENERATOR_COMMAND}` at generation time. {standing(gap_numbers, deferred_numbers)}"
                ),
                "",
                (
                    "**Read this first.** Phase 3's claim is that one manifest becomes one "
                    "container that runs on AWS Batch and lands its records in lineage. That has "
                    "happened. Four submissions have gone from GitHub through OIDC, admission, "
                    "Batch and EventBridge to S3: one succeeded, one exited non-zero "
                    "deliberately, one was stopped by its own timeout, and one was refused "
                    "before anything could be launched. What they left behind is captured and "
                    "committed, and the checks that rest on it cite tests that read those "
                    "records rather than describing what a run would show."
                ),
                "",
                (
                    "What is not done is captures rather than mechanism, which is a change in "
                    "this bundle rather than only in the account. Four checks name an "
                    "observation no completed run produced, and two need a shape of capture the "
                    "per-run records cannot make; nothing left in the list waits on code being "
                    "written. The Result table below says which. Cancellation is the one "
                    "capability this phase describes and does not have, and it is no longer "
                    "measured here -- read the Known limitations for where it went."
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
                            "cost. The admission matrix has run against real sessions; the "
                            "workload one has not."
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
                            "`schema-compatibility.md` — the contract models the modules "
                            "behind this bundle define, with the structural digest of each "
                            "and what makes one move."
                        ),
                        (
                            "`unit-test-report.md` — summarised pass and fail counts, per module "
                            "and for the whole suite, with the commands to reproduce them."
                        ),
                        (
                            "`batch-execution-evidence.md`, `log-stream-evidence.md`, "
                            "`lineage-record-evidence.md`, `cancellation-and-timeout-evidence.md` "
                            "and `deployed-role-drift.md` — what four completed runs left "
                            "behind, rendered from the captures committed under "
                            f"`{PHASE3_CAPTURE_DIR}/`."
                        ),
                        (
                            "`rollback-evidence.md` — the rollback rehearsal, which has not been "
                            "performed. It says so, and says why nothing in the acceptance list "
                            "is waiting for it."
                        ),
                        (
                            "`event-evidence.md` — empty. It says what it records, what would "
                            "fill it, and which criteria are waiting on it."
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
                        ["contract models in schema-compatibility.md", str(len(models))],
                    ],
                ),
                "",
                "## Verification commands",
                "",
                "Run these from the repository root.",
                "",
                command_block(VERIFICATION_COMMANDS),
                "",
                gate_verdict(gap_numbers, phase_number=3),
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
        "batch-execution-evidence.md": render_batch_execution(repo_root),
        "log-stream-evidence.md": render_log_streams(repo_root),
        "lineage-record-evidence.md": render_lineage_records(repo_root),
        "cancellation-and-timeout-evidence.md": render_cancellation_and_timeout(repo_root),
        "deployed-role-drift.md": render_deployed_role_drift(repo_root),
        "rollback-evidence.md": render_rollback(repo_root),
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

    sys.exit(main())


# --------------------------------------------------------------------------------------
# The shared generator machinery, named locally so call sites and tests read unchanged.
#
# What moved is the part that was identical across phases 1 to 3: the CLI, the nested
# verification run, the per-module scoping, the unit-test report, and the two verdict
# sentences. What stayed is every renderer whose content is this phase's rather than
# every phase's -- measured at 20% to 60% textually common, which is not duplication.
# --------------------------------------------------------------------------------------


def default_output_dir(repo_root: Path) -> Path:
    return bundle_directory(repo_root, PHASE)


def establish_coherence(repo_root: Path) -> Coherence:
    return shared_establish_coherence(
        repo_root,
        criteria=phase3_criteria(),
        nested_env=NESTED_RUN_ENV,
        test_prefixes=PHASE3_TEST_PREFIXES,
    )


def verify_repository(repo_root: Path) -> Verification:
    return shared_verify_repository(
        repo_root,
        criteria=phase3_criteria(),
        nested_env=NESTED_RUN_ENV,
        test_prefixes=PHASE3_TEST_PREFIXES,
    )


def render_unit_test_report(verification: Verification) -> str:
    return shared_render_unit_test_report(
        verification,
        phase_number=3,
        verification_commands=VERIFICATION_COMMANDS,
        caveat=(
            "**A green suite is not evidence that the path works.** Phase 1 shipped one over a "
            "workflow that could not complete a run and Phase 2 shipped one over a state "
            "machine that could not complete an execution, both times because both sides of a "
            "seam were asserted and neither compared to the other. The counts above say the "
            "tests pass; `negative-case-matrix.md` says what they establish, which for most of "
            "this phase's criteria is not the criterion."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_generator_cli(
        argv,
        description="Build the Phase 3 proof bundle under proof/phase-3/.",
        repo_root=PROJECT_ROOT,
        nested_env=NESTED_RUN_ENV,
        default_output_dir=default_output_dir,
        build=build_bundle,
    )


if __name__ == "__main__":
    sys.exit(main())
