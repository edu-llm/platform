"""The Phase 2 proof bundle, written to ``proof/phase-2/``.

Mirrors ``tools/build_phase1_proof.py``: the same golden-digest tripwire and the same
refusal to overwrite a drifted one, the same nested verification run, the same secret scan
over every document before it is written, and the same rule that no sentence may give a
criterion a status the gate did not reach.

**Phase 2 is not accepted, and this bundle has to say so on its first screen.** The gate
exits 1: eight of the twenty-two criteria are gaps. That is not a broken gate. The path ran
end to end on 2026-07-27 -- a lead released their own routine run, an exception routed to
the admin gate, a duplicate execution name was refused, a tampered hash was refused and
still earned a decision record, and a six-probe denial matrix came back refused on every
entry -- and what is committed is a capture of the *state* those runs left behind rather
than of the runs. A criterion whose statement can only be established by evidence nobody
committed is a gap however convincing the run was to whoever watched it, so the index
names the eight before it names anything else.

Three kinds of document come out of that, and the distinction is the one thing to
understand before reading any of them:

*Rendered from a committed capture.* The lineage store, the admission executions, the
GitHub environment and secret configuration and the membership of the team that reviews
the lead gate were captured and are read here. Ten criteria are covered on the strength of
them, which is why :func:`read_captures` refuses the whole build if any of them has
expired or stopped loading: a bundle that printed those ten as covered after the capture
lapsed would state a status the gate does not reach.

*Rendered, and carrying no evidence.* ``admission-denial-matrix.md`` describes a matrix
that has run live and was never captured. It is rendered from the probe definitions rather
than omitted, because the matrix is the thing a reviewer needs to read before deciding
whether its result would mean anything -- and it closes by saying plainly that it holds no
refusal.

*Explicitly empty.* Two documents describe evidence nobody has captured at all. Each is
generated empty, names the capture that would fill it, and lists the criteria waiting on
it with each status read off the definition rather than typed. Omitting them would make the
phase look like it has fewer claims than it has.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.admission_denials import (
    ADMISSION_DENIED_ACTIONS,
    ADMISSION_PROBE_LESSONS,
    LINEAGE_PROBE_KEY,
)
from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import DecisionRecord, IntentRecord
from edullm_platform.contracts.authorization import AuthorizationDecision, evaluate_authorization
from edullm_platform.contracts.decision_matrix import AuthorizationScenario
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy, RequestFacts
from edullm_platform.criteria import (
    CriterionSpec,
    CriterionStatus,
)
from edullm_platform.evidence import FRESHNESS_WINDOW
from edullm_platform.open_decisions import OpenDecision, open_decisions
from edullm_platform.phase2_criteria import phase2_criteria
from edullm_platform.phase2_evidence import (
    PHASE2_ROLE_TEMPLATES,
    AdmissionExecutionInventory,
    EnvironmentInventory,
    LeadTeamMembership,
    LineageInventory,
    SecretInventory,
)
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
from edullm_platform.role_drift import TemplateRole, load_template_roles
from edullm_platform.status_prose import spell, status_summary_sentence

PHASE: Final = "phase-2"
BUNDLE_SCHEMA_VERSION: Final = 1
BUNDLE_RELATIVE_DIR: Final = Path("proof") / PHASE
GOLDENS_FILENAME: Final = "serialization-goldens.json"
GOLDENS_REPORT_FILENAME: Final = "serialization-goldens.md"
GOLDENS_FILENAMES: Final = (GOLDENS_FILENAME, GOLDENS_REPORT_FILENAME)

BUNDLE_FILENAMES: Final = (
    "README.md",
    "admission-denial-matrix.md",
    "admission-execution-evidence.md",
    "approval-gate-evidence.md",
    "authorization-matrix.md",
    "deployed-role-drift.md",
    "lineage-record-evidence.md",
    "negative-case-matrix.md",
    "oidc-session-evidence.md",
    "open-decisions.md",
    "schema-compatibility.md",
    GOLDENS_FILENAME,
    GOLDENS_REPORT_FILENAME,
    "unit-test-report.md",
)

NESTED_RUN_ENV: Final = "EDULLM_PHASE2_PROOF_NESTED"
GENERATOR_TEST_PATH: Final = "tests/test_phase2_proof.py"
GENERATOR_COMMAND: Final = "uv run python tools/build_phase2_proof.py"

EVIDENCE_DIR: Final = "fixtures/evidence/phase-2"
ENVIRONMENTS_PATH: Final = f"{EVIDENCE_DIR}/github/environments.sanitized.json"
SECRETS_PATH: Final = f"{EVIDENCE_DIR}/github/secrets.sanitized.json"
LEAD_TEAM_PATH: Final = f"{EVIDENCE_DIR}/github/lead-team.sanitized.json"
EXECUTIONS_PATH: Final = f"{EVIDENCE_DIR}/executions.sanitized.json"
LINEAGE_PATH: Final = f"{EVIDENCE_DIR}/lineage.sanitized.json"
RECORDS_DIR: Final = f"{EVIDENCE_DIR}/lineage/records"
SCENARIO_DIR: Final = "fixtures/authorization"

CaptureModel = (
    EnvironmentInventory
    | SecretInventory
    | LeadTeamMembership
    | AdmissionExecutionInventory
    | LineageInventory
)

#: Every committed capture this bundle rests on, with the model that reads it back. One
#: tuple rather than a list inside :func:`read_captures`, because three separate places
#: have to agree about it -- what is loaded, what governs the expiry date, and what the
#: refusal test breaks -- and the lead-team capture is here because it was added to none
#: of them. It was committed, cited by criteria 9 and 15, measured in the digest table by
#: an ``rglob``, and never loaded: backdating it moved no date and refused no build.
CAPTURE_SOURCES: Final[tuple[tuple[str, type[CaptureModel]], ...]] = (
    (ENVIRONMENTS_PATH, EnvironmentInventory),
    (SECRETS_PATH, SecretInventory),
    (LEAD_TEAM_PATH, LeadTeamMembership),
    (EXECUTIONS_PATH, AdmissionExecutionInventory),
    (LINEAGE_PATH, LineageInventory),
)

CAPTURE_TOOL: Final = "tools/capture_phase2_evidence.py"

#: The committed artifacts Phase 2 owns, whose digests this bundle records so a reviewer
#: can confirm it describes the tree in front of them. The committed captures are read off
#: the tree instead of listed, so a record added to the evidence directory is measured
#: without a second edit here.
PHASE2_INPUTS: Final = (
    "infra/iam/admission-role.yaml",
    "infra/iam/admission-service-roles.yaml",
    "infra/iam/infra-deployer-role.yaml",
    "infra/admission-state-machine.yaml",
    "infra/lineage-bucket.yaml",
    ".github/workflows/submit-run.yml",
    ".github/workflows/deploy-phase2-admission.yml",
    "config/organization.yaml",
    "config/policy.yaml",
)

#: The library modules Phase 2 added. The repository-wide contract inventory lives in the
#: Phase 0 bundle; repeating every row here would be a second copy going stale.
PHASE2_CONTRACT_MODULES: Final = (
    "edullm_platform.admission_denials",
    "edullm_platform.contracts.admission",
    "edullm_platform.phase2_evidence",
    "edullm_platform.phase2_gate",
)

#: Test modules that carry Phase 2's evidence, by prefix. The reentrant ones are removed
#: rather than listed out, so a module added to that list is dropped from here too.
PHASE2_TEST_PREFIXES: Final = ("tests/test_phase2_", "tests/test_capture_phase2_")

VERIFICATION_COMMANDS: Final = (
    "uv run pytest -q",
    "uv run ruff check .",
    "uv run mypy",
    "uv run python tools/export_schemas.py",
    "uv run python tools/validate_phase2.py",
    GENERATOR_COMMAND,
)

#: What every explicitly empty document says about why it is empty. One sentence rather
#: than two, so a reader who has met it once knows both holes in this bundle have the same
#: cause -- and the cause is not the one Phase 3's empty documents have. Phase 3 had run
#: nothing. Phase 2 ran everything and captured the aftermath.
NOTHING_CAPTURED: Final = (
    "**This document is empty, and it is empty for one reason.** What it would describe has "
    "already happened, and nothing captured it. Phase 2's path went end to end on "
    "2026-07-27, and its three roles were deployed from a laptop the same day; what "
    f"`{CAPTURE_TOOL}` records is the state that left behind -- the lineage objects, the "
    "execution list, the GitHub configuration -- and not the artifact this document is for. "
    "It is generated empty rather than omitted because a bundle missing a document reads as "
    "a phase with fewer claims, and a reviewer counting what is here should count this too."
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
        filename="oidc-session-evidence.md",
        title="Phase 2 OIDC session evidence",
        records=(
            "The CloudTrail AssumeRoleWithWebIdentity records for the accepted path and "
            "the refused path, each with the subject from "
            "responseElements.subjectFromWebIdentityToken, the audience and the provider."
        ),
        filled_by=(
            (
                "The refused call is the one worth having and it has already happened on "
                "every live run. The `deny-unapproved` job sits in the submission workflow so "
                "that the environment is the only variable -- same repository, same workflow "
                "ref, same branch -- and it succeeded each time, meaning STS refused the "
                "ref-based subject with `AccessDenied`."
            ),
            (
                "The accepted call beside it, so the pair shows one subject admitted and the "
                "other refused rather than a refusal on its own."
            ),
            (
                "Retries designed around CloudTrail's documented fifteen-minute delivery "
                "window rather than the roughly three minutes Phase 1 happened to observe."
            ),
        ),
        closes=("6", "7"),
    ),
    EmptySection(
        filename="deployed-role-drift.md",
        title="Phase 2 deployed-role drift",
        records=(
            "The three roles Phase 2 creates and the amended deployer, each compared "
            "against the committed template that declares it, in both directions."
        ),
        filled_by=(
            (
                "The three Phase 2 roles captured from the account and committed, then added "
                "to `role_drift.COMMITTED_ROLE_TEMPLATES` so the comparison Phase 1 runs for "
                "its two roles runs for these as well."
            ),
            (
                "A comparison that re-runs, in place of the one somebody did once by eye. Both "
                "roles behind criterion 19 were read back from IAM by hand on 2026-07-27 and "
                "matched, with the Lambda role carrying CloudWatch Logs on its own log group "
                "and no S3 action of any kind. Reading a role by hand establishes what was "
                "true that afternoon; nothing re-checks it, and that difference is the whole "
                "of what this document is for."
            ),
        ),
        closes=("6", "19"),
    ),
)


@dataclass(frozen=True)
class CommittedEvidence:
    """Everything Phase 2 captured, loaded, with the records beside their inventory."""

    environments: EnvironmentInventory
    secrets: SecretInventory
    lead_team: LeadTeamMembership
    executions: AdmissionExecutionInventory
    lineage: LineageInventory
    intents: tuple[tuple[str, IntentRecord], ...]
    decisions: tuple[tuple[str, DecisionRecord], ...]

    @property
    def expires_on(self) -> str:
        """When the earliest of these captures stops loading, as a date.

        The earliest and not each one's own, because the bundle is refused as a whole:
        the captures were not taken on the same day -- the GitHub environments on
        2026-07-27 and the lead team on 2026-07-31 -- and a criterion resting on both is
        only as current as the older of them.
        """
        observed = [
            self.environments.observed_at,
            self.secrets.observed_at,
            self.lead_team.observed_at,
            self.executions.observed_at,
            self.lineage.observed_at,
        ]
        return (min(observed) + FRESHNESS_WINDOW).date().isoformat()


# --------------------------------------------------------------------------------------
# What Phase 2 records golden digests for
# --------------------------------------------------------------------------------------


def committed_role(repo_root: Path, *, role_name: str, relative_path: str) -> TemplateRole:
    roles = load_template_roles(repo_root / relative_path)
    matching = [role for role in roles if role.role_name == role_name]
    if len(matching) != 1:
        raise ProofBundleError(f"{relative_path} does not declare exactly one {role_name}")
    return matching[0]


def compute_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    """One digest per Phase 2 role, over its projection rather than over the file.

    The same tripwire Phase 1 records, aimed at the three roles this phase adds. A comment
    or a reordered key changes the file and not the projection; a statement that grants one
    more action changes the projection whatever it does to the file.

    None of these three appears in ``role_drift.COMMITTED_ROLE_TEMPLATES``, because no
    capture of any of them exists, so the drift comparison has nothing to run on and
    criteria 6 and 19 are gaps for that reason. Until a capture lands, the recorded digest
    is the only thing standing between one of these roles being widened in a template and
    nobody noticing.
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
        for role_name, relative_path in PHASE2_ROLE_TEMPLATES
    )


