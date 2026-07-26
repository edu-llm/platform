"""Capturing what one publish run left behind: an image, a scan, a session, denials.

The role capture in ``test_capture_phase1_evidence_cli.py`` reads standing facts and
compares them to a committed template. These four targets read a *run*, and none of them
has a template to be compared against. What stands in for the comparison is the join:
each record has to be tied to the image the run produced, and a target that cannot make
the tie writes nothing rather than writing the nearest thing it found.

Three joins are exercised here, and each one is the reason its target exists:

* the scan is looked up by the image's digest, not by its tag, so a scan of some other
  image cannot be filed under this one;
* the session is found by the access key that pushed the image, so it is the session that
  did the push rather than the most recent session anybody held;
* each denial is joined to the CloudTrail event for the same operation under the same
  role session within a bounded window, so the two runs of the matrix in one afternoon
  cannot be confused for each other.

The stub answers carry a real twelve-digit account ID and a real-shaped ``ASIA`` key,
because what is most worth proving is that neither reaches a file or a stream.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.build_tooling import load_registry
from edullm_platform.evidence import redact_content_digests, scan_for_secrets
from edullm_platform.phase1_evidence import UNDECLARED_IDENTITY_PLACEHOLDER
from edullm_platform.publisher_denials import PUBLISHER_DENIED_ACTIONS
from tools.capture_phase1_evidence import (
    CAPTURE_TARGET_NAMES,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"
# Assembled rather than written out, so the tracked tree carries no key-shaped literal.
SESSION_KEY = "ASIA" + "Q2QWZFWTEXAMPLE1"
OTHER_SESSION_KEY = "ASIA" + "Q2QWZFWTEXAMPLE2"
#: The key an ECR authorization token is logged under, which STS never issued.
ECR_TOKEN_KEY = "ASIA" + "Q2QWZFWTEXAMPLE3"
PROFILE = "sandbox"
REPOSITORY = "OLMo-core"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
PUBLISHER_ROLE = "sbsandbox-intern-edullm-ecr-publisher"
SESSION_NAME = "GitHubActions"
OUTPUT_SUFFIX = Path("docs-frank/working/phase-1-evidence")

COMMIT_SHA = "a1b2c3d4e5f6708192a3b4c5d6e7f80912345678"
IMAGE_TAG = COMMIT_SHA[:12]
IMAGE_DIGEST = "sha256:" + "4e" * 32
#: Read from the shipped registry rather than restated. The base an image was built from
#: is not something this capture observes; it is what the platform registered, and a test
#: carrying its own copy would pass while the two disagreed.
BASE_IMAGE_DIGEST = (
    load_registry(PROJECT_ROOT / "config" / "repositories.yaml")
    .repository_by_name(REPOSITORY)
    .base_image_digest
)
PUSHED_AT = "2026-07-26T22:05:41.454000+00:00"

ASSUMED_AT = datetime(2026, 7, 26, 22, 5, 19, tzinfo=UTC)
EXPIRES_AT = ASSUMED_AT + timedelta(hours=1)
OIDC_SUBJECT = "repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/heads/edullm/platform"
SESSION_EVENT_ID = "d5e06ba1-30a8-4b3c-8143-de9bcd95071d"
REFUSAL_EVENT_ID = "3a5d0a15-3ad6-4e57-8f11-8ff8a2ad0f4b"
#: Not the publisher role. The second push was made from a laptop, and the record has
#: to be able to say so rather than implying an identity nobody observed.
OPERATOR_ROLE = "Intern-somebody-sbsandbox"

DENIAL_ATTEMPTED_AT = datetime(2026, 7, 26, 22, 4, 58, tzinfo=UTC)
#: One CloudTrail event id per matrix action, in matrix order.
DENIAL_EVENT_IDS = (
    "21bd62b9-4797-494c-9255-b9c8dc84647e",
    "8fded737-d906-44ad-a74f-e3b4ead75f06",
    "e4ed1062-27d4-456b-ae91-91e18816be72",
    "34af3061-53d8-418b-9700-407457d31d2a",
    "386779c1-c28d-4abd-960e-f28547020cfd",
)
#: The operation CloudTrail logs for each action, in matrix order. Not always the
#: action's own word, which is why the matrix records both.
DENIAL_OPERATIONS = (
    ("SubmitJob", "batch.amazonaws.com", "edullm-denial-probe-absent-queue"),
    ("ListBuckets", "s3.amazonaws.com", None),
    ("CreateRole", "iam.amazonaws.com", PUBLISHER_ROLE),
    ("UpdateComputeEnvironment", "batch.amazonaws.com", "edullm-denial-probe-absent-ce"),
    ("DeleteRepository", "ecr.amazonaws.com", f"{ECR_REPOSITORY}-denial-probe-absent"),
)


def denial_matrix() -> dict[str, Any]:
    """The record the publish workflow's deny job writes, as it writes it."""
    return {
        "schema_version": 1,
        "attempts": [
            {
                "region": REGION,
                "role_name": PUBLISHER_ROLE,
                "session_name": SESSION_NAME,
                "attempted_action": action,
                "attempted_resource": resource,
                "attempted_at": (DENIAL_ATTEMPTED_AT + timedelta(seconds=index)).isoformat(),
                "outcome": "denied",
                "error_code": "AccessDenied",
                "error_message": (
                    f"User: arn:aws:sts::<aws-account-id>:assumed-role/{PUBLISHER_ROLE}"
                    f"/{SESSION_NAME} is not authorized to perform: {action}"
                ),
                "event_name": operation,
                "event_source": source,
            }
            for index, (action, (operation, source, resource)) in enumerate(
                zip(PUBLISHER_DENIED_ACTIONS, DENIAL_OPERATIONS, strict=True)
            )
        ],
    }


