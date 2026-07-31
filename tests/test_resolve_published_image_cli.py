"""The command that asks the registry which image a commit published, and what is in it.

Every case runs the real command against a stub ``aws`` on PATH, the way
``tests/test_capture_phase3_evidence_cli.py`` does, because what is being claimed here is
about a process launched with an argument list. A patched ``subprocess.run`` cannot say
whether ``imageTag=8076c0775336`` arrived as one argument, and the tag is the whole of how
a commit is joined to its image.

**Nothing in this file is a twelve-digit run, and that is deliberate rather than fussy.**
``tests/test_evidence.py`` scans the tracked tree for exactly that shape, and the account id
this tool must never disclose is written down nowhere in this repository. Where a test needs
one it is assembled at import, and where a test needs to say that something was not
disclosed it asks ``scan_for_secrets`` rather than searching for a value it had to write
down first.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanPolicy,
    ImageScanStatus,
    ImageScanSummary,
    image_scan_is_reviewed,
)
from edullm_platform.evidence import scan_allowing_content_digests, scan_for_secrets
from edullm_platform.image_resolution import PublishedImage, resolve_image
from tools.resolve_published_image import IMAGE_TAG_LENGTH, build_parser, main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "config" / "repositories.yaml"
POLICY = PROJECT_ROOT / "config" / "policy.yaml"

REPOSITORY = "OLMo-core"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
REGION = "us-east-1"

#: A commit sha from this repository's own history, so the shape is one that has existed.
COMMIT = "8076c077533eb79742f4ed22aade439df123a593"
TAG = COMMIT[:IMAGE_TAG_LENGTH]

PUBLISHED_DIGEST = "sha256:" + "1a" * 32
PUSHED_AT = "2026-07-26T22:05:49+00:00"
SCANNED_AT = "2026-07-26T22:07:12+00:00"

#: Twelve digits, assembled at import rather than written down. It stands for the account
#: id an AWS error message names, which is the one thing out of a failed lookup that must
#: never reach a world-readable log.
UNDISCLOSED_ACCOUNT = "".join(str(digit % 10) for digit in range(2, 14))

DESCRIBE_IMAGES = f"ecr describe-images --repository-name {ECR_REPOSITORY} --image-ids imageTag="
DESCRIBE_FINDINGS = (
    f"ecr describe-image-scan-findings --repository-name {ECR_REPOSITORY} --image-id imageDigest="
)


def described_image(**overrides: Any) -> dict[str, Any]:
    """One image as ``ecr:DescribeImages`` reports it, registry id and all.

    The ``registryId`` is the account, which is why it is here: the answer this tool reads
    carries it, and every field it does not copy out is a field that cannot leak.
    """
    detail: dict[str, Any] = {
        "registryId": UNDISCLOSED_ACCOUNT,
        "repositoryName": ECR_REPOSITORY,
        "imageDigest": PUBLISHED_DIGEST,
        "imageTags": [TAG],
        "imageSizeInBytes": 1_842_003_611,
        "imagePushedAt": PUSHED_AT,
        "imageManifestMediaType": "application/vnd.docker.distribution.manifest.v2+json",
    }
    detail.update(overrides)
    return {"imageDetails": [detail]}


def described_findings(
    *,
    status: str = "COMPLETE",
    findings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One scan as ``ecr:DescribeImageScanFindings`` reports it.

    The severity counts are the four criticals and eight highs the only published image
    actually carries, all of them Debian base-OS packages, because a summary of a clean
    image would not exercise the gate this feeds.
    """
    described: dict[str, Any] = {
        "registryId": UNDISCLOSED_ACCOUNT,
        "repositoryName": ECR_REPOSITORY,
        "imageId": {"imageDigest": PUBLISHED_DIGEST},
        "imageScanStatus": {"status": status, "description": "reported by the registry"},
    }
    if findings is not None:
        described["imageScanFindings"] = findings
    return described


COMPLETE_FINDINGS = {
    "imageScanCompletedAt": SCANNED_AT,
    "vulnerabilitySourceUpdatedAt": SCANNED_AT,
    "findingSeverityCounts": {"CRITICAL": 4, "HIGH": 8, "MEDIUM": 21, "LOW": 30},
    "findings": [],
}