# --------------------------------------------------------------------------------------
# Verifying the tree
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# The committed captures, or a refusal naming the one that stopped holding
# --------------------------------------------------------------------------------------


def read_lineage_payloads(repo_root: Path, kind: str) -> tuple[tuple[str, object], ...]:
    """Every committed record of one kind, decoded, whichever shape it was written in.

    Two shapes exist in the store and both are real history: records written after
    2026-07-27 are the canonical object, and records written before are a JSON string that
    contains the object. A reader handling one shape would either skip the older records or
    fail on them, and both would misreport what the store holds.
    """
    directory = repo_root / RECORDS_DIR / kind
    payloads: list[tuple[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        parsed = json.loads(path.read_bytes())
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        payloads.append((path.stem, parsed))
    if not payloads:
        raise ProofBundleError(f"no committed {kind} record was found under {RECORDS_DIR}/{kind}")
    return tuple(payloads)


def read_captures(repo_root: Path) -> CommittedEvidence:
    """The committed Phase 2 captures, or a refusal naming why one does not load.

    Ten criteria are recorded as covered on the strength of these records, and the
    matrix prints the recorded status. So once a capture expires, drifts or stops loading,
    the gate fails those criteria and this bundle would still print them covered -- which
    is the one defect a bundle cannot survive. Refusing is also what makes the expiry
    visible: somebody has to re-capture, or delete the records and the citations.

    Every capture is loaded from :data:`CAPTURE_SOURCES` rather than from a list written
    here, because a capture this function does not open is a capture that cannot refuse
    anything. The lead-team record was committed on 2026-07-31, cited by criteria 9 and
    15, and left out of this loop: backdating it past the freshness window changed no date
    in the bundle and stopped no build, so the generator would have printed both criteria
    covered while the gate failed them.
    """
    loaded: dict[str, CaptureModel] = {}
    for relative_path, model in CAPTURE_SOURCES:
        path = repo_root / relative_path
        try:
            loaded[relative_path] = model.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as error:
            raise ProofBundleError(
                f"the committed capture at {relative_path} no longer loads, and criteria "
                "this bundle prints as covered rest on it, so the bundle would state a "
                "status the acceptance gate does not reach. Re-capture with "
                f"`uv run python {CAPTURE_TOOL}`, or delete the record and remove the "
                "citations resting on it in src/edullm_platform/phase2_criteria.py: "
                f"{error}"
            ) from error
    environments = loaded[ENVIRONMENTS_PATH]
    secrets = loaded[SECRETS_PATH]
    lead_team = loaded[LEAD_TEAM_PATH]
    executions = loaded[EXECUTIONS_PATH]
    lineage = loaded[LINEAGE_PATH]
    assert isinstance(environments, EnvironmentInventory)
    assert isinstance(secrets, SecretInventory)
    assert isinstance(lead_team, LeadTeamMembership)
    assert isinstance(executions, AdmissionExecutionInventory)
    assert isinstance(lineage, LineageInventory)
    return CommittedEvidence(
        environments=environments,
        secrets=secrets,
        lead_team=lead_team,
        executions=executions,
        lineage=lineage,
        intents=tuple(
            (run_id, IntentRecord.model_validate(payload))
            for run_id, payload in read_lineage_payloads(repo_root, "intent")
        ),
        decisions=tuple(
            (run_id, DecisionRecord.model_validate(payload))
            for run_id, payload in read_lineage_payloads(repo_root, "decision")
        ),
    )


def hex_checksum(checksum_sha256: str) -> str:
    """S3's base64 ChecksumSHA256, rewritten as the hex form everything else here uses.

    A presentation change rather than a redaction, and worth a sentence because both
    obvious readings of it are wrong: nothing is masked, and the scanner was not widened.
    Base64 of thirty-two bytes is forty-four characters of ``[A-Za-z0-9/+=]``, which is
    exactly the shape ``AWS_SECRET_ACCESS_KEY_PATTERN`` refuses, so a bundle printing the
    literal value would be withheld as though it carried a credential. The hex form is the
    same thirty-two bytes, is reversible with one line of base64, and is the spelling every
    other digest in this repository already uses.
    """
    try:
        decoded = base64.b64decode(checksum_sha256, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ProofBundleError(
            f"a captured ChecksumSHA256 is not base64 and cannot be re-encoded: {error}"
        ) from error
    if len(decoded) != 32:
        raise ProofBundleError("a captured ChecksumSHA256 is not thirty-two bytes")
    return f"sha256:{decoded.hex()}"


# --------------------------------------------------------------------------------------
# The documents
# --------------------------------------------------------------------------------------


def render_empty_section(section: EmptySection, checks: Sequence[CriterionSpec]) -> str:
    """One live-evidence document, empty, saying what would fill it.

    The criteria it names are read back against the definition, so a section cannot claim
    to serve a criterion this phase does not have, and the status word beside each is taken
    from the recorded status rather than typed -- which is what stops a sentence here
    disagreeing with the gate.
    """
    rows = [[number, STATUS_PROSE[recorded_status(checks, number)]] for number in section.closes]
    return (
        "\n".join(
            [
                f"# {section.title}",
                "",
                NOTHING_CAPTURED,
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
                    "Each of those is recorded in `src/edullm_platform/phase2_criteria.py` with "
                    "the same account of what is missing, and "
                    "`uv run python tools/validate_phase2.py` reports it. This document and "
                    "that definition are two views of one fact rather than two claims."
                ),
            ]
        )
        + "\n"
    )


def render_denial_matrix(checks: Sequence[CriterionSpec]) -> str:
    """The admission denial matrix, what each probe is aimed at, and what it has cost."""
    sections = [
        "# Phase 2 admission denial matrix",
        "",
        (
            "The admission role may do exactly one thing: start one Step Functions state "
            f"machine and read that execution back. The {spell(len(ADMISSION_DENIED_ACTIONS))} "
            "actions below are the ways that grant could have been wider than it reads, and "
            "each is attempted under a real admission session after the approval gate and "
            "immediately before `StartExecution`."
        ),
        "",
        (
            "That distinction is the whole reason this matrix exists. Every other test of "
            "this role reads a committed CloudFormation template, which is what the account "
            "was asked for rather than what it holds -- and a role widened in the console "
            "leaves every one of them green."
        ),
        "",
        "## What is attempted, and why a permitted call would still change nothing",
        "",
        table(
            ["action", "why a permitted call changes nothing"],
            [[f"`{action}`", PROBE_INERTNESS[action]] for action in ADMISSION_DENIED_ACTIONS],
        ),
        "",
        (
            "The list is read from `edullm_platform.admission_denials` rather than written "
            "here, so adding a probe or renaming an action changes this document rather than "
            "leaving it behind."
        ),
        "",
        (
            "**One of them is not inert and says so.** S3 has no dry run and the bucket has "
            "to be the real one, so a permitted `s3:PutObject` writes a zero-byte object once, "
            f"under `{LINEAGE_PROBE_KEY}` and never under `intent/`, `decision/` or "
            "`conflicts/`. What is bounded is that it cannot forge or overwrite a lineage "
            "record and cannot write a second time; what is not bounded is that the first "
            "object exists."
        ),
        "",
        "## What choosing a probe has cost",
        "",
        (
            "Read this before adding one. Each entry is a rule some probe broke, with what "
            "taught it, because a rule with no incident attached reads as caution and gets "
            "skipped. Phase 1's list still applies; these are what the admission matrix added."
        ),
        "",
    ]
    for lesson in ADMISSION_PROBE_LESSONS:
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
            "## Why this document is not evidence",
            "",
            (
                "**It ran, and nothing captured it.** The live matrix executed on 2026-07-27 "
                f"and refused all {spell(len(ADMISSION_DENIED_ACTIONS))} entries. The "
                "submission workflow already uploads the result as an `admission-denials` "
                "artifact, so what is missing is a download rather than another run: the "
                "artifact sanitized, committed under `fixtures/evidence/phase-2/`, and a test "
                "that reads it -- with the CloudTrail event id of each refusal, so a reviewer "
                "can look any of them up in the account."
            ),
            "",
            (
                "**The EC2 entry claims less than it looks like.** `ec2:RunInstances` could "
                "not be made conclusive, because EC2 validates the image format, then looks "
                "the image up, and only then authorizes, so no absent image ever reaches the "
                "question. `ec2:CreateKeyPair` has no resource preconditions and answers from "
                "authorization alone, which establishes that this session is refused EC2 "
                "mutation rather than that `RunInstances` specifically is refused. The compute "
                "path this platform uses is Batch, and `batch:SubmitJob` is denied beside it."
            ),
            "",
            (
                "Criterion 14 rests on this document and is "
                f"{STATUS_PROSE[recorded_status(checks, '14')]} for exactly that reason."
            ),
        ]
    )
    return "\n".join(sections).rstrip() + "\n"