def cloudtrail_event(
    *,
    event_id: str,
    event_name: str,
    event_source: str,
    event_time: datetime,
    error_code: str | None = None,
    access_key: str = SESSION_KEY,
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    role: str = PUBLISHER_ROLE,
    session_created_at: datetime | None = None,
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "arn": f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{role}/{SESSION_NAME}",
        "accessKeyId": access_key,
    }
    if session_created_at is not None:
        identity["sessionContext"] = {
            "attributes": {
                "creationDate": session_created_at.isoformat().replace("+00:00", "Z"),
                "mfaAuthenticated": "false",
            }
        }
    record: dict[str, Any] = {
        "eventID": event_id,
        "eventTime": event_time.isoformat().replace("+00:00", "Z"),
        "eventName": event_name,
        "eventSource": event_source,
        "awsRegion": REGION,
        "userIdentity": identity,
        "requestParameters": request,
        "responseElements": response,
    }
    if error_code is not None:
        record["errorCode"] = error_code
    return record


def lookup_answer(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "Events": [
            {"EventId": record["eventID"], "CloudTrailEvent": json.dumps(record)}
            for record in records
        ]
    }


def session_event(
    *,
    event_id: str = SESSION_EVENT_ID,
    access_key: str = SESSION_KEY,
    assumed_at: datetime = ASSUMED_AT,
) -> dict[str, Any]:
    return cloudtrail_event(
        event_id=event_id,
        event_name="AssumeRoleWithWebIdentity",
        event_source="sts.amazonaws.com",
        event_time=assumed_at,
        access_key=access_key,
        request={"roleSessionName": SESSION_NAME, "durationSeconds": 3600},
        response={
            "credentials": {"accessKeyId": access_key, "expiration": EXPIRES_AT.isoformat()},
            "subjectFromWebIdentityToken": OIDC_SUBJECT,
            "assumedRoleUser": {
                "arn": (f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{PUBLISHER_ROLE}/{SESSION_NAME}")
            },
            "provider": (
                f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
            ),
            "audience": "sts.amazonaws.com",
        },
    )


def run_answers() -> dict[str, Any]:
    """Every call the four run targets make, and what the account answers."""
    answers: dict[str, Any] = {
        "sts get-caller-identity": {
            "Account": ACCOUNT_ID,
            "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/somebody",
        },
        f"ecr describe-images imageTag={IMAGE_TAG}": {
            "imageDetails": [
                {
                    "registryId": ACCOUNT_ID,
                    "repositoryName": ECR_REPOSITORY,
                    "imageDigest": IMAGE_DIGEST,
                    "imageTags": [IMAGE_TAG],
                    "imagePushedAt": PUSHED_AT,
                }
            ]
        },
        f"ecr describe-image-scan-findings imageDigest={IMAGE_DIGEST}": {
            "registryId": ACCOUNT_ID,
            "repositoryName": ECR_REPOSITORY,
            "imageId": {"imageDigest": IMAGE_DIGEST},
            "imageScanStatus": {
                "status": "COMPLETE",
                "description": "The scan was completed successfully.",
            },
            "imageScanFindings": {
                "imageScanCompletedAt": "2026-07-26T22:05:49+00:00",
                "findingSeverityCounts": {"CRITICAL": 4, "HIGH": 8, "MEDIUM": 4, "LOW": 1},
            },
        },
        "cloudtrail lookup-events PutImage": lookup_answer(
            [
                cloudtrail_event(
                    event_id="7248f477-0349-4997-aae0-21d20009e3ff",
                    event_name="PutImage",
                    event_source="ecr.amazonaws.com",
                    event_time=datetime(2026, 7, 26, 22, 5, 41, tzinfo=UTC),
                    # The key a docker push is logged under is the ECR authorization
                    # token's, which STS never issued to anybody. The session that made
                    # the push survives the exchange only in sessionContext.
                    access_key=ECR_TOKEN_KEY,
                    session_created_at=ASSUMED_AT,
                    request={"repositoryName": ECR_REPOSITORY, "imageTag": IMAGE_TAG},
                )
            ]
        ),
        "cloudtrail lookup-events AssumeRoleWithWebIdentity": lookup_answer(
            [
                session_event(),
                # The deny job's session: the same role and the same session name,
                # twenty-five seconds earlier, and its window contains the push too.
                session_event(
                    event_id="a2e988e9-0670-4321-83a4-e1d7dec6fd9e",
                    access_key=OTHER_SESSION_KEY,
                    assumed_at=ASSUMED_AT - timedelta(seconds=25),
                ),
            ]
        ),
    }
    for event_id, (operation, source, _resource), offset in zip(
        DENIAL_EVENT_IDS, DENIAL_OPERATIONS, range(len(DENIAL_EVENT_IDS)), strict=True
    ):
        answers[f"cloudtrail lookup-events {operation}"] = lookup_answer(
            [
                # The same probe from an hour-earlier run of the matrix, which the join
                # has to leave alone.
                cloudtrail_event(
                    event_id="00000000-0000-4000-8000-000000000000",
                    event_name=operation,
                    event_source=source,
                    event_time=DENIAL_ATTEMPTED_AT + timedelta(seconds=offset, hours=-1),
                    error_code="AccessDenied",
                ),
                cloudtrail_event(
                    event_id=event_id,
                    event_name=operation,
                    event_source=source,
                    event_time=DENIAL_ATTEMPTED_AT + timedelta(seconds=offset),
                    error_code="AccessDenied",
                ),
            ]
        )
    return answers


def answers_with_a_refused_push() -> dict[str, Any]:
    """The trail after somebody pushed a different image under a tag that already exists."""
    answers = copy.deepcopy(run_answers())
    events = answers["cloudtrail lookup-events PutImage"]["Events"]
    refusal = cloudtrail_event(
        event_id=REFUSAL_EVENT_ID,
        event_name="PutImage",
        event_source="ecr.amazonaws.com",
        event_time=datetime(2026, 7, 26, 23, 3, 38, tzinfo=UTC),
        error_code="ImageTagAlreadyExistsException",
        request={"repositoryName": ECR_REPOSITORY, "imageTag": IMAGE_TAG},
        role=OPERATOR_ROLE,
    )
    refusal["errorMessage"] = (
        f"The image tag '{IMAGE_TAG}' already exists in the '{ECR_REPOSITORY}' repository "
        f"in the registry with id '{ACCOUNT_ID}' and cannot be overwritten because the "
        "tag is immutable."
    )
    answers["cloudtrail lookup-events PutImage"] = {
        "Events": [
            {"EventId": REFUSAL_EVENT_ID, "CloudTrailEvent": json.dumps(refusal)},
            *events,
        ]
    }
    return answers


def install_aws_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: dict[str, Any] | None = None,
) -> Path:
    responses = run_answers() if answers is None else answers
    recording = tmp_path / "aws-calls.txt"
    branches = []
    for key in sorted(responses):
        body = f"cat <<'RESPONSE'\n{json.dumps(responses[key])}\nRESPONSE"
        branches.append(f'  "{key}")\n{body}\n    ;;')
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
        'key="${1-} ${2-}"\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    --image-ids|--image-id) key="$key $2"; shift ;;\n'
        '    --lookup-attributes) key="$key ${2##*AttributeValue=}"; shift ;;\n'
        '    --next-token) key="$key page2"; shift ;;\n'
        "  esac\n"
        "  shift\n"
        "done\n"
        'case "$key" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def output_dir(tmp_path: Path) -> Path:
    return tmp_path / OUTPUT_SUFFIX / "run"