def aws_error(code: str, operation: str) -> str:
    """What the CLI prints when a service refuses, account id and all."""
    return (
        f"An error occurred ({code}) when calling the {operation} operation: User: "
        f"arn:aws:sts::{UNDISCLOSED_ACCOUNT}:assumed-role/sbsandbox-intern-edullm-image-"
        "resolver/resolve is not authorized to perform this action"
    )


def install_aws_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: dict[str, Any] | None = None,
    failures: dict[str, tuple[int, str]] | None = None,
) -> Path:
    """Put an ``aws`` on PATH that answers by the leading words of the call it was given.

    Matched on a prefix of the whole argument list, because both calls this tool makes are
    ``ecr describe-`` something and only the rest of the line tells them apart.
    """
    responses: dict[str, Any] = {
        f"{DESCRIBE_IMAGES}{TAG}": described_image(),
        f"{DESCRIBE_FINDINGS}{PUBLISHED_DIGEST}": described_findings(
            findings=dict(COMPLETE_FINDINGS)
        ),
        **(answers or {}),
    }
    refusals = failures or {}
    recording = tmp_path / "aws-calls.txt"
    branches = []
    for key, (status, message) in refusals.items():
        branches.append(
            f'  "{key}"*)\n    printf \'%s\\n\' {json.dumps(message)} >&2\n'
            f"    exit {status}\n    ;;"
        )
    for key, answer in responses.items():
        if key in refusals:
            continue
        body = f"cat <<'RESPONSE'\n{json.dumps(answer)}\nRESPONSE"
        branches.append(f'  "{key}"*)\n{body}\n    ;;')
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
        'case "$*" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def resolve(
    tmp_path: Path,
    *,
    repository: str = REPOSITORY,
    commit_sha: str = COMMIT,
    registry: Path | None = None,
    policy: Path | None = None,
    output: Path | None = None,
) -> int:
    return main(
        [
            "--registry",
            str(registry if registry is not None else REGISTRY),
            # The shipped policy, so what counts as a blocking finding here is what counts
            # everywhere else. A fixture policy would let these tests agree with themselves
            # about a severity list nothing deploys.
            "--policy",
            str(policy if policy is not None else POLICY),
            "--repository",
            repository,
            "--commit-sha",
            commit_sha,
            "--aws-region",
            REGION,
            "--output",
            str(output if output is not None else tmp_path / "published-image.json"),
        ]
    )