#: Why being permitted would have changed nothing, one line per probe. Written here rather
#: than on the probe because it is prose for a reviewer, and the probe definition is read
#: by the job that makes the call.
PROBE_INERTNESS: Final = {
    "batch:SubmitJob": "the queue and the job definition named do not exist",
    "ec2:CreateKeyPair": "the call is a dry run, so nothing is created either way",
    "s3:PutObject": (
        "not inert, and bounded instead -- see below. It is aimed at the real bucket, "
        "because an invented one is answered `NoSuchBucket` before anybody is authorized"
    ),
    "states:StartExecution": "the state machine named sits beside the real one and does not exist",
    "states:StopExecution": (
        "the execution named is under the real admission machine and was never started"
    ),
    "iam:CreateRole": "the role name is the caller's own, which IAM already holds",
}


def render_approval_gate(evidence: CommittedEvidence, checks: Sequence[CriterionSpec]) -> str:
    """The gate as GitHub is configured, and the three things about it nobody captured."""
    environments = evidence.environments
    secrets = evidence.secrets
    lead_team = evidence.lead_team
    environment_rows = [
        [
            environment.name,
            ", ".join(f"{reviewer.kind}:{reviewer.name}" for reviewer in environment.reviewers),
            "custom" if environment.custom_branch_policies else "**protected-branches**",
            ", ".join(environment.branch_policy_names) or "—",
            "yes" if environment.can_admins_bypass else "no",
            "yes" if environment.prevent_self_review else "no",
        ]
        for environment in environments.environments
    ]
    secret_rows = [
        ["repository secrets", count_or_none(secrets.repository_secret_names)],
        ["organization secrets", count_or_none(secrets.organization_secret_names)],
        ["dependabot secrets", count_or_none(secrets.dependabot_secret_names)],
        *(
            [f"environment secrets on `{name}`", count_or_none(names)]
            for name, names in sorted(secrets.environment_secret_names.items())
        ),
        ["repository variables", ", ".join(f"`{name}`" for name in secrets.repository_variable_names)],
    ]
    return (
        "\n".join(
            [
                "# Phase 2 approval gate evidence",
                "",
                (
                    "The gate is GitHub configuration rather than code, and nothing in this "
                    "repository could read it until this capture existed. A setting here "
                    "changes in a browser in ten seconds and leaves no artifact in any "
                    "repository, which is why a statement about one expires rather than "
                    f"standing: these records stop loading on {evidence.expires_on}."
                ),
                "",
                (
                    f"Captured by `{CAPTURE_TOOL}` from "
                    f"`{environments.organization}/{environments.repository}` and read from "
                    f"`{ENVIRONMENTS_PATH}`, `{SECRETS_PATH}` and `{LEAD_TEAM_PATH}`."
                ),
                "",
                "## All three approval environments, as configured",
                "",
                table(
                    [
                        "environment",
                        "reviewers",
                        "branch policy form",
                        "branches",
                        "admins may bypass",
                        "prevents self-review",
                    ],
                    environment_rows,
                ),
                "",
                (
                    "Every environment the capture found is listed, not only the three this "
                    "phase expects. An environment is auto-created, with no protection rules "
                    "at all, by anybody who can name one in a workflow file -- which is "
                    "everybody who can submit -- so a capture reading only the three expected "
                    "names would report a healthy gate with a fourth, unprotected environment "
                    "beside it."
                ),
                "",
                (
                    "`run-approval-automatic` has no reviewers and that is what it is for: a "
                    "run the policy priced under five dollars and under an hour is released "
                    "by nobody. It carries every other protection the reviewed two carry -- "
                    "pinned to `main` by name, no admin bypass, no wait timer -- so what the "
                    "class removes is the person and not the gate. Its `prevents self-review` "
                    "reads no for a different reason than theirs do: GitHub refuses that flag "
                    "on an environment with no reviewers, answering 422, so the capture "
                    "derives it from an absent rule rather than from a setting. There is "
                    "nobody to prevent."
                ),
                "",
                (
                    "**The lead gate's single reviewer is a team, so its effective reviewer "
                    "list is a second record.** The environment capture can say that the slot "
                    "holds a team and cannot say who stands behind it, because that is "
                    "organization state no file in this repository follows. "
                    f"`{LEAD_TEAM_PATH}` is that record: "
                    f"{spell(len(lead_team.member_logins))} logins in "
                    f"`{lead_team.team_slug}`, compared against `team_leads` in "
                    "`config/organization.yaml` in both directions rather than flattened into "
                    "the reviewer list above, which would agree with the roster for the wrong "
                    "reason. It was taken on a later day than the environment capture, and the "
                    "expiry quoted above is the earlier of the two: a criterion resting on both "
                    "is only as current as the older one."
                ),
                "",
                (
                    "**The branch policy form is asserted specifically and the two forms are "
                    "not equivalent.** `protected_branches` follows whatever branch protection "
                    "happens to cover, so it widens the moment a second branch is protected -- "
                    "a change nobody would connect to this control. `custom_branch_policies` "
                    "matches names that were written down."
                ),
                "",
                (
                    "**Self-review is permitted deliberately, and it is not what enforces "
                    "anything.** A lead self-authorizing a routine run and an admin approving "
                    "their own exception are both intended. What stops a member approving "
                    "their own submission is that members are not reviewers on either "
                    "environment, and independently that `evaluate_authorization` returns "
                    "`self_approval_not_permitted_for_member`."
                ),
                "",
                "## Secrets and variables, by name and never by value",
                "",
                table(["scope", "names"], secret_rows),
                "",
                (
                    "Names only, and the model has no field a value could occupy, which is a "
                    "stronger guarantee than a capture tool that is careful. It matters here "
                    "more than anywhere: the evidence for no-credentials-are-stored must not "
                    "itself store one."
                ),
                "",
                (
                    "Phase 2 introduced no credential at all, and that was a live question "
                    "rather than a foregone conclusion. The fallback, had the approvals "
                    "endpoint needed a fine-grained token, was to store one as an environment "
                    "secret. The endpoint answered a `GITHUB_TOKEN` holding actions read, so "
                    "nothing was stored and both environment secret lists are empty."
                ),
                "",
                "## What this capture does not carry",
                "",
                (
                    "Four artifacts the phase plan asks this document for do not exist, and "
                    "each is about a run rather than about configuration, which is why the "
                    "configuration capture cannot reach them."
                ),
                "",
                bullets(
                    [
                        (
                            "**The workflow run URLs.** The runs are in GitHub's Actions "
                            "history and no committed record names one, so a reviewer cannot "
                            "get from this bundle to the run it describes."
                        ),
                        (
                            "**The pending-deployment state.** A submission left unapproved on "
                            "2026-07-27 sat in status `waiting` with its submit job reporting "
                            "no runner at all, and the state machine execution count did not "
                            "move while it sat there. Nothing reads that."
                        ),
                        (
                            "**The approvals API response naming the approver.** The approver "
                            "reaches AWS because the submitting job read it from that endpoint "
                            "and passed it along; the response itself was never committed."
                        ),
                        (
                            "**The `$GITHUB_STEP_SUMMARY` the approver saw.** The compile job "
                            "now uploads the same markdown as an artifact, copied from the file "
                            "the summary is written from rather than re-rendered. No run that "
                            "actually waited at a gate has had that artifact captured."
                        ),
                    ]
                ),
                "",
                table(
                    ["criterion", "status today", "what it is short of"],
                    [
                        ["2", STATUS_PROSE[recorded_status(checks, "2")], "the pending-deployment state"],
                        ["3", STATUS_PROSE[recorded_status(checks, "3")], "a second lead releasing one run"],
                        ["9", STATUS_PROSE[recorded_status(checks, "9")], "the approvals API response"],
                        ["11", STATUS_PROSE[recorded_status(checks, "11")], "the rendered approver context"],
                    ],
                ),
                "",
                (
                    "Criterion 3 is the one nobody here can close alone. It needs a lead other "
                    "than the submitter to release a routine submission, and every run so far "
                    "was released by the submitter, who is also a lead."
                ),
            ]
        )
        + "\n"
    )