def denials_file(tmp_path: Path, matrix: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "publisher-denials.json"
    path.write_text(json.dumps(matrix or denial_matrix()), encoding="utf-8")
    return path


def capture(tmp_path: Path, *targets: str, **overrides: str) -> int:
    arguments = [
        "--aws-profile",
        PROFILE,
        "--aws-region",
        REGION,
        "--environment",
        "sandbox",
        "--repository",
        REPOSITORY,
        "--output-dir",
        str(output_dir(tmp_path)),
        "--commit-sha",
        COMMIT_SHA,
    ]
    for target in targets:
        arguments += ["--target", target]
    for name, value in overrides.items():
        arguments += [name, value]
    return main(arguments, base_dir=tmp_path)


def written(tmp_path: Path) -> dict[str, str]:
    root = output_dir(tmp_path)
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def loaded(tmp_path: Path, relative: str) -> Any:
    return json.loads((output_dir(tmp_path) / relative).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# What each target writes
# --------------------------------------------------------------------------------------


def test_every_run_target_is_offered_by_the_command() -> None:
    # The registry is the extension point, and these four are what one completed publish
    # run leaves behind that nothing else in this repository can read.
    assert CAPTURE_TARGET_NAMES == (
        "roles",
        "repository",
        "image",
        "scan",
        "session",
        "tag-refusal",
        "denials",
    )


def test_the_image_target_records_the_digest_the_registry_holds_for_this_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "image") == 0

    image = loaded(tmp_path, "sanitized/ecr-image.sanitized.json")
    assert image["image_digest"] == IMAGE_DIGEST
    assert image["image_tag"] == IMAGE_TAG
    assert image["source_commit_sha"] == COMMIT_SHA
    assert image["base_image_digest"] == BASE_IMAGE_DIGEST
    assert image["repository_name"] == ECR_REPOSITORY
    assert image["region"] == REGION
    assert "registry_id" not in image


def test_the_scan_target_records_every_severity_including_the_ones_ecr_omits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ECR leaves a severity out of findingSeverityCounts when its count is zero, so a
    # record that copied the answer would not say whether the scan found none or the
    # capture dropped it.
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "scan") == 0

    scan = loaded(tmp_path, "sanitized/image-scan.sanitized.json")
    assert scan["scan_status"] == "COMPLETE"
    assert scan["image_digest"] == IMAGE_DIGEST
    assert scan["finding_counts"] == {
        "critical": 4,
        "high": 8,
        "medium": 4,
        "low": 1,
        "informational": 0,
        "undefined": 0,
    }


