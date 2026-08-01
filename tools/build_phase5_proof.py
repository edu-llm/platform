"""Build the Phase 5 proof bundle under ``proof/phase-5/``.

Phase 5's bundle is unlike the four before it, and the difference is worth stating before
anybody reads one. Phases 0 to 4 each proved that a mechanism works: a contract, a
protected admission path, a container on Batch, a model on a GPU. Their bundles are
arguments about machinery. **This one is an argument about two people**, and its
distinguishing content is that the same run carries a submitter and an approver who are
different, which had never happened in twenty-five prior submissions.

That changes what a reviewer should be looking for. In a mechanism bundle the question is
whether the evidence generalises from the runs that were captured. Here the runs are not a
sample of anything -- there are three, by one person, on one day -- and the question is
narrower and answerable: did the reason code the two-person design exists to produce get
written, by somebody who is not the person who submitted, in a store nothing rewrites.

**Everything here is derived rather than restated.** The criteria come from
``phase5_criteria.py``, which the acceptance gate also reads, so the matrix and
``tools/validate_phase5.py`` cannot disagree. The run facts come from the committed captures
through ``phase5_capture.py``, which is the same reader the tests use. No sentence in this
generator states a criterion status of its own; where one names a check, the status word is
looked up, and ``contradicting_status_claims`` refuses the bundle if a document ever
disagrees with the gate.

**Nothing here rebuilds a bundle inside a fixture.** The coherence half runs on every pull
request and the reproduction half runs nightly behind ``EDULLM_REPRODUCE_PROOFS=1``; a
generator test that rebuilt would be exactly what that split exists to prevent.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.criteria import CriterionSpec, CriterionStatus
from edullm_platform.open_decisions import OpenDecision, open_decisions
from edullm_platform.phase5_capture import (
    CapturedRun,
    admitted_runs,
    branch_protection,
    published_image,
)
from edullm_platform.phase5_criteria import phase5_criteria
from edullm_platform.phase5_evidence import (
    LEAD_APPROVAL_REASON,
    SELF_AUTHORIZED_REASONS,
    AdmittedRunEvidence,
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
    contradicting_status_claims,
    describe_drift,
    file_digest,
    golden_drift,
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
from edullm_platform.status_prose import spell

PHASE: Final = "phase-5"
BUNDLE_SCHEMA_VERSION: Final = 1
GOLDENS_FILENAME: Final = "serialization-goldens.json"
GOLDENS_REPORT_FILENAME: Final = "serialization-goldens.md"
GOLDENS_FILENAMES: Final = (GOLDENS_FILENAME, GOLDENS_REPORT_FILENAME)

BUNDLE_FILENAMES: Final = (
    "README.md",
    "access-control-evidence.md",
    "image-provenance-evidence.md",
    "negative-case-matrix.md",
    "open-decisions.md",
    "schema-compatibility.md",
    "second-person-evidence.md",
    GOLDENS_FILENAME,
    GOLDENS_REPORT_FILENAME,
    "unit-test-report.md",
)

NESTED_RUN_ENV: Final = "EDULLM_PHASE5_PROOF_NESTED"
GENERATOR_TEST_PATH: Final = "tests/test_phase5_proof.py"
GENERATOR_COMMAND: Final = "uv run python tools/build_phase5_proof.py"

#: The committed artifacts Phase 5 rests on, whose digests this bundle records so a reviewer
#: can confirm it describes the tree in front of them. The captures are here as well as the
#: configuration, because a bundle whose evidence moved underneath it is the failure this
#: table exists to make visible.
PHASE5_INPUTS: Final = (
    ".github/CODEOWNERS",
    ".github/workflows/build-research-image.yml",
    ".github/workflows/submit-run.yml",
    "README.md",
    "config/image-exceptions.yaml",
    "config/organization.yaml",
    "fixtures/evidence/phase-5/branch-protection.sanitized.json",
    "fixtures/evidence/phase-5/published-image.sanitized.json",
    "src/edullm_platform/image_resolution.py",
)

#: The library modules Phase 5 added. The repository-wide contract inventory lives in the
#: Phase 0 bundle; repeating every row here would be a second copy going stale.
PHASE5_CONTRACT_MODULES: Final = (
    "edullm_platform.phase5_evidence",
    "edullm_platform.phase5_gate",
)

#: Test modules that carry Phase 5's evidence, by prefix. The reentrant ones are removed
#: rather than listed out, so a module added to that list is dropped from here too.
PHASE5_TEST_PREFIXES: Final = ("tests/test_phase5_", "tests/test_capture_phase5_")

VERIFICATION_COMMANDS: Final = (
    "uv run pytest -q",
    "uv run ruff check .",
    "uv run mypy",
    "uv run python tools/export_schemas.py",
    "uv run python tools/validate_phase5.py",
    GENERATOR_COMMAND,
)


def capture_root(repo_root: Path) -> Path:
    """Where this phase's committed captures live, under the tree being measured.

    Resolved against ``repo_root`` rather than taken from ``phase5_capture.CAPTURE_ROOT``,
    which is anchored at the installed package. A generator run against a copied tree has to
    read that tree's captures, and the tests that build a bundle in a temporary directory
    depend on it.
    """
    return repo_root / "fixtures" / "evidence" / "phase-5"


def read_runs(repo_root: Path) -> tuple[CapturedRun, ...]:
    """Every committed pilot run, refused if there are none.

    Refused rather than returning empty, because a bundle rendered from no runs would read
    as a phase whose evidence is thin rather than as a phase whose evidence is missing.
    """
    runs = admitted_runs(capture_root(repo_root))
    if not runs:
        raise ProofBundleError(
            "no pilot run is committed under fixtures/evidence/phase-5/runs/, so this "
            "bundle would describe a phase about two people with evidence about none"
        )
    return runs


def compute_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    """One digest per committed capture, over the parsed record rather than over the file.

    The tripwire Phases 1, 2 and 3 record, aimed at what this phase actually rests on. Those
    three digest a role's projection, because a role template is the thing that can be
    widened without anybody noticing. Here the thing that can move without anybody noticing
    is a capture: these records are the only evidence that two people used the platform, and
    a reformatted or re-taken one changes what the bundle claims.

    Over the parsed model rather than the file bytes, so reindenting a record is not drift
    and a field changing value is.
    """
    recorded: list[RecordedGolden] = []
    for run in read_runs(repo_root):
        relative = (
            f"fixtures/evidence/phase-5/runs/{run.run_id}/admitted-run.sanitized.json"
        )
        recorded.append(
            RecordedGolden(
                fixture=run.run_id,
                relative_path=relative,
                contract=AdmittedRunEvidence.__name__,
                canonical_json_bytes=len(canonical_json_bytes(run.record)),
                digest=sha256_digest(run.record),
            )
        )
    return tuple(recorded)


def _authorization_rows(runs: Sequence[CapturedRun]) -> list[list[str]]:
    return [
        [
            run.run_id,
            run.record.submitter,
            run.record.authorization.approver,
            run.record.authorization.reason,
            run.record.authorization.claimed_team,
            "yes" if run.record.authorization.team_verified else "no",
        ]
        for run in runs
    ]


def render_second_person(repo_root: Path, checks: Sequence[CriterionSpec]) -> str:
    """The document this bundle exists for: who submitted, who released, and what ran."""

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    runs = read_runs(repo_root)
    released = [run for run in runs if run.record.released_by_another_person]
    submitters = sorted({run.record.submitter for run in runs})
    approvers = sorted({run.record.authorization.approver for run in runs})

    outcome_rows = [
        [
            run.run_id,
            run.record.compute_profile,
            run.record.scheduler_status or "not submitted",
            "—" if run.record.exit_code is None else str(run.record.exit_code),
            ", ".join(run.record.recorded_states) or "none recorded",
            run.record.result_outcome or "no result record",
        ]
        for run in runs
    ]

    return (
        "\n".join(
            [
                "# Phase 5 second-person evidence",
                "",
                (
                    f"{spell(len(runs)).capitalize()} runs, submitted by "
                    f"{', '.join(f'`{name}`' for name in submitters)} and released by "
                    f"{', '.join(f'`{name}`' for name in approvers)}. Rendered from the "
                    "captures committed under `fixtures/evidence/phase-5/runs/`, read through "
                    "the same module the tests read them through."
                ),
                "",
                (
                    "**Why this is the whole phase.** Every accepted decision record written "
                    "before these read `" + "` or `".join(SELF_AUTHORIZED_REASONS) + "`. Both "
                    "are granted authorizations and neither is evidence about the path a "
                    "member takes, because in both the person who approved is the person who "
                    "submitted. Twenty-five dispatches produced twenty-five of them. The "
                    f"reason code below, `{LEAD_APPROVAL_REASON}`, is what the entire "
                    "two-person approval design exists to produce, and it had never been "
                    "written."
                ),
                "",
                "## Authorization, as the decision records carry it",
                "",
                table(
                    ["run", "submitter", "approver", "reason", "claimed team", "team verified"],
                    _authorization_rows(runs),
                ),
                "",
                (
                    (
                        "Every one of them was"
                        if len(released) == len(runs)
                        else f"{spell(len(released)).capitalize()} of the {spell(len(runs))} were"
                    )
                    + " released by "
                    "somebody other than their submitter, which is what check 2 is and is why "
                    f"it is {status_of('2')}. It also closes Phase 2's criterion 3 -- any team "
                    "lead approval succeeding while `approval_scope` is `organization` -- which "
                    "could not be closed by writing code and has been open across every "
                    "submission this platform has ever taken."
                ),
                "",
                (
                    "**`team verified` is `no` on every row, and that is correct rather than a "
                    "defect.** The team a submitter claims is recorded and not enforced: "
                    "nothing binds a team to a person yet and the bindings that would are "
                    "unbuilt. A record reading `yes` here would be evidence for a control "
                    "that does not exist. "
                    "A submitter is told the same thing in the same words -- `team` routes "
                    "approval rather than granting permission -- on the summary every "
                    "accepted submission ends on. That sentence was on the pilot limitations "
                    "page until it was taken out of the README on 2026-07-31, and moving it "
                    "onto the summary put it in front of everybody who submits rather than "
                    "everybody who goes looking."
                ),
                "",
                "## What each run did",
                "",
                table(
                    ["run", "profile", "scheduler", "exit", "recorded states", "result"],
                    outcome_rows,
                ),
                "",
                (
                    "**The failures are committed deliberately.** A phase whose evidence is "
                    "only its successes is a phase that has not been tested, and each of these "
                    "two failed in a way worth keeping."
                ),
                "",
                bullets(
                    (
                        (
                            "The run with no result record was admitted, submitted to Batch, "
                            "and died on an instance resolving its entire command line against "
                            "`$PATH` -- the submitter's shell quoting survived into the form "
                            "field, so `shlex.split` returned one token and the whole line "
                            "became argv[0]. Its states read `runnable, runnable, failed`, "
                            "which is what a container that never started looks like from "
                            "outside, and the absence of a result record is correct rather "
                            "than missing evidence. The contract now refuses a first element "
                            "that is empty or carries whitespace or a quote, so the refusal "
                            "lands at compile ahead of the approval gate rather than on a warm "
                            "instance after a lead has read it."
                        ),
                        (
                            "The run that exited 1 did so on `No API key configured`. Its "
                            "command logged to Weights and Biases, and at the time "
                            "`CONTAINER_SHAPES['cpu-32vcpu']` declared `secrets=()` while "
                            "`gpu-1xa10g` named the W&B secret -- so no run on the CPU profile "
                            "could authenticate. That was a finding rather than a user error, "
                            "and it has since been closed: the CPU execution role may read the "
                            "secret and the CPU job definition injects it. This run predates "
                            "the fix."
                        ),
                    )
                ),
                "",
                "## What the digest that ran establishes",
                "",
                (
                    f"Check 4 is {status_of('4')} and it is the one that most repays reading "
                    "the method rather than the verdict. The digest is compared against "
                    "`container.image` in the scheduler's own description of the job. Before "
                    "this phase, `batch_submit_request` built `ContainerOverrides` with a "
                    "command and an environment and no image, so the container that ran was "
                    "whatever the CloudFormation job definition said, while the digest a "
                    "submitter typed was validated, gated admission through the ECR scan, and "
                    "written immutably into lineage. The two coincided only because the "
                    "exception file happened to contain exactly those digests -- which made the "
                    "lineage record's image provenance true by convention. Reading the digest "
                    "back out of the template would have proved the convention."
                ),
                "",
                (
                    "Each of these runs was submitted against a job definition registered for "
                    "it and named after it, which is the mechanism that makes the digest "
                    "selectable at all. A shared definition pins one image for every run, so a "
                    "matching digest would be a coincidence rather than a property."
                ),
            ]
        )
        + "\n"
    )


def render_image_provenance(repo_root: Path, checks: Sequence[CriterionSpec]) -> str:
    """Commit to digest to container, and the allowlist that used to stand in the way."""

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    image = published_image(capture_root(repo_root))
    runs = read_runs(repo_root)

    return (
        "\n".join(
            [
                "# Phase 5 image provenance evidence",
                "",
                (
                    "How a commit became the container that ran, read from the registry and "
                    "from the scheduler rather than from a template."
                ),
                "",
                "## The image the pilot runs were admitted on",
                "",
                table(
                    ["field", "value"],
                    [
                        ["repository", image.repository_name],
                        ["declared commit", image.commit_sha],
                        ["published tag", image.image_tag],
                        ["image digest", image.image_digest],
                        [
                            "pushed at",
                            image.pushed_at.astimezone(UTC).isoformat(timespec="seconds"),
                        ],
                        ["tags in the repository", str(len(image.published_tags))],
                    ],
                ),
                "",
                (
                    "The tag is the first twelve characters of the commit, which is what ties "
                    "the digest to the commit rather than leaving two facts sitting beside each "
                    "other. The capture refuses to load if the tag is not a prefix of the "
                    "commit it names."
                ),
                "",
                "## One commit, one image",
                "",
                (
                    f"The registry holds {spell(len(image.published_tags))} tags and "
                    f"{spell(len(image.published_tags))} distinct images. That is not a "
                    "coincidence, and check 14 is the criterion that says so. Three mechanisms "
                    "hold at once: the tag carries nothing that varies between builds, both "
                    "ECR repositories set `ImageTagMutability` to `IMMUTABLE`, and "
                    "`build-research-image.yml` resolves the tag in a pre-flight step and skips "
                    "the build entirely when it is already published."
                ),
                "",
                (
                    "**Check 14 was rewritten rather than retired silently, and the bundle "
                    "should say so where a reviewer will see it.** It asked that a commit built "
                    "more than once resolve deterministically to the most recently published "
                    "image and that the decision record name the chosen digest. That state "
                    "cannot occur through the only path that publishes, so the criterion was "
                    "not untested -- it was unreachable by construction, which is a stronger "
                    "outcome than the check was asking for. The criterion now states the "
                    "property that survives and carries the retired sentence in a scope limit. "
                    "The rules for the state that cannot occur remain in "
                    "`image_resolution.py` as unreachable defence, cited as supporting rather "
                    "than proving, with a comment recording which three configuration choices "
                    "would make them live."
                ),
                "",
                "## No hand-written exception stood behind it",
                "",
                (
                    f"Check 3 is {status_of('3')}, and until the unit of review changed it was "
                    "unpassable by construction rather than merely unproven. "
                    "`config/image-exceptions.yaml` held two entries, each naming one image "
                    "digest; an image is refused unless somebody has written its digest there; "
                    "and every build produces a new digest. So exactly two digests in the world "
                    "could be submitted, and every iteration needed a reviewed pull request "
                    "from an admin before it could run -- which is the friction this platform "
                    "removed from choosing an image, arriving one step to the left."
                ),
                "",
                (
                    "The per-digest list is empty now. What a reviewer actually did when "
                    "writing those two entries was read four CVEs and decide they were "
                    "acceptable, and the file records that instead: four reviewed "
                    "vulnerabilities, all inherited from the digest-pinned base every image "
                    "shares. A finding nobody has reviewed still refuses the run, which is the "
                    "thing the per-digest form could not express."
                ),
                "",
                (
                    "The residual is stated where it applies. The registry is on BASIC "
                    "scanning, which reads the operating system package database and does not "
                    "look at Python distributions at all -- so the roughly three gigabytes of "
                    "installed Python in this image was not scanned by anything, and "
                    "\"no unreviewed finding\" is a statement about what was looked at."
                ),
                "",
                "## What each run declared and what it was given",
                "",
                table(
                    ["run", "declared digest", "container digest", "agree"],
                    [
                        [
                            run.run_id,
                            run.record.declared_image_digest,
                            run.record.container_image_digest or "nothing ran",
                            "yes" if run.record.image_that_ran_is_the_image_admitted else "no",
                        ]
                        for run in runs
                    ],
                ),
                "",
                (
                    "**A twelve-character tag is a collision surface and the residual is "
                    "recorded rather than closed.** Two commits sharing a twelve-hex-character "
                    "prefix cannot both publish, and under derivation the second would resolve "
                    "to the first's image -- a lineage record naming commit B for an image "
                    "commit A produced, which is the exact defect class this phase exists to "
                    "close, arriving by a route nothing looks at. Forty-eight bits makes it "
                    "negligible at this volume, and `build-research-image.yml` already refuses "
                    "the colliding build by verifying the published image against the commit. "
                    "The tag stays twelve characters because widening it would falsify two "
                    "committed Phase 1 captures and dissolve the rationale for the one field "
                    "exempt from the secret scan. It was on the pilot limitations page until "
                    "that page was taken out of the README on 2026-07-31, and it is now "
                    "recorded here and nowhere a pilot user reads."
                ),
            ]
        )
        + "\n"
    )


def render_access_control(repo_root: Path, checks: Sequence[CriterionSpec]) -> str:
    """The containment that had to land in the same change as the write grant."""

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    protection = branch_protection(
        capture_root(repo_root)
    )
    owners = [
        line.split()[0]
        for line in (repo_root / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    return (
        "\n".join(
            [
                "# Phase 5 access control evidence",
                "",
                (
                    "Granting write access to this repository is granting merge access to five "
                    "workflow files pinned by name in three IAM trust policies, and to the "
                    "source and configuration that ship inside the two released Lambda zips. "
                    "This document is the containment for that, captured from GitHub rather "
                    "than described."
                ),
                "",
                "## How `main` is protected",
                "",
                table(
                    ["setting", "value"],
                    [
                        ["branch", protection.branch],
                        [
                            "required approving reviews",
                            str(protection.required_approving_review_count),
                        ],
                        [
                            "code-owner review required",
                            "yes" if protection.require_code_owner_reviews else "no",
                        ],
                        [
                            "stale reviews dismissed",
                            "yes" if protection.dismiss_stale_reviews else "no",
                        ],
                        ["enforced for admins", "yes" if protection.enforce_admins else "no"],
                        ["force pushes", "yes" if protection.allow_force_pushes else "no"],
                        ["deletions", "yes" if protection.allow_deletions else "no"],
                        [
                            "conversation resolution required",
                            "yes" if protection.required_conversation_resolution else "no",
                        ],
                        [
                            "required status checks",
                            ", ".join(protection.required_status_checks) or "none",
                        ],
                    ],
                ),
                "",
                (
                    f"**`enforced for admins` is `no`, and check 10 is worded around it.** The "
                    "master plan asks that a change to a workflow file cannot reach `main` "
                    "without a code-owner review. That is false for the three admins and stays "
                    "false by decision: turning `enforce_admins` on makes every pull request "
                    "the author writes wait on the one other code owner, on a repository where "
                    "the author is writing most of them. So the criterion is about what a "
                    f"*member* may do, and it is {status_of('10')} as that narrower claim. A "
                    "gate asserting the unqualified sentence would be asserting something "
                    "untrue about this account, which is worse than a narrower claim that "
                    "holds."
                ),
                "",
                (
                    "The required checks are recorded beside the review requirement because a "
                    "code-owner review with nothing else behind it lets a member merge a red "
                    "branch, which is the same bypass by another route."
                ),
                "",
                "## What a code owner owns",
                "",
                bullets(owners),
                "",
                (
                    "The last four of those were added when write access was first granted to "
                    "somebody who did not build this platform. Until then ownership covered the "
                    "workflows and the infrastructure and left the admission validator's own "
                    "source and the policy it enforces uncovered -- and "
                    "`tools/build_admission_lambda.py` copies `config/*.yaml` and the whole "
                    "`src/edullm_platform` tree into the zip, so a change to either decides "
                    "whether a run is authorized. The test behind check 10 walks the packaged "
                    "set rather than checking that the file merely exists, so the next module "
                    "added under `src/` cannot quietly fall outside it."
                ),
                "",
                "## Who may start a deployment",
                "",
                (
                    f"Check 9 is {status_of('9')}, and it was not built the way the plan "
                    "specified. The plan asked for a repository actor rule in evaluate mode. "
                    "The organization is on the `free` plan, where ruleset enforcement is "
                    "`active` or `disabled` and `evaluate` is Enterprise Cloud only, so "
                    "\"measured against real dispatches before it refuses one\" was "
                    "unavailable."
                ),
                "",
                (
                    "An environment gate is worse than it looks and was rejected on inspection: "
                    "`infra/iam/infra-deployer-role.yaml` pins the OIDC subject with "
                    "`StringLike` to a ref, and naming an `environment:` on a deploy job "
                    "rewrites that claim to `…:environment:<name>` and silently revokes every "
                    "deployment."
                ),
                "",
                (
                    "What shipped instead is a guard step, first in each deploy job and before "
                    "the checkout, failing rather than skipping, tied to the admin list in "
                    "`config/organization.yaml`. It guards the dispatch path only -- a control "
                    "that also blocked the merge path would have stopped deployment entirely, "
                    "which is why a push to `main` deploying without meeting the guard is "
                    "asserted rather than treated as a hole. The three copies are asserted "
                    "word-for-word identical, because three copies that drift are one workflow "
                    "silently unguarded."
                ),
            ]
        )
        + "\n"
    )


def render_open_decisions(decisions: Sequence[OpenDecision]) -> str:
    if not decisions:
        return (
            "# Phase 5 open decisions\n"
            "\n"
            "**No open decision is recorded against this phase.** That is a statement about "
            "the register rather than a claim that Phase 5 raised no questions: the register "
            "refuses an entry with fewer than two options and forbids a recommendation, so a "
            "question with an obvious answer does not belong in it. The judgements this phase "
            "took and did not defer -- narrowing check 10 to members, rewriting check 14, "
            "leaving `enforce_admins` off, and shipping a guard step in place of an actor rule "
            "-- are argued where they apply, in the criterion's own scope limits and in the "
            "documents beside this one, rather than collected here as though they were still "
            "open.\n"
        )
    rows = [[decision.number, decision.question, decision.lands_in] for decision in decisions]
    return (
        "\n".join(
            [
                "# Phase 5 open decisions",
                "",
                "Questions this phase surfaced and did not settle.",
                "",
                table(["#", "question", "lands in"], rows),
            ]
        )
        + "\n"
    )


def phase5_models(repo_root: Path) -> tuple[ModelRecord, ...]:
    return tuple(
        record for record in model_records(repo_root) if record.module in PHASE5_CONTRACT_MODULES
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
                "# Phase 5 schema compatibility report",
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
                    "`AdmittedRunEvidence` is the one worth reading. It is the only record in "
                    "this repository that spans two systems -- the lineage store and the "
                    "scheduler -- and it does so because the central Phase 5 claim is a "
                    "comparison between them. Splitting it would put the two halves of one "
                    "assertion into two records that nothing requires to be about the same run."
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
        "# Phase 5 negative-case matrix",
        "",
        (
            f"The {spell(len(criteria))} Phase 5 acceptance criteria, mapped to the tests cited "
            "for each one by node id. Each cited node id was collected and executed by this "
            "generator before the bundle was written; a citation pytest cannot collect aborts "
            "generation rather than being printed."
        ),
        "",
        (
            "This mapping is defined once, in `src/edullm_platform/phase5_criteria.py`. The "
            "acceptance gate reads the same definition and executes the same node ids, so this "
            "matrix and `tools/validate_phase5.py` cannot disagree."
        ),
        "",
        (
            "**The numbering is the migration document's eleven checks followed by four.** "
            "Criteria 1 to 11 are the checks the 2026-07-29 re-cut listed, in its order. "
            "Criteria 12 to 15 are what deriving a run's image from its declared commit owes "
            "over merely comparing the two, and they are appended rather than interleaved so "
            "that nothing already argued about had to be renumbered. Criterion 14 keeps its "
            "number after being rewritten, for the same reason."
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
                    "of these. Relabelling one would turn the gate green without anything "
                    "changing in the account -- and because a deferral may never be "
                    "pilot-blocking, it would open the pilot rung at the same time. That is "
                    "two controls disabled by one word."
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
    runs: Sequence[CapturedRun],
) -> tuple[str, ...]:
    """What this bundle does not establish, read off the tree rather than remembered.

    No entry states a criterion status of its own: where one names a check, the status word
    comes from ``checks``, so a limitation cannot disagree with the verdict the gate
    reached. ``contradicting_status_claims`` refuses the bundle if one ever does.
    """

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    submitters = sorted({run.record.submitter for run in runs})

    return (
        (
            f"Everything about people here rests on {spell(len(runs))} runs by "
            f"{spell(len(submitters))} submitter on one day. That is enough to establish that "
            "the two-person path completes, which is the thing that had never been "
            "established; it is not a sample from which anything about how the platform "
            "behaves for a second, third or tenth person follows."
        ),
        (
            "The cohort is three and two of them are leads, who authorize their own routine "
            "runs by design. So the only person in it whose submission needs releasing by "
            f"somebody else at all is the one non-lead, and check 2 -- {status_of('2')} -- "
            "rests entirely on him. If he had dropped out the phase would have lost its point "
            "rather than a participant, and the correct response would have been to seat "
            "another non-lead rather than to record the criterion closed by self-authorization."
        ),
        (
            f"Check 6 is {status_of('6')}, which passes the gate and proves nothing. All three "
            "runs went to the CPU profile carrying a print statement and two W&B calls, so no "
            "pilot run has trained anything, written a checkpoint, or touched a GPU -- and "
            "that is the largest thing this bundle does not establish. The deferral moved the "
            "observation to the closeout campaign, where it still closes this phase's gate; it "
            "did not make the observation less necessary, and a reader who takes the green "
            "verdict for a research workload having run is reading it wrong."
        ),
        (
            "No CPU run could reach Weights and Biases while these three ran. "
            "`CONTAINER_SHAPES['cpu-32vcpu']` declared `secrets=()` while `gpu-1xa10g` named "
            "the W&B secret, so the third pilot run's command failed on `No API key "
            f"configured`. Check 8 is {status_of('8')} on what the submitter is told, which was "
            "honest and, on that profile and that day, pointed at a project nothing could write "
            "to. The gap is closed -- both profiles now carry the same secrets -- so what these "
            "runs demonstrate about W&B is the defect rather than the remedy."
        ),
        (
            "The result manifest names no W&B run for any of these, because "
            "`lifecycle_projection` hardcodes `wandb_run=None` on every one it writes. Recording "
            "the run in lineage is unbuilt, and the current behaviour is asserted rather than "
            "worked around, so the day it changes a test fails and this sentence gets reread."
        ),
        (
            f"Check 7 is {status_of('7')} against the workflow rather than against a refusal "
            "somebody received, which is one step weaker. No pilot submission has been refused "
            "on its merits: the two failed dispatches were a tool invoked without a required "
            "argument and a container that could not start, and neither is a refusal. What is "
            "asserted is what the workflow does with a refusal it is given."
        ),
        (
            "The branch-protection record expires thirty days after it was observed, and the "
            "cited tests fail once it does. Nothing about the runs will have changed on that "
            "date -- every lineage object is in a write-once store -- and what will have lapsed "
            "is anybody's knowledge of how the repository is configured. That is the window "
            "working rather than a defect."
        ),
        (
            "`enforce_admins` is off, so the three admins may merge a workflow change without "
            "a code-owner review. Check 10 says `a member` for that reason and the captured "
            "record carries the field, but a reader should not leave this bundle believing the "
            "control binds everybody."
        ),
        (
            "The image scan behind check 3 is BASIC, which reads the operating system package "
            "database and does not look at Python distributions. About three gigabytes of "
            "installed Python in the pilot image was scanned by nothing."
        ),
        (
            "The nested verification run excludes every test module that builds a proof bundle "
            f"({', '.join(GENERATOR_TEST_PATHS)}), because those tests invoke a generator and "
            "would recurse. They run in the reviewer's own `uv run pytest -q`."
        ),
        (
            "This bundle describes the working tree at generation time, which may differ from "
            "the commit named above. The input digests recorded below identify exactly what was "
            "measured."
        ),
        (
            "Nothing forces this bundle to stay current. It is a snapshot, and its counts go "
            f"stale as soon as a test is added or a capture is retaken. Re-run "
            f"`{GENERATOR_COMMAND}` and read the diff before accepting a phase gate."
        ),
    )


def input_digest_table(repo_root: Path) -> tuple[tuple[str, str], ...]:
    return tuple((path, file_digest(repo_root / path)) for path in sorted(PHASE5_INPUTS))


def render_index(
    *,
    generated_at: datetime,
    commit_sha: str,
    criteria: Sequence[CriterionSpec],
    verification: Verification,
    goldens: Sequence[RecordedGolden],
    models: Sequence[ModelRecord],
    runs: Sequence[CapturedRun],
    input_digests: Sequence[tuple[str, str]],
    limitations: Sequence[str],
) -> str:
    covered = [check.number for check in criteria if check.status is CriterionStatus.COVERED]
    deferred = [check.number for check in criteria if check.status is CriterionStatus.DEFERRED]
    gaps = [check.number for check in criteria if check.status is CriterionStatus.GAP]
    blocking = [check.number for check in criteria if check.pilot_blocking]
    unmet = [check.number for check in criteria if check.pilot_blocking and check.status is CriterionStatus.GAP]
    released = [run for run in runs if run.record.released_by_another_person]

    return (
        "\n".join(
            [
                "# Phase 5 proof bundle",
                "",
                f"Phase: {PHASE}",
                f"Bundle schema version: {BUNDLE_SCHEMA_VERSION}",
                f"Source commit: {commit_sha}",
                f"Generated: {generated_at.astimezone(UTC).isoformat(timespec='seconds')}",
                "",
                (
                    "This bundle exists so that a reviewer can decide whether Phase 5 is done "
                    "without reading the test suite. Everything it claims was executed by "
                    f"`{GENERATOR_COMMAND}` at generation time. {standing(gaps, deferred)}"
                ),
                "",
                (
                    "**Read this first.** Phase 5's claim is not about a mechanism. Every phase "
                    "before it proved that something works; this one adds no capability at all "
                    "and asks whether the capabilities already built are reachable by somebody "
                    "who did not build them. The answer arrived on one day: "
                    f"{spell(len(runs))} runs were submitted by a researcher who is not the "
                    f"author, {'every one' if len(released) == len(runs) else spell(len(released))}"
                    " of them was released by a lead who is not the submitter, and the "
                    "decision records carry "
                    f"`{LEAD_APPROVAL_REASON}` -- the reason code the entire two-person "
                    "approval design exists to produce, and which had never been written in "
                    "twenty-five prior dispatches."
                ),
                "",
                (
                    "**What this bundle does not establish is larger than its one outstanding "
                    "check, and the gate being green does not shrink it.** Every one of the "
                    "three runs went to the CPU profile carrying a print statement, so nothing "
                    "here was trained, no checkpoint was written and no GPU was touched. What "
                    "was established is that the two-person path completes, which is what the "
                    "phase is named after and what had never happened. It is not evidence that "
                    "this platform carries a research workload for somebody who did not build "
                    "it."
                ),
                "",
                (
                    "The one check that is outstanding is a different kind of open from every "
                    "other in this repository. The others are captures nobody has taken. This "
                    "one wants a GPU run claiming a team other than `platform` and writing a "
                    "checkpoint, and each of those three works and has been exercised "
                    "separately -- so it closes on one submission rather than on any work. Its "
                    "observation moved to the closeout campaign on 2026-07-31 and still closes "
                    "this phase's gate rather than the campaign's own, which is why the verdict "
                    "below is green while the phase is not finished. The Result table says which "
                    "check, and `negative-case-matrix.md` carries the reason and the trigger."
                ),
                "",
                "## Contents",
                "",
                bullets(
                    [
                        (
                            f"`negative-case-matrix.md` — each of the {spell(len(criteria))} "
                            "Phase 5 acceptance criteria mapped to the tests cited for it, by "
                            "node id, with every gap and every deferral stated. Read this one "
                            "first."
                        ),
                        (
                            "`second-person-evidence.md` — who submitted, who released, what "
                            "each run did, and why the two that failed are committed. This is "
                            "the document the phase exists for."
                        ),
                        (
                            "`image-provenance-evidence.md` — the commit, the tag, the digest "
                            "and the container, and the two-entry allowlist that used to stand "
                            "between a freshly built image and a run."
                        ),
                        (
                            "`access-control-evidence.md` — how `main` is protected, what a "
                            "code owner owns, and who may start a deployment. The containment "
                            "that had to land in the same change as the write grant."
                        ),
                        (
                            "`open-decisions.md` — what this phase surfaced and did not settle, "
                            "and why the judgements it took are argued where they apply rather "
                            "than collected here."
                        ),
                        (
                            "`serialization-goldens.md` and `serialization-goldens.json` — the "
                            "recorded canonical digest of every committed pilot-run capture, "
                            "and the tripwire that fails when one drifts."
                        ),
                        (
                            "`schema-compatibility.md` — the contract models the modules behind "
                            "this bundle define, with the structural digest of each."
                        ),
                        (
                            "`unit-test-report.md` — summarised pass and fail counts, per "
                            "module and for the whole suite, with the commands to reproduce "
                            "them."
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
                        [
                            "criteria COVERED",
                            f"{len(covered)} ({', '.join(covered)})" if covered else "0",
                        ],
                        [
                            "criteria DEFERRED",
                            f"{len(deferred)} ({', '.join(deferred)})" if deferred else "0",
                        ],
                        [
                            "criteria GAP (each one fails the gate)",
                            f"{len(gaps)} ({', '.join(gaps)})" if gaps else "0",
                        ],
                        [
                            "criteria pilot-blocking",
                            f"{len(blocking)} ({', '.join(blocking)})",
                        ],
                        [
                            "pilot-blocking criteria unmet",
                            f"{len(unmet)} ({', '.join(unmet)})" if unmet else "0",
                        ],
                        ["pilot runs captured", str(len(runs))],
                        ["pilot runs released by another person", str(len(released))],
                        ["capture digests recorded", str(len(goldens))],
                        ["contract models in schema-compatibility.md", str(len(models))],
                    ],
                ),
                "",
                "## Verification commands",
                "",
                "Run these from the repository root.",
                "",
                "```",
                "\n".join(VERIFICATION_COMMANDS),
                "```",
                "",
                gate_verdict(gaps, phase_number=5),
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


def render_goldens_report(goldens: Sequence[RecordedGolden]) -> str:
    rows = [
        [golden.fixture, golden.relative_path, golden.contract, str(golden.canonical_json_bytes), golden.digest]
        for golden in goldens
    ]
    return (
        "\n".join(
            [
                "# Phase 5 serialization goldens",
                "",
                (
                    f"The canonical digest of each of the {spell(len(goldens))} committed "
                    "pilot-run captures, taken over the parsed record rather than over the file "
                    "bytes. Reindenting a capture is therefore not drift; a field changing "
                    "value is."
                ),
                "",
                (
                    "Phases 1, 2 and 3 record this tripwire over role templates, because a role "
                    "is the thing that can be widened without anybody noticing. Here the thing "
                    "that can move without anybody noticing is a capture: these records are the "
                    "only evidence that two people used this platform, and re-taking one after "
                    "the account has moved on would change what the bundle claims while leaving "
                    "every test green."
                ),
                "",
                table(
                    ["run", "path", "contract", "canonical bytes", "digest"],
                    rows,
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
    """Record the digest of each committed capture, before anything else is written."""
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
    criteria = phase5_criteria()
    goldens = compute_goldens(repo_root)
    goldens_written = write_goldens(output_dir, goldens, criteria, regenerate=regenerate_goldens)

    resolved = verify_repository(repo_root) if verification is None else verification
    models = phase5_models(repo_root)
    runs = read_runs(repo_root)
    documents = {
        "unit-test-report.md": render_unit_test_report(resolved),
        "negative-case-matrix.md": render_matrix(criteria, resolved),
        "second-person-evidence.md": render_second_person(repo_root, criteria),
        "image-provenance-evidence.md": render_image_provenance(repo_root, criteria),
        "access-control-evidence.md": render_access_control(repo_root, criteria),
        "open-decisions.md": render_open_decisions(open_decisions()),
        "schema-compatibility.md": render_schema_report(models),
    }
    documents["README.md"] = render_index(
        generated_at=generated_at,
        commit_sha=source_commit_sha(repo_root),
        criteria=criteria,
        verification=resolved,
        goldens=goldens,
        models=models,
        runs=runs,
        input_digests=input_digest_table(repo_root),
        limitations=known_limitations(criteria, runs),
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


# --------------------------------------------------------------------------------------
# The shared generator machinery, named locally so call sites and tests read unchanged.
# --------------------------------------------------------------------------------------


def default_output_dir(repo_root: Path) -> Path:
    return bundle_directory(repo_root, PHASE)


def establish_coherence(repo_root: Path) -> Coherence:
    return shared_establish_coherence(
        repo_root,
        criteria=phase5_criteria(),
        nested_env=NESTED_RUN_ENV,
        test_prefixes=PHASE5_TEST_PREFIXES,
    )


def verify_repository(repo_root: Path) -> Verification:
    return shared_verify_repository(
        repo_root,
        criteria=phase5_criteria(),
        nested_env=NESTED_RUN_ENV,
        test_prefixes=PHASE5_TEST_PREFIXES,
    )


def render_unit_test_report(verification: Verification) -> str:
    return shared_render_unit_test_report(
        verification,
        phase_number=5,
        verification_commands=VERIFICATION_COMMANDS,
        caveat=(
            "**A green suite says nothing about whether anybody can use this.** That is the "
            "whole premise of the phase: every capability it measures was already technically "
            "possible and already covered by passing tests, while being unreachable by "
            "everybody except the person who wrote them. What changed is not the counts below "
            "-- it is that three of the runs behind `second-person-evidence.md` were submitted "
            "by somebody else."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_generator_cli(
        argv,
        description="Build the Phase 5 proof bundle under proof/phase-5/.",
        repo_root=PROJECT_ROOT,
        nested_env=NESTED_RUN_ENV,
        default_output_dir=default_output_dir,
        build=build_bundle,
    )


if __name__ == "__main__":
    sys.exit(main())