def count_or_none(names: Sequence[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) if names else "none"


def render_execution_evidence(
    evidence: CommittedEvidence,
    checks: Sequence[CriterionSpec],
) -> str:
    """Every admission execution the account has run, and how each one ended."""
    executions = evidence.executions
    rows = [
        [f"`{execution.name}`", execution.status, f"`{execution.error}`" if execution.error else "—"]
        for execution in executions.executions
    ]
    rejected = [
        execution for execution in executions.executions if execution.error == "AdmissionRejected"
    ]
    runtime = [
        execution
        for execution in executions.executions
        if execution.status == "FAILED" and execution.error != "AdmissionRejected"
    ]
    return (
        "\n".join(
            [
                "# Phase 2 admission execution evidence",
                "",
                (
                    f"Every execution `{executions.state_machine_name}` has run, read from "
                    f"`{EXECUTIONS_PATH}`. Sourced from the execution list rather than from "
                    "CloudWatch, because execution history is guaranteed and log delivery is "
                    "best-effort."
                ),
                "",
                table(["execution name", "status", "error"], rows),
                "",
                (
                    "**The name is the run id, and that is what makes the duplicate-name "
                    "refusal mean something.** Step Functions answers a second "
                    "`StartExecution` under a name that has already closed with "
                    "`ExecutionAlreadyExists` for ninety days, so the name is a deduplication "
                    "key rather than a label."
                ),
                "",
                "## Reading the failures",
                "",
                (
                    f"`AdmissionRejected` is the validator refusing a submission, and "
                    f"{spell(len(rejected))} executions carry it. Anything else is the machine "
                    "itself failing, and the two mean very different things about whether "
                    f"admission worked: {spell(len(runtime))} execution failed with "
                    "`States.Runtime`, once, before the handler and the state machine "
                    "definition agreed on a payload shape."
                ),
                "",
                (
                    "A refusal that left no record would make a rejected submission "
                    "indistinguishable from one nobody made. Each `AdmissionRejected` execution "
                    "has an intent record and a decision record under its name, and the "
                    "decision reads `manifest_hash_mismatch` with `accepted: false`. That join "
                    "is shown in `lineage-record-evidence.md` rather than asserted here."
                ),
                "",
                "## What this document does not carry",
                "",
                bullets(
                    [
                        (
                            "**The duplicate-name refusal itself.** Step Functions refused a "
                            "second `StartExecution` under an existing name with `400 "
                            "ExecutionAlreadyExists` on 2026-07-27, and the response was never "
                            "captured. What is committed is the store it left behind, in which "
                            "no run id appears twice -- which is the consequence rather than "
                            "the refusal."
                        ),
                        (
                            "**The execution ARNs and their histories.** The capture records "
                            "names and terminal states. An ARN carries the account id, and "
                            "`GetExecutionHistory` carries the submitted payload, so both need "
                            "a projection designed for them rather than a scan afterwards."
                        ),
                    ]
                ),
                "",
                table(
                    ["criterion", "status today", "what it is short of"],
                    [
                        [
                            "12",
                            STATUS_PROSE[recorded_status(checks, "12")],
                            "the `ExecutionAlreadyExists` response and the S3 412 beside it",
                        ],
                        [
                            "13",
                            STATUS_PROSE[recorded_status(checks, "13")],
                            "nothing -- the refused runs left committed decision records",
                        ],
                    ],
                ),
            ]
        )
        + "\n"
    )


def render_lineage_evidence(
    evidence: CommittedEvidence,
    checks: Sequence[CriterionSpec],
) -> str:
    """What the lineage store holds, as S3 attests it rather than as the platform wrote it."""
    lineage = evidence.lineage
    object_rows = [
        [
            f"`{stored.key}`",
            f"`{stored.version_id}`",
            f"`{hex_checksum(stored.checksum_sha256)}`",
            str(stored.content_length),
            "yes" if stored.canonical else "no",
        ]
        for stored in lineage.objects
    ]
    join_rows = [
        [
            f"`{run_id}`",
            "yes" if decision.accepted else "no",
            decision.approval_class.value,
            decision.approving_environment.value if decision.approving_environment else "—",
            decision.reason.value,
            f"`{decision.policy_version}`",
        ]
        for run_id, decision in evidence.decisions
    ]
    older = [stored for stored in lineage.objects if not stored.canonical]
    return (
        "\n".join(
            [
                "# Phase 2 lineage record evidence",
                "",
                (
                    f"Every object in `{lineage.bucket}`, read from `{LINEAGE_PATH}`, with the "
                    f"records themselves committed under `{RECORDS_DIR}/`. These expire on "
                    f"{evidence.expires_on}, and this generator refuses to build once they do."
                ),
                "",
                "## What S3 attests about each object",
                "",
                table(
                    ["key", "VersionId", "ChecksumSHA256", "bytes", "canonical"],
                    object_rows,
                ),
                "",
                (
                    "**The checksum is written in hex and S3 reported it in base64.** That is "
                    "a presentation change rather than a redaction: the same thirty-two bytes, "
                    "reversible with one line of base64, and the spelling every other digest in "
                    "this repository uses. Base64 of thirty-two bytes is forty-four characters "
                    "of `[A-Za-z0-9/+=]`, which is precisely the shape the evidence secret scan "
                    "refuses, so printing the literal value would have the whole document "
                    "withheld as though it carried a credential."
                ),
                "",
                (
                    "**Attested rather than computed here.** Both fields come back from "
                    "`HeadObject` with `--checksum-mode ENABLED`, so an object missing either "
                    "would mean a write took a path the template does not describe."
                ),
                "",
                (
                    "**`ChecksumSHA256` is not the manifest hash and the two must never be "
                    "conflated.** The checksum attests that the object's bytes arrived intact; "
                    "`manifest_sha256` attests the manifest's canonical serialization and is the "
                    "value an approval was taken against. They answer different questions, and "
                    "a record that mixed them would be a lineage error rather than a wording "
                    "slip."
                ),
                "",
                (
                    "**The store holds two shapes and both are captured.** The "
                    f"{spell(len(older))} objects marked `no` above were written before the "
                    "encoding fix and are a JSON string containing the record, because the S3 "
                    "SDK integration encodes whatever the Body path yields and the handler was "
                    "returning canonical strings. The rest are the canonical bytes. "
                    "Recording the older shape rather than dropping it is deliberate: a capture "
                    "that made the store look uniform would leave the first person to read one "
                    "of those objects meeting a surprise nobody wrote down."
                ),
                "",
                "## The decision beside every intent, joined by run id",
                "",
                table(
                    ["run id", "accepted", "class", "gate it came through", "reason", "policy"],
                    join_rows,
                ),
                "",
                (
                    "The last row is the one worth reading twice. It is a refusal, and it still "
                    "has both records: a submission whose manifest did not hash to what was "
                    "approved earned a decision naming the reason, against the run id that was "
                    "attempted. A refusal that left no record would make a rejected submission "
                    "indistinguishable from one nobody made."
                ),
                "",
                (
                    "Each run id owns exactly one intent record and one decision record, and "
                    "each intent's manifest still hashes to the value recorded beside it -- "
                    "recomputed from the stored bytes, so a manifest edited after the fact "
                    "would fail rather than read as intact. That property is what the whole "
                    "approval gate rests on."
                ),
                "",
                (
                    "Reading these records back is what found the defect that made them "
                    "readable. `maximum_compute_cost_usd` is a computed field, so pydantic "
                    "wrote it out and refused it on the way back in, and every decision record "
                    "in the store failed to load. A record the writing model cannot read back "
                    "is an audit trail nobody can audit."
                ),
                "",
                "## What this document does not carry",
                "",
                bullets(
                    [
                        (
                            "**The `412 PreconditionFailed` refusal of a second conditional "
                            "write.** `tools/probe_conditional_write.py` established that a "
                            "second `PutObject` carrying `IfNoneMatch: *` fails, and that Step "
                            "Functions surfaces it as `S3.S3Exception`. The response was never "
                            "committed."
                        ),
                        (
                            "**What `S3.S3Exception` does and does not distinguish.** It is the "
                            "generic name for every unmodelled S3 error, so it does not tell a "
                            "genuine already-exists from a transient fault. The 412 and its "
                            "precondition message appear only in the `Cause`, which no "
                            "`ErrorEquals` can match, so `RecordConflict` means the write was "
                            "refused rather than that the key existed."
                        ),
                    ]
                ),
                "",
                table(
                    ["criterion", "status today"],
                    [
                        [number, STATUS_PROSE[recorded_status(checks, number)]]
                        for number in ("12", "17", "18", "21")
                    ],
                ),
            ]
        )
        + "\n"
    )


# --------------------------------------------------------------------------------------
# The authorization matrix, computed rather than typed
# --------------------------------------------------------------------------------------

UNKNOWN_LOGIN: Final = "not-a-member"

#: The hourly rate every row below is evaluated at, and gpu-1xa10g's rate is the value.
#:
#: Classification now also asks whether the profile a request names is expensive enough to
#: need an admin, and a scenario states RequestFacts without naming a profile, so the rate
#: has to come from here. Every row in this matrix varies who submits and who approves; none
#: of them is about price, and a rate above the ceiling would turn each routine row into an
#: exception and change what this document reports about the roster.
SCENARIO_HOURLY_RATE_USD: Final = Decimal("1.006")


@dataclass(frozen=True)
class MatrixRow:
    label: str
    submitter: str
    approver: str | None
    decision: AuthorizationDecision


def read_scenarios(repo_root: Path) -> tuple[AuthorizationScenario, ...]:
    directory = repo_root / SCENARIO_DIR
    scenarios = tuple(
        load_yaml(path, AuthorizationScenario) for path in sorted(directory.glob("*.yaml"))
    )
    if not scenarios:
        raise ProofBundleError(f"no authorization scenario is committed under {SCENARIO_DIR}")
    return scenarios


def scenario_named(scenarios: Sequence[AuthorizationScenario], name: str) -> AuthorizationScenario:
    for scenario in scenarios:
        if scenario.scenario == name:
            return scenario
    raise ProofBundleError(f"{SCENARIO_DIR} does not carry a scenario named {name}")


def a_plain_member(inventory: OrganizationInventory, *, besides: Sequence[str] = ()) -> str:
    """Somebody on the roster who is neither an admin nor a lead.

    Read off the roster rather than typed, so this matrix keeps describing the roster
    after somebody is promoted rather than describing whoever was plain when it was
    written.
    """
    excluded = {login.casefold() for login in besides}
    for member in inventory.members:
        login = member.github_login
        if inventory.is_admin(login) or inventory.is_team_lead(login):
            continue
        if login.casefold() in excluded:
            continue
        return login
    raise ProofBundleError(
        "the roster has no member who is neither an admin nor a lead, so the negative half "
        "of this matrix cannot be built from it"
    )


def a_lead_who_is_not_an_admin(inventory: OrganizationInventory) -> str:
    for login in inventory.team_leads:
        if not inventory.is_admin(login):
            return login
    raise ProofBundleError("the roster has no team lead who is not also an admin")


def an_admin(inventory: OrganizationInventory) -> str:
    return inventory.admins[0]


def facts_with(request: RequestFacts, **overrides: object) -> RequestFacts:
    payload = request.model_dump(mode="json")
    payload.update(overrides)
    return RequestFacts.model_validate(payload)


def matrix_rows(
    repo_root: Path,
    policy: ApprovalPolicy,
    inventory: OrganizationInventory,
) -> tuple[tuple[MatrixRow, ...], tuple[MatrixRow, ...]]:
    """The committed scenarios, then the refusals derived by varying one actor.

    Every row is evaluated by :func:`evaluate_authorization` against the shipped policy
    and the shipped roster, so this document reports what the code does rather than what
    somebody remembers it doing. The negative half varies exactly one thing per row --
    who approves, or whether the submitter is on the roster -- because a row that changed
    two would not say which one earned the refusal.
    """
    scenarios = read_scenarios(repo_root)
    committed = tuple(
        MatrixRow(
            label=scenario.scenario,
            submitter=scenario.submitter.github_login,
            approver=None if scenario.approver is None else scenario.approver.github_login,
            decision=scenario.decide(policy, inventory, hourly_rate_usd=SCENARIO_HOURLY_RATE_USD),
        )
        for scenario in scenarios
    )
    routine = scenario_named(scenarios, "member-approval")
    exception = scenario_named(scenarios, "admin-exception")
    lead_run = scenario_named(scenarios, "lead-self-authorization")
    member = routine.submitter.github_login
    other_member = a_plain_member(inventory, besides=(member,))
    lead = a_lead_who_is_not_an_admin(inventory)

    def row(label: str, submitter: str, approver: str | None, request: RequestFacts) -> MatrixRow:
        return MatrixRow(
            label=label,
            submitter=submitter,
            approver=approver,
            decision=evaluate_authorization(
                submitter,
                approver,
                request,
                policy,
                inventory,
                hourly_rate_usd=SCENARIO_HOURLY_RATE_USD,
            ),
        )

    derived = (
        row("member submits, nobody approves", member, None, routine.request),
        row("member submits, another member approves", member, other_member, routine.request),
        row("member submits, approver is off the roster", member, UNKNOWN_LOGIN, routine.request),
        row("submitter is off the roster", UNKNOWN_LOGIN, lead, routine.request),
        row("exception, approved by a lead who is not an admin", member, lead, exception.request),
        row(
            "lead self-authorizes, attributing the run to another team",
            lead_run.submitter.github_login,
            None,
            facts_with(lead_run.request, claimed_team=exception.request.claimed_team),
        ),
    )
    return committed, derived


def render_authorization_matrix(
    repo_root: Path,
    checks: Sequence[CriterionSpec],
) -> str:
    """Who may release what, evaluated against the shipped policy and the shipped roster."""
    policy = load_yaml(repo_root / "config" / "policy.yaml", ApprovalPolicy)
    inventory = load_yaml(repo_root / "config" / "organization.yaml", OrganizationInventory)
    committed, derived = matrix_rows(repo_root, policy, inventory)
    scenarios = {scenario.scenario: scenario for scenario in read_scenarios(repo_root)}
    committed_rows = [
        [
            entry.label,
            role_of(inventory, entry.submitter),
            "—" if entry.approver is None else role_of(inventory, entry.approver),
            entry.decision.approval_class.value,
            "granted" if entry.decision.granted else "refused",
            f"`{entry.decision.reason.value}`",
            "yes" if scenarios[entry.label].expected.matches(entry.decision) else "**no**",
        ]
        for entry in committed
    ]
    derived_rows = [
        [
            entry.label,
            role_of(inventory, entry.submitter),
            "—" if entry.approver is None else role_of(inventory, entry.approver),
            entry.decision.approval_class.value,
            "granted" if entry.decision.granted else "refused",
            f"`{entry.decision.reason.value}`",
            "yes" if entry.decision.team_verified else "no",
        ]
        for entry in derived
    ]
    return (
        "\n".join(
            [
                "# Phase 2 authorization matrix",
                "",
                (
                    "Every row below was evaluated by `evaluate_authorization` while this "
                    "bundle was generated, against `config/policy.yaml` and "
                    "`config/organization.yaml` as they are committed. Nothing here is a "
                    "recollection of what the function returns."
                ),
                "",
                (
                    f"The approval scope in force is `{policy.approval_scope.value}`, which is "
                    "what makes criterion 3's statement -- that any team lead may release a "
                    "routine run -- the thing to check rather than assume."
                ),
                "",
                "## The committed scenarios",
                "",
                (
                    f"The {spell(len(committed))} scenarios under `{SCENARIO_DIR}/`, each "
                    "carrying the outcome it expects. The last column compares that expectation "
                    "to what the function returned just now, so a scenario whose recorded "
                    "expectation has drifted away from the code shows up here rather than in a "
                    "reader's assumptions."
                ),
                "",
                table(
                    [
                        "scenario",
                        "submitter",
                        "approver",
                        "class",
                        "outcome",
                        "reason",
                        "matches its recorded expectation",
                    ],
                    committed_rows,
                ),
                "",
                "## The refusals, derived by varying one actor",
                "",
                (
                    "Built from the committed scenarios' own request facts, changing exactly "
                    "one thing per row. The logins are read off the roster by role rather than "
                    "written here, so this table keeps describing the roster after somebody is "
                    "promoted instead of describing whoever held a role when it was written."
                ),
                "",
                table(
                    [
                        "case",
                        "submitter",
                        "approver",
                        "class",
                        "outcome",
                        "reason",
                        "team verified",
                    ],
                    derived_rows,
                ),
                "",
                "## The last row, and why it is a deferral rather than a failure",
                "",
                (
                    "A lead self-authorizing a run attributed to a team that is not theirs is "
                    "granted, and criterion 4 is "
                    f"{STATUS_PROSE[recorded_status(checks, '4')]} for that reason rather than "
                    "failing. `team_bindings.teams` in `config/organization.yaml` is empty, so "
                    "membership is unverifiable and enforcing this literally would reject every "
                    "submission, including the ones that should succeed."
                ),
                "",
                (
                    "What keeps that visible rather than silent is the `team verified` column: "
                    "it is `no` on every row, and every decision record in the lineage store "
                    "carries the same false, so an unverified attribution is written into the "
                    "audit trail rather than passed over. The deferral becomes live again with "
                    "no code change, the moment `team_bindings.teams` is populated."
                ),
                "",
                "## What this matrix does not establish",
                "",
                bullets(
                    [
                        (
                            "That GitHub agrees. This is the platform's own authorization "
                            "function, and it holds regardless of how the environments are "
                            "configured. The second mechanism -- that members are not reviewers "
                            "on either gate -- is GitHub configuration and lives in "
                            "`approval-gate-evidence.md`."
                        ),
                        (
                            "That the approver in a decision record is who GitHub says it is. "
                            "The OIDC token proves an approval happened and which gate it "
                            "passed; it carries no claim naming the approver. The identity "
                            "reaches AWS because the submitting job read it from the approvals "
                            "API and passed it along, so a compromised runner could still "
                            "misreport who released a run."
                        ),
                        (
                            "That any of these cases happened. Rows are evaluations of the "
                            "shipped code, not observations of the account; what the account "
                            "did is in `lineage-record-evidence.md`."
                        ),
                    ]
                ),
            ]
        )
        + "\n"
    )


def role_of(inventory: OrganizationInventory, login: str) -> str:
    """How a login should be read in the matrix: by role, with the name beside it."""
    if not any(
        member.normalized_github_login == login.casefold() for member in inventory.members
    ):
        return f"`{login}` (not on the roster)"
    roles = []
    if inventory.is_admin(login):
        roles.append("admin")
    if inventory.is_team_lead(login):
        roles.append("lead")
    return f"`{login}` ({', '.join(roles) if roles else 'member'})"


# --------------------------------------------------------------------------------------
# The rest of the bundle
# --------------------------------------------------------------------------------------


def render_open_decisions(decisions: Sequence[OpenDecision]) -> str:
    """Questions this repository has surfaced and deliberately has not answered."""
    sections = [
        "# Phase 2 open decisions",
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
            "Phase 1's question -- whether a registry scan result may block a publish -- was "
            "carried into this register and answered during Phase 3. It is gone from here "
            "rather than edited to agree with what was built, which is what the register's own "
            "rule requires; the answer lives in `contracts/image_scan.py`, in "
            "`config/policy.yaml` and in `config/image-exceptions.yaml`."
        ),
        "",
        (
            "None of what is left has a recommendation, and none may have one. The source is "
            "`src/edullm_platform/open_decisions.py`, which refuses an entry with fewer than "
            "two options, so an entry cannot become a decision by having its alternatives "
            "deleted."
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
    sections.extend(
        [
            "## What Phase 2 left unrecorded here, and why",
            "",
            (
                "The nine decisions the Phase 2 plan opened with, D1 to D9, were all taken "
                "before the phase shipped: they are settled in `config/organization.yaml`, in "
                "the two environments' configuration, in `infra/lineage-bucket.yaml` and in "
                "the submission workflow. A decision that has been taken belongs where it is "
                "enforced rather than in a register of open ones, so none of them is repeated "
                "here."
            ),
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
                "# Phase 2 golden canonical digests",
                "",
                (
                    f"The canonical JSON digest of each of the {spell(len(goldens))} roles "
                    "Phase 2 adds, taken over the projection the drift comparison acts on "
                    "rather than over the file."
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
                    "This tripwire is doing more work in Phase 2 than in Phase 1, and it is "
                    "worth understanding why. All three roles are deployed -- they were created "
                    "from a laptop on 2026-07-27 -- and none of them has been captured, so the "
                    "comparison that would catch one widened in the console has nothing to run "
                    "on. Until a capture lands, the recorded digest catches a template that "
                    "changed and says nothing at all about the account."
                ),
                "",
                table(["role", "template", "canonical bytes", "digest"], rows),
                "",
                "## How this fails",
                "",
                (
                    f"`{GOLDENS_FILENAME}` in this directory is the machine-readable copy. "
                    "`tests/test_phase2_proof.py` reprojects each template, recomputes its "
                    "digest and compares it to the recorded value."
                ),
                "",
                (
                    f"`{GENERATOR_COMMAND}` refuses to overwrite a drifted digest. Re-recording "
                    "requires `--regenerate-goldens`, so a change to what a role may do cannot "
                    "be absorbed by re-running the generator."
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


def phase2_models(repo_root: Path) -> tuple[ModelRecord, ...]:
    return tuple(
        record for record in model_records(repo_root) if record.module in PHASE2_CONTRACT_MODULES
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
                "# Phase 2 schema compatibility report",
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
                table(
                    ["model", "module", "kind", "schema_version", "exported", "structural digest"],
                    rows,
                ),
                "",
                (
                    "`IntentRecord` and `DecisionRecord` are the two a reviewer should read "
                    "closely: they are the audit trail, they are written once and never "
                    "rewritten, and a field retyped after a record is in the store is a field "
                    "the store's older objects no longer satisfy. That is not hypothetical "
                    "here -- `CostInputs` had to be taught to accept a recorded total, because "
                    "`maximum_compute_cost_usd` is computed and pydantic refused every decision "
                    "record in the store on the way back in."
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
        "# Phase 2 negative-case matrix",
        "",
        (
            f"The {spell(len(criteria))} Phase 2 acceptance criteria, mapped to the tests "
            "cited for each one by node id. Each cited node id was collected and executed by "
            "this generator before the bundle was written; a citation pytest cannot collect "
            "aborts generation rather than being printed."
        ),
        "",
        (
            "This mapping is defined once, in `src/edullm_platform/phase2_criteria.py`. The "
            "acceptance gate reads the same definition and executes the same node ids, so this "
            "matrix and `tools/validate_phase2.py` cannot disagree."
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
                    "changing in the account, which is the one thing this matrix exists to "
                    "make impossible to do quietly."
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
    evidence: CommittedEvidence,
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
            "The path ran and the runs were not captured. This is the limitation the eight "
            "open checks are consequences of: on 2026-07-27 a lead released a routine run, an "
            "exception routed to the admin gate, a duplicate execution name was refused, a "
            "tampered hash was refused, and a six-probe denial matrix came back refused on "
            "every entry. What is committed is the state those runs left behind rather than "
            "the runs, and a criterion that can only be established by evidence nobody "
            "committed is open however convincing the run was to whoever watched it."
        ),
        (
            f"Check 7 -- that a job omitting the approval environment cannot assume the "
            f"admission role -- is {status_of('7')}, and it is the strongest thing this phase "
            "produced. The `deny-unapproved` job succeeded on every live run, meaning STS "
            "refused the ref-based subject, and none of it is in this repository."
        ),
        (
            f"Check 6 is {status_of('6')} and check 19 is {status_of('19')}, on the same "
            "missing artifact. Both rest on committed CloudFormation templates, which are "
            "what the repository asks the account for rather than what the account holds. "
            "The three Phase 2 roles were deployed from a laptop and no capture has been "
            "compared against any of them, so the comparison that catches a role widened in "
            "the console does not run for them."
        ),
        (
            f"Check 11 is {status_of('11')} and cannot be closed by capturing harder. It "
            "asks for the branch, and `RunManifest` has no branch field: every source "
            "revision is a full commit SHA, because a branch is mutable and a commit is not. "
            "Closing it means either carrying the branch as advisory metadata that nothing "
            "authorizes on, or amending the check with that reason written down."
        ),
        (
            f"Check 21 states what a decision record carries and is {status_of('21')} on that "
            "reading alone. It does not claim AWS verified the actor. The approver reaches "
            "AWS because the submitting job read it from the GitHub approvals API and passed "
            "it along; no OIDC claim names who approved, so a compromised runner could "
            "misreport it. The gate itself cannot be skipped."
        ),
        (
            "Every committed Phase 2 capture is a statement about one moment, and they were "
            "not all taken at the same one. The earliest of them stops loading on "
            f"{evidence.expires_on}, this generator refuses to build from that date, and "
            "every check resting on any of them is open again. Nothing about GitHub or the "
            "lineage store will have changed; what will have lapsed is anybody's knowledge of "
            "them. Re-capturing is a read of the account rather than another run."
        ),
        (
            f"The {spell(len(goldens))} recorded role digests describe committed templates and "
            "say nothing about the account. They catch a template widened between now and the "
            "next capture, which is the only thing standing in for a drift comparison that "
            "cannot run yet."
        ),
        (
            "The authorization matrix is an evaluation rather than an observation. Every row "
            "in it is `evaluate_authorization` run against the shipped policy and roster at "
            "generation time, which says what the platform decides and nothing about who "
            "GitHub let through."
        ),
        (
            "**There is no rollback result here, and the master plan asks every bundle for "
            "one.** The rollback is written down -- remove the reviewers from both "
            "environments, redeploy the admission role granting nothing, disable the "
            "submission workflow, leave the lineage bucket and the state machine alone -- and "
            "it has been described rather than rehearsed. Section 6 of the Phase 2 plan does "
            "not list a document for it and nothing in `src/edullm_platform/phase2_criteria.py` "
            "covers it, so this bundle would have passed over the omission silently. Recording "
            "it here is the alternative to that. What a rehearsal has to establish is that a "
            "submission dispatched after step 1 does not reach AWS, and that a record written "
            "before step 1 is still readable afterwards."
        ),
        (
            "The `S3.S3Exception` this phase reads as a duplicate-write refusal is the generic "
            "name for every unmodelled S3 error. It does not distinguish a genuine "
            "already-exists from a transient fault, because the 412 and its precondition "
            "message appear only in the `Cause`, which no `ErrorEquals` can match."
        ),
        (
            "The secret scan applied to this bundle masks its own content digests before "
            "scanning, and the S3 checksums here are rewritten from base64 into that hex form "
            "for the same reason. Both are presentation changes over bytes that are still "
            "fully recorded; no other exemption is applied."
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
    """Everything this bundle was generated from, including every committed record.

    The captures and the authorization scenarios are read off the tree rather than listed,
    so a record added to either directory is measured without a second edit here -- and so
    one deleted stops appearing rather than being reported at a digest nobody can
    reproduce.
    """
    captured = sorted(
        str(path.relative_to(repo_root)) for path in (repo_root / EVIDENCE_DIR).rglob("*.json")
    )
    scenarios = sorted(
        str(path.relative_to(repo_root)) for path in (repo_root / SCENARIO_DIR).glob("*.yaml")
    )
    paths = sorted({*PHASE2_INPUTS, *captured, *scenarios})
    return tuple((path, file_digest(repo_root / path)) for path in paths)


def open_checks_table(criteria: Sequence[CriterionSpec]) -> str:
    """The open checks, by number and statement, rendered from the recorded status."""
    return table(
        ["#", "check that is not satisfied"],
        [
            [check.number, check.statement]
            for check in criteria
            if check.status is CriterionStatus.GAP
        ],
    )


def render_index(
    *,
    generated_at: datetime,
    commit_sha: str,
    criteria: Sequence[CriterionSpec],
    verification: Verification,
    goldens: Sequence[RecordedGolden],
    models: Sequence[ModelRecord],
    evidence: CommittedEvidence,
    decisions: Sequence[OpenDecision],
    input_digests: Sequence[tuple[str, str]],
    limitations: Sequence[str],
) -> str:
    covered_numbers = [check.number for check in criteria if check.status is CriterionStatus.COVERED]
    deferred_numbers = [
        check.number for check in criteria if check.status is CriterionStatus.DEFERRED
    ]
    gap_numbers = [check.number for check in criteria if check.status is CriterionStatus.GAP]
    accepted = [record for _, record in evidence.decisions if record.accepted]
    return (
        "\n".join(
            [
                "# Phase 2 proof bundle",
                "",
                f"Phase: {PHASE}",
                f"Bundle schema version: {BUNDLE_SCHEMA_VERSION}",
                f"Source commit: {commit_sha}",
                f"Generated: {generated_at.astimezone(UTC).isoformat(timespec='seconds')}",
                "",
                (
                    "This bundle exists so that a reviewer can decide whether Phase 2 is done "
                    "without reading the test suite. Everything it claims was executed by "
                    f"`{GENERATOR_COMMAND}` at generation time. {standing(gap_numbers, deferred_numbers)}"
                ),
                "",
                "## Read this first",
                "",
                gate_verdict(gap_numbers, phase_number=2),
                "",
                status_summary_sentence(criteria),
                "",
                open_checks_table(criteria),
                "",
                (
                    "**Every one of those is a run that happened and was never captured, or a "
                    "role nobody compared to its template.** Phase 2's path went end to end on "
                    "2026-07-27. What is committed is the state those runs left behind -- the "
                    "lineage objects, the execution list, the GitHub configuration -- and that "
                    "is what the covered checks rest on. A statement that can only be "
                    "established by evidence nobody committed is open here however convincing "
                    "the run was to whoever watched it, because the gate executes tests and a "
                    "test that reads nothing proves nothing."
                ),
                "",
                "## Contents",
                "",
                bullets(
                    [
                        (
                            f"`negative-case-matrix.md` — each of the {spell(len(criteria))} "
                            "Phase 2 acceptance criteria mapped to the tests cited for it, by "
                            "node id, with every gap stated. Read this one first."
                        ),
                        (
                            "`lineage-record-evidence.md` — every object in the lineage store "
                            "with its VersionId and S3-attested checksum, and the decision "
                            "beside every intent, joined by run id."
                        ),
                        (
                            "`admission-execution-evidence.md` — every execution the admission "
                            "state machine has run, and how each one ended."
                        ),
                        (
                            "`approval-gate-evidence.md` — both approval environments as GitHub "
                            "is configured, the secret and variable names at every level, and "
                            "the three artifacts about a *run* that nobody captured."
                        ),
                        (
                            "`authorization-matrix.md` — who may release what, evaluated against "
                            "the shipped policy and roster while this bundle was generated."
                        ),
                        (
                            "`admission-denial-matrix.md` — the six actions the admission "
                            "session must not be able to take, how each probe is aimed so that "
                            "being permitted would change nothing, and what choosing a probe "
                            "has cost. The matrix has run and holds no committed refusal."
                        ),
                        (
                            "`open-decisions.md` — what this repository has surfaced and not "
                            "answered, and why none of D1 to D9 is among it."
                        ),
                        (
                            "`serialization-goldens.md` and `"
                            + GOLDENS_FILENAME
                            + "` — the recorded canonical digest of what each Phase 2 role "
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
                            "`oidc-session-evidence.md` and `deployed-role-drift.md` — empty. "
                            "Each says what it records, what would fill it, and which checks "
                            "are waiting on it."
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
                        ["admission executions captured", str(len(evidence.executions.executions))],
                        ["lineage objects captured", str(len(evidence.lineage.objects))],
                        ["submissions accepted, of those captured", str(len(accepted))],
                        ["denial matrices captured", "0"],
                        ["CloudTrail records captured", "0"],
                        ["captures expire", evidence.expires_on],
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
    """Record what each Phase 2 role template grants, before anything reads a capture.

    Written first and as a pair, on the terms Phase 1's are and for the same reason: both
    documents are derived from templates this repository commits, neither says anything
    about the account, and re-recording one without the other leaves two committed
    documents in one directory disagreeing about what a role grants.
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
    criteria = phase2_criteria()
    goldens = compute_goldens(repo_root)
    goldens_written = write_goldens(output_dir, goldens, criteria, regenerate=regenerate_goldens)

    resolved = verify_repository(repo_root) if verification is None else verification
    models = phase2_models(repo_root)
    evidence = read_captures(repo_root)
    decisions = open_decisions()
    documents = {
        "unit-test-report.md": render_unit_test_report(resolved),
        "negative-case-matrix.md": render_matrix(criteria, resolved),
        "approval-gate-evidence.md": render_approval_gate(evidence, criteria),
        "admission-execution-evidence.md": render_execution_evidence(evidence, criteria),
        "lineage-record-evidence.md": render_lineage_evidence(evidence, criteria),
        "authorization-matrix.md": render_authorization_matrix(repo_root, criteria),
        "admission-denial-matrix.md": render_denial_matrix(criteria),
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
        evidence=evidence,
        decisions=decisions,
        input_digests=input_digest_table(repo_root),
        limitations=known_limitations(criteria, evidence, goldens),
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
        criteria=phase2_criteria(),
        nested_env=NESTED_RUN_ENV,
        test_prefixes=PHASE2_TEST_PREFIXES,
    )


def verify_repository(repo_root: Path) -> Verification:
    return shared_verify_repository(
        repo_root,
        criteria=phase2_criteria(),
        nested_env=NESTED_RUN_ENV,
        test_prefixes=PHASE2_TEST_PREFIXES,
    )


def render_unit_test_report(verification: Verification) -> str:
    return shared_render_unit_test_report(
        verification,
        phase_number=2,
        verification_commands=VERIFICATION_COMMANDS,
        caveat=(
            "**A green suite is not evidence that the path works.** Phase 1 shipped one over a "
            "workflow that could not complete a run, because every assertion compared the "
            "literal text of expressions rather than checking whether they named anything "
            "real. The counts above say the tests pass; `negative-case-matrix.md` says what "
            "they establish, which for eight of this phase's criteria is not the criterion."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_generator_cli(
        argv,
        description="Build the Phase 2 proof bundle under proof/phase-2/.",
        repo_root=PROJECT_ROOT,
        nested_env=NESTED_RUN_ENV,
        default_output_dir=default_output_dir,
        build=build_bundle,
    )


if __name__ == "__main__":
    sys.exit(main())