def test_the_session_target_records_the_session_that_pushed_the_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two publisher sessions exist in the window and only one of them pushed. Picking the
    # latest would have recorded the other.
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "session") == 0

    session = loaded(tmp_path, "sanitized/publisher-session.sanitized.json")
    assert session["event_id"] == SESSION_EVENT_ID
    assert session["role_name"] == PUBLISHER_ROLE
    assert session["session_name"] == SESSION_NAME
    assert session["oidc_issuer"] == "token.actions.githubusercontent.com"
    assert session["oidc_audience"] == "sts.amazonaws.com"
    assert session["oidc_subject"] == OIDC_SUBJECT
    assert session["assumed_at"].startswith("2026-07-26T22:05:19")
    assert session["expires_at"].startswith("2026-07-26T23:05:19")


def test_the_denials_target_completes_every_attempt_with_its_cloudtrail_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "denials", **{"--denials": str(denials_file(tmp_path))}) == 0

    for action, event_id in zip(PUBLISHER_DENIED_ACTIONS, DENIAL_EVENT_IDS, strict=True):
        record = loaded(tmp_path, f"sanitized/denials/{action.replace(':', '-')}.sanitized.json")
        assert record["attempted_action"] == action
        assert record["outcome"] == "denied"
        assert record["event_id"] == event_id


def test_a_denial_is_joined_to_its_own_run_rather_than_to_an_earlier_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The matrix ran twice in one afternoon against the same role and the same probes.
    # Every answer the stub gives carries both, so a join by action alone picks the wrong
    # one half the time and a join by action and role picks it every time.
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "denials", **{"--denials": str(denials_file(tmp_path))}) == 0

    recorded = {
        loaded(tmp_path, f"sanitized/denials/{action.replace(':', '-')}.sanitized.json")["event_id"]
        for action in PUBLISHER_DENIED_ACTIONS
    }
    assert "00000000-0000-4000-8000-000000000000" not in recorded