def written(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "published-image.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def calls(recording: Path) -> list[str]:
    if not recording.exists():
        return []
    return recording.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------------------
# What it asks the registry, and where each part of the question comes from
# ---------------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_tag_it_looks_up_is_the_twelve_characters_the_build_workflow_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: look the whole commit up, or the first seven characters of it.

    ``build-research-image.yml`` tags a published image with ``${COMMIT_SHA:0:12}`` and
    nothing else, so any other slice is a tag that has never existed -- and the answer is
    ``ImageNotFoundException``, which this tool reports as a commit nobody built. The
    submitter is then told to go and build a commit whose image is already published.
    """
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    assert len(TAG) == 12
    assert f"imageTag={TAG}" in calls(recording)[0]


@pytest.mark.slow
def test_the_ecr_repository_is_read_from_the_registry_and_never_from_the_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: take the ECR repository name as an argument.

    The same reasoning the deleted ``write_image_provenance.py`` used. This job holds a
    role that may describe any ``sbsandbox-intern-edullm-*`` repository, so a
    caller-supplied name is a caller-supplied choice of which repository's images a
    submission is resolved against -- and the manifest would still name the repository the
    form declared.
    """
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    for call in calls(recording):
        assert f"--repository-name {ECR_REPOSITORY} " in f"{call} "
    assert "--ecr-repository" not in build_parser().format_usage()


@pytest.mark.slow
def test_the_calls_name_the_region_they_were_given_and_no_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: name a profile, as the five capture tools do.

    Those run from a laptop against an SSO session. This runs on a GitHub Actions runner
    under a role the workflow assumed, so the credentials are in the environment and there
    is no profile to name -- and naming one is not a harmless default: the CLI answers a
    profile that is not in the config file with ProfileNotFound before it reaches ECR.
    """
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    for call in calls(recording):
        assert f"--region {REGION}" in call
        assert "--profile" not in call


# ---------------------------------------------------------------------------------------
# What it writes down
# ---------------------------------------------------------------------------------------


@pytest.mark.slow
def test_a_published_commit_is_written_with_its_digest_and_when_the_registry_took_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    document = written(tmp_path)
    assert document["published"] == [
        {"image_digest": PUBLISHED_DIGEST, "pushed_at": "2026-07-26T22:05:49.000000Z"}
    ]


@pytest.mark.slow
def test_what_it_writes_is_what_resolve_image_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam. Mutation: rename either field, or drop the offset off the instant.

    This document exists for exactly one reader, and the two halves are written and read in
    different jobs -- one holding AWS credentials and one deliberately holding none -- so
    nothing else would notice them disagreeing until a submission was dispatched.
    """
    install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    published = [
        PublishedImage(
            image_digest=str(entry["image_digest"]),
            pushed_at=datetime.fromisoformat(str(entry["pushed_at"])),
        )
        for entry in written(tmp_path)["published"]
    ]
    resolved = resolve_image(commit_sha=COMMIT, published=published, override=None)
    assert resolved.image_digest == PUBLISHED_DIGEST
    assert resolved.chosen_from == 1
    assert published[0].pushed_at == datetime(2026, 7, 26, 22, 5, 49, tzinfo=UTC)


@pytest.mark.slow
def test_the_scan_is_written_as_the_counts_the_registry_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    summary = ImageScanSummary.model_validate(written(tmp_path)["image_scan"])
    assert summary.status is ImageScanStatus.COMPLETE
    assert (summary.critical, summary.high, summary.medium, summary.low) == (4, 8, 21, 30)
    assert summary.scanned_at == datetime(2026, 7, 26, 22, 7, 12, tzinfo=UTC)


@pytest.mark.slow
def test_the_summary_it_writes_is_the_one_the_scan_gate_will_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The findings are real ones. Mutation: report zero counts for a scan it did not read.

    Four criticals and eight highs is what the only published image carries, every one of
    them a Debian base-OS package. Recording them is the point of the second call: without
    it the summary is absent, ``image_scan_is_reviewed`` reads that as nobody having looked,
    and every submission is refused against a two-entry allowlist.
    """
    install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    summary = ImageScanSummary.model_validate(written(tmp_path)["image_scan"])
    policy = ImageScanPolicy(blocking_severities=["CRITICAL", "HIGH"])
    empty = ImageScanExceptionRegistry(schema_version=1)
    assert not image_scan_is_reviewed(
        image_digest=PUBLISHED_DIGEST, summary=summary, policy=policy, registry=empty
    )
    excepted = ImageScanExceptionRegistry.model_validate(
        {
            "schema_version": 1,
            "exceptions": [
                {
                    "image_digest": PUBLISHED_DIGEST,
                    "reason": "every finding is a Debian base-OS package this project cannot patch",
                    "recorded_by": "caiiris",
                    "recorded_at": "2026-07-26T22:30:00.000000Z",
                }
            ],
        }
    )
    assert image_scan_is_reviewed(
        image_digest=PUBLISHED_DIGEST, summary=summary, policy=policy, registry=excepted
    )


@pytest.mark.slow
def test_the_summary_it_prints_names_no_digest_and_carries_nothing_that_looks_like_a_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = resolve(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert scan_for_secrets(captured.out) == captured.out
    summary = json.loads(captured.out)
    assert summary["ecr_repository"] == ECR_REPOSITORY
    assert summary["image_tag"] == TAG
    assert summary["published_images"] == 1
    assert summary["image_scan_status"] == "COMPLETE"


# ---------------------------------------------------------------------------------------
# The two absences, which are answers rather than failures
# ---------------------------------------------------------------------------------------


@pytest.mark.slow
def test_a_commit_with_no_published_image_is_recorded_as_nothing_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: exit non-zero here, and refuse the submission from this job.

    The refusal belongs to ``resolve_image``, whose message names the build workflow and
    tells the submitter to go and build the commit. Nothing this tool could say would be
    better, and a failure here would arrive as a red job rather than as a refusal a
    submitter can read.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            f"{DESCRIBE_IMAGES}{TAG}": (
                254,
                aws_error("ImageNotFoundException", "DescribeImages"),
            )
        },
    )

    exit_code = resolve(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    # Findings null rather than empty, for the same reason the summary is. Nothing was
    # scanned because nothing was published, and an empty list would say the registry looked
    # and found nothing blocking -- which is the one reading that opens the gate.
    assert written(tmp_path) == {
        "published": [],
        "image_scan": None,
        "blocking_findings": None,
    }
    assert json.loads(captured.out)["published_images"] == 0


@pytest.mark.slow
def test_an_image_whose_scan_has_not_started_is_recorded_as_no_summary_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: write a summary with zero counts when the registry has no scan.

    ECR scans on push and does it asynchronously, so a freshly published image answers
    ScanNotFoundException for a while. A zero-count summary reads as an image somebody
    looked at and found clean; an absent one reads as nobody having looked, which is what
    happened and is the fail-closed direction.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            f"{DESCRIBE_FINDINGS}{PUBLISHED_DIGEST}": (
                254,
                aws_error("ScanNotFoundException", "DescribeImageScanFindings"),
            )
        },
    )

    assert resolve(tmp_path) == 0

    document = written(tmp_path)
    assert document["image_scan"] is None
    assert len(document["published"]) == 1


@pytest.mark.slow
def test_a_scan_still_running_is_recorded_as_running_rather_than_as_no_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: keep the counts and drop the status.

    ``COMPLETE`` with no findings and ``IN_PROGRESS`` with no findings are the same numbers
    and opposite facts, which is why ``ImageScanSummary`` carries the status at all.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={
            f"{DESCRIBE_FINDINGS}{PUBLISHED_DIGEST}": described_findings(
                status="IN_PROGRESS",
                findings={
                    "imageScanCompletedAt": SCANNED_AT,
                    "findingSeverityCounts": {},
                },
            )
        },
    )

    assert resolve(tmp_path) == 0

    summary = ImageScanSummary.model_validate(written(tmp_path)["image_scan"])
    assert summary.status is ImageScanStatus.IN_PROGRESS
    assert summary.total == 0
    assert not image_scan_is_reviewed(
        image_digest=PUBLISHED_DIGEST,
        summary=summary,
        policy=ImageScanPolicy(blocking_severities=["CRITICAL"]),
        registry=ImageScanExceptionRegistry(schema_version=1),
    )


# ---------------------------------------------------------------------------------------
# What stops it, and what each refusal is allowed to say
# ---------------------------------------------------------------------------------------


@pytest.mark.slow
def test_a_lookup_the_registry_refused_stops_the_tool_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: treat any failed lookup as a commit with nothing published.

    A refused lookup and an unbuilt commit would then be one answer, and the submitter of a
    perfectly well built commit would be told to go and build it -- while the reason the
    role could not read the registry went unreported.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            f"{DESCRIBE_IMAGES}{TAG}": (254, aws_error("AccessDeniedException", "DescribeImages"))
        },
    )

    exit_code = resolve(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines() == [
        "aws_call_failed:DescribeImages:AccessDeniedException"
    ]
    assert not (tmp_path / "published-image.json").exists()


@pytest.mark.slow
def test_a_refused_scan_lookup_is_not_quietly_read_as_an_unscanned_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: fold every scan failure into the absent-summary branch.

    Absent is the fail-closed answer, so folding a refusal into it is safe for the run and
    silent about the role: a permission this workflow lost would show up as every
    submission needing an exception, months later, with nothing pointing at the cause.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            f"{DESCRIBE_FINDINGS}{PUBLISHED_DIGEST}": (
                254,
                aws_error("AccessDeniedException", "DescribeImageScanFindings"),
            )
        },
    )

    exit_code = resolve(tmp_path)

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines() == [
        "aws_call_failed:DescribeImageScanFindings:AccessDeniedException"
    ]
    assert not (tmp_path / "published-image.json").exists()


@pytest.mark.slow
def test_no_failure_discloses_the_account_the_service_named_in_its_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: print the CLI's stderr, which is what a reader of a red job wants.

    An AWS refusal names the caller's ARN and an ARN carries the account id, and this job's
    log is readable by anybody who can see the repository.
    """
    install_aws_stub(
        tmp_path,
        monkeypatch,
        failures={
            f"{DESCRIBE_IMAGES}{TAG}": (254, aws_error("AccessDeniedException", "DescribeImages"))
        },
    )

    exit_code = resolve(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert scan_for_secrets(captured.out + captured.err) == captured.out + captured.err


@pytest.mark.slow
def test_nothing_it_writes_carries_anything_that_looks_like_a_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # scan_allowing_content_digests rather than scan_for_secrets, because the document
    # exists to carry an image digest and the strict scan reads sixty-four hexadecimal
    # characters as a credential. Everything else it refuses -- an account id, a key, a
    # token -- is still refused here.
    install_aws_stub(tmp_path, monkeypatch)

    assert resolve(tmp_path) == 0

    text = (tmp_path / "published-image.json").read_text(encoding="utf-8")
    assert scan_allowing_content_digests(text) == text


@pytest.mark.slow
def test_an_answer_that_names_no_digest_stops_rather_than_resolving_to_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # An image detail with no digest is not an image, and skipping it would turn a
    # malformed answer into a commit with nothing published -- the one refusal whose
    # message sends the submitter to build something that is already built.
    install_aws_stub(
        tmp_path,
        monkeypatch,
        answers={f"{DESCRIBE_IMAGES}{TAG}": {"imageDetails": [{"imageTags": [TAG]}]}},
    )

    exit_code = resolve(tmp_path)

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "published_image_unreadable"
    assert not (tmp_path / "published-image.json").exists()


def test_an_unregistered_repository_exits_as_a_refusal_rather_than_as_a_tool_that_could_not_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: return ``EXIT_UNUSABLE`` here, which is what it used to return.

    The two exit codes mean different things to the job that calls this, and the difference
    reaches a person. ``EXIT_UNUSABLE`` says the tool could not find out, and the submitting
    workflow answers it with "This is not a refusal on the merits" -- which is a true
    sentence about a policy file that will not parse and a false one about a repository
    nobody registered. That is a refusal on the merits, and the most actionable one this
    platform produces, because the fix is a pull request against config/repositories.yaml.

    Reported here rather than left to the compile job, which also refuses it. Refusing twice
    is not the problem; refusing the second time with a better sentence than the first is,
    because the first is the one the submitter reads.
    """
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = resolve(tmp_path, repository="dolma")

    assert exit_code == 1
    assert exit_code != 2, "an unregistered repository is a refusal, not a tool that failed"
    assert calls(recording) == []


def test_the_unregistered_repository_message_says_what_to_do_rather_than_naming_a_condition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: print the bare ``unregistered_repository`` token and stop.

    Phase 5 criterion 7's lesson, applied one job earlier. A token names the condition the
    code is in, which the submitter is not in and cannot act on. What they can act on is
    the repository they asked for, the ones that are registered, and the file that decides.

    The token stays as the first line: the workflow greps for it and a machine-readable
    first line is why the sentence below it can be written for a person.
    """
    install_aws_stub(tmp_path, monkeypatch)

    resolve(tmp_path, repository="dolma")

    reported = capsys.readouterr().err
    assert reported.splitlines()[0] == "unregistered_repository"
    assert "dolma" in reported
    assert "OLMo-core" in reported
    assert "config/repositories.yaml" in reported


def test_a_registry_that_cannot_be_read_stops_before_reaching_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = resolve(tmp_path, registry=tmp_path / "absent.yaml")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "registry_unreadable"
    assert calls(recording) == []


@pytest.mark.parametrize(
    "commit_sha",
    ["main", "8076c07", "8076C077533EB79742F4ED22AADE439DF123A593", "z" * 40],
    ids=["a branch", "a short sha", "upper case", "not hexadecimal"],
)
def test_a_commit_that_is_not_a_full_sha_is_refused_rather_than_sliced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    commit_sha: str,
) -> None:
    # The tag is twelve characters of whatever it is handed, so a branch name resolves to a
    # tag nobody published and the answer comes back as a commit with no image. The manifest
    # holds the same pattern, so anything this refuses would have been refused there anyway
    # -- one job later, having spent a role assumption on it.
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = resolve(tmp_path, commit_sha=commit_sha)

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "commit_sha_unusable"
    assert calls(recording) == []


@pytest.mark.slow
def test_a_runner_without_the_aws_cli_resolves_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    exit_code = resolve(tmp_path)

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "aws_cli_unavailable"
    assert not (tmp_path / "published-image.json").exists()


@pytest.mark.slow
def test_an_output_that_cannot_be_written_is_reported_without_naming_the_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_aws_stub(tmp_path, monkeypatch)
    unwritable = tmp_path / "a-directory"
    unwritable.mkdir()

    exit_code = resolve(tmp_path, output=unwritable)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines()[0] == "output_unwritable"
    assert str(unwritable) not in captured.err