def test_the_tag_refusal_target_records_the_refusal_and_the_digest_that_survived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A refusal on its own does not say the original image is still there, so the digest
    # is read back from the registry after the attempt and recorded beside it.
    install_aws_stub(tmp_path, monkeypatch, answers=answers_with_a_refused_push())

    assert capture(tmp_path, "tag-refusal") == 0

    refusal = loaded(tmp_path, "sanitized/immutable-tag-refusal.sanitized.json")
    assert refusal["outcome"] == "refused"
    assert refusal["error_code"] == "ImageTagAlreadyExistsException"
    assert refusal["image_tag"] == IMAGE_TAG
    assert refusal["image_digest"] == IMAGE_DIGEST
    assert refusal["event_id"] == REFUSAL_EVENT_ID
    assert refusal["attempted_by"] == UNDECLARED_IDENTITY_PLACEHOLDER
    assert refusal["attempted_by_publisher_role"] is False


def test_a_refusal_met_by_somebody_other_than_the_publisher_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tag immutability belongs to the repository, not to the caller, so a refusal is a
    # refusal whoever met it. Two things the record may not do: leave a reader to assume
    # the publisher role met it when somebody at a laptop did, and publish the personal
    # role name of whoever that somebody was.
    install_aws_stub(tmp_path, monkeypatch, answers=answers_with_a_refused_push())

    assert capture(tmp_path, "tag-refusal") == 0

    text = written(tmp_path)["sanitized/immutable-tag-refusal.sanitized.json"]
    refusal = json.loads(text)
    assert refusal["attempted_by_publisher_role"] is False
    assert refusal["attempted_by"] == UNDECLARED_IDENTITY_PLACEHOLDER
    assert OPERATOR_ROLE not in text


def test_a_repository_with_no_refused_push_captures_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The default answers hold one successful push and no refusal, which is the state
    # every repository is in until somebody deliberately tries the second push.
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, "tag-refusal")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "tag_refusal_event_not_found"
    assert written(tmp_path) == {}


def test_a_push_on_a_later_page_of_the_trail_is_still_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CloudTrail answers fifty events at a time, newest first, and this account is
    # shared: another team pushes images all day, so the page holding one publish run is
    # not page one. A reader that stopped there would report the push as absent, which
    # is the same answer it gives when the run never happened.
    answers = copy.deepcopy(run_answers())
    ours = answers["cloudtrail lookup-events PutImage"]
    answers["cloudtrail lookup-events PutImage"] = {
        "Events": [
            {
                "EventId": "11111111-1111-4111-8111-111111111111",
                "CloudTrailEvent": json.dumps(
                    cloudtrail_event(
                        event_id="11111111-1111-4111-8111-111111111111",
                        event_name="PutImage",
                        event_source="ecr.amazonaws.com",
                        event_time=datetime(2026, 7, 26, 22, 12, 41, tzinfo=UTC),
                        request={"repositoryName": "someone-elses-repo", "imageTag": "latest"},
                        role="someone-elses-codebuild",
                    )
                ),
            }
        ],
        "NextToken": "a-second-page-exists",
    }
    answers["cloudtrail lookup-events PutImage page2"] = ours
    install_aws_stub(tmp_path, monkeypatch, answers=answers)

    assert capture(tmp_path, "session") == 0

    assert loaded(tmp_path, "sanitized/publisher-session.sanitized.json")["event_id"] == (
        SESSION_EVENT_ID
    )


# --------------------------------------------------------------------------------------
# A target that cannot make its join writes nothing
# --------------------------------------------------------------------------------------


def test_a_scan_of_another_image_is_refused_rather_than_filed_under_this_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = copy.deepcopy(run_answers())
    answers[f"ecr describe-image-scan-findings imageDigest={IMAGE_DIGEST}"]["imageId"] = {
        "imageDigest": "sha256:" + "99" * 32
    }
    install_aws_stub(tmp_path, monkeypatch, answers=answers)

    exit_code = capture(tmp_path, "scan")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "scan_describes_another_image"
    assert written(tmp_path) == {}


def test_a_session_that_cannot_be_tied_to_the_push_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Guessing here would put a session in the record that nothing established, and the
    # record's whole purpose is to say which session held the credentials that pushed.
    answers = copy.deepcopy(run_answers())
    answers["cloudtrail lookup-events AssumeRoleWithWebIdentity"] = lookup_answer(
        [session_event(assumed_at=ASSUMED_AT - timedelta(seconds=25))]
    )
    install_aws_stub(tmp_path, monkeypatch, answers=answers)

    exit_code = capture(tmp_path, "session")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "session_for_the_push_not_found"
    assert written(tmp_path) == {}


def test_a_denial_with_no_matching_event_stops_the_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = copy.deepcopy(run_answers())
    answers["cloudtrail lookup-events ListBuckets"] = lookup_answer([])
    install_aws_stub(tmp_path, monkeypatch, answers=answers)

    exit_code = capture(tmp_path, "denials", **{"--denials": str(denials_file(tmp_path))})

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == ("denial_event_not_found:s3:ListAllMyBuckets")
    assert written(tmp_path) == {}


def test_an_image_target_without_a_commit_sha_is_refused_before_any_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)
    arguments = [
        "--aws-profile",
        PROFILE,
        "--aws-region",
        REGION,
        "--environment",
        "sandbox",
        "--repository",
        REPOSITORY,
        "--output-dir",
        str(output_dir(tmp_path)),
        "--target",
        "image",
    ]

    exit_code = main(arguments, base_dir=tmp_path)

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "commit_sha_required_for:image"
    assert not recording.exists()


def test_a_denials_target_without_a_matrix_file_is_refused_before_any_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(tmp_path, "denials")

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "denials_matrix_required_for:denials"
    assert not recording.exists()


def test_a_matrix_missing_an_action_is_refused_by_the_contract_that_holds_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A run that refused four of five actions proved the criterion for four of them, and
    # a file able to hold the four would later read as though it had proved all five.
    install_aws_stub(tmp_path, monkeypatch)
    partial = denial_matrix()
    del partial["attempts"][1]

    exit_code = capture(tmp_path, "denials", **{"--denials": str(denials_file(tmp_path, partial))})

    assert exit_code == 2
    assert capsys.readouterr().err.splitlines()[0] == "denials_matrix_unreadable"
    assert written(tmp_path) == {}


# --------------------------------------------------------------------------------------
# Nothing the account said about itself survives into a file
# --------------------------------------------------------------------------------------


def test_no_account_id_or_session_key_reaches_a_file_or_a_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The session lookup joins on the ASIA key the push was made with, which is the one
    # value in this capture that is a live credential's identifier.
    install_aws_stub(tmp_path, monkeypatch)

    exit_code = capture(
        tmp_path,
        "image",
        "scan",
        "session",
        "denials",
        **{"--denials": str(denials_file(tmp_path))},
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    for name, text in written(tmp_path).items():
        assert ACCOUNT_ID not in text, name
        assert SESSION_KEY not in text, name
        # Content digests are masked before the scan and only content digests are. A
        # 64-character sha256 digest and a 40-character commit SHA are what these
        # records exist to carry, and both match the generic long-credential patterns,
        # so scanning them unmasked would refuse every valid image record. The contracts
        # constrain those two fields by exact pattern instead, which is the stricter
        # check; this asserts that nothing else in the bytes needs an exemption.
        assert scan_for_secrets(redact_content_digests(text)) == redact_content_digests(text), name
    assert ACCOUNT_ID not in captured.out + captured.err
    assert SESSION_KEY not in captured.out + captured.err


def test_the_run_targets_ask_for_exactly_what_they_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = install_aws_stub(tmp_path, monkeypatch)

    assert (
        capture(
            tmp_path,
            "image",
            "scan",
            "session",
            "denials",
            **{"--denials": str(denials_file(tmp_path))},
        )
        == 0
    )

    calls = recording.read_text(encoding="utf-8").splitlines()
    operations = [" ".join(call.split()[:2]) for call in calls]
    assert operations.count("sts get-caller-identity") == 1
    assert operations.count("ecr describe-image-scan-findings") == 1
    assert operations.count("cloudtrail lookup-events") == 7
    assert all(f"--profile {PROFILE}" in call for call in calls)
    # Every CloudTrail read is a lookup. Nothing here creates, deletes or updates a trail.
    assert [
        call
        for call in calls
        if call.startswith("cloudtrail ") and " lookup-events " not in f" {call} "
    ] == []
