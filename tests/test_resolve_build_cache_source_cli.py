from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from workflow_support import write_stub

from tools.resolve_build_cache_source import main, resolve_cache_source

REGISTRY = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
REGION = "us-east-1"
NEAR = "aaaaaaaaaaaa"
MIDDLE = "bbbbbbbbbbbb"
FAR = "cccccccccccc"
# What DescribeImages puts in its error text, and the reason it is never echoed.
ACCOUNT_NAMING_ERROR = (
    "An error occurred (ImageNotFoundException) when calling the DescribeImages operation: "
    "The image with imageId {imageTag:aaaaaaaaaaaa} does not exist within the repository "
    "with name 'sbsandbox-intern-edullm-olmo-core' in the registry with id '123456789012'"
)


def stub_aws(tmp_path: Path, *, stdout: str = "[]", stderr: str = "", status: int = 0) -> Path:
    stub_bin = tmp_path / "bin"
    body = ""
    if stdout:
        body += f"printf '%s' {json.dumps(stdout)}\n"
    if stderr:
        body += f"printf '%s' {json.dumps(stderr)} >&2\n"
    body += f'printf "%s\\n" "$*" >> "{tmp_path}/aws-calls.txt"\nexit {status}\n'
    write_stub(stub_bin, "aws", body)
    return stub_bin


def with_stub(monkeypatch: pytest.MonkeyPatch, stub_bin: Path) -> None:
    """Put the stub ahead of the real ``aws``, keeping the default path behind it.

    The stub is a script with a ``/usr/bin/env bash`` shebang, so a path holding only the
    stub directory leaves ``env`` unable to find ``bash`` and every call fails as though
    the registry were unreachable -- which is a state this module has a real answer for,
    so the test would pass for the wrong reason.
    """
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")


def resolve(candidates: tuple[str, ...]) -> tuple[str, object]:
    return resolve_cache_source(
        candidates, registry=REGISTRY, ecr_repository=ECR_REPOSITORY, region=REGION
    )


def test_the_nearest_published_ancestor_becomes_the_cache_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_stub(monkeypatch, stub_aws(tmp_path, stdout=json.dumps([FAR, MIDDLE])))

    reference, reason = resolve((NEAR, MIDDLE, FAR))

    assert reason is None
    assert reference == f"{REGISTRY}/{ECR_REPOSITORY}:{MIDDLE}"


def test_every_candidate_goes_in_one_call_rather_than_one_call_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reaching the registry costs the build time this exists to save, and a loop would
    # spend twenty-five round trips to learn what one call can say.
    with_stub(monkeypatch, stub_aws(tmp_path, stdout=json.dumps([FAR])))

    resolve((NEAR, MIDDLE, FAR))

    calls = (tmp_path / "aws-calls.txt").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert f"imageTag={NEAR}" in calls[0]
    assert f"imageTag={MIDDLE}" in calls[0]
    assert f"imageTag={FAR}" in calls[0]


def test_no_candidates_never_reaches_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_stub(monkeypatch, stub_aws(tmp_path))

    reference, reason = resolve(())

    assert reference == ""
    assert reason is not None and reason.value == "no_ancestor_commits"
    assert not (tmp_path / "aws-calls.txt").exists()


def test_an_unpublished_history_is_an_ordinary_answer_rather_than_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When no candidate exists ECR fails the whole call rather than returning an empty
    # list, and the first build of a repository reaches that path.
    with_stub(
        monkeypatch,
        stub_aws(tmp_path, stdout="", stderr=ACCOUNT_NAMING_ERROR, status=254),
    )

    reference, reason = resolve((NEAR,))

    assert reference == ""
    assert reason is not None and reason.value == "no_published_ancestor"


def test_a_registry_that_will_not_answer_is_a_slow_build_and_not_a_failed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_stub(monkeypatch, stub_aws(tmp_path, stdout="", stderr="AccessDenied", status=254))

    reference, reason = resolve((NEAR,))

    assert reference == ""
    assert reason is not None and reason.value == "registry_unreadable"


def test_an_answer_that_is_not_a_list_of_tags_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for payload in ("not json", '{"imageTags": []}', "[1, 2]"):
        with_stub(monkeypatch, stub_aws(tmp_path, stdout=payload))
        reference, reason = resolve((NEAR,))
        assert reference == ""
        assert reason is not None and reason.value == "registry_unreadable"


def test_a_published_tag_that_is_not_a_candidate_is_never_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # THE SECURITY PROPERTY. The registry holds an image for every branch of this
    # repository, and only the ones this tree descends from may hand it a layer.
    with_stub(monkeypatch, stub_aws(tmp_path, stdout=json.dumps(["ffffffffffff"])))

    reference, reason = resolve((NEAR,))

    assert reference == ""
    assert reason is not None and reason.value == "no_published_ancestor"


def test_the_registry_error_text_never_reaches_either_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # DescribeImages names the registry id, and the runner log is world readable for any
    # public caller repository.
    with_stub(
        monkeypatch,
        stub_aws(tmp_path, stdout="", stderr=ACCOUNT_NAMING_ERROR, status=254),
    )
    output = tmp_path / "step-output.txt"

    exit_code = main(
        [
            "--candidates",
            NEAR,
            "--registry",
            REGISTRY,
            "--ecr-repository",
            ECR_REPOSITORY,
            "--region",
            REGION,
            "--github-output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "123456789012" not in captured.out + captured.err
    assert "no_published_ancestor" in captured.err
    assert output.read_text(encoding="utf-8") == "cache_from=\n"


def test_the_chosen_image_is_named_by_its_tag_and_never_by_its_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The reference carries the registry host, which carries the account id, and the line
    # that reports a cache hit is world readable.
    with_stub(monkeypatch, stub_aws(tmp_path, stdout=json.dumps([NEAR])))
    output = tmp_path / "step-output.txt"

    exit_code = main(
        [
            "--candidates",
            f"{NEAR} {MIDDLE}",
            "--registry",
            REGISTRY,
            "--ecr-repository",
            ECR_REPOSITORY,
            "--region",
            REGION,
            "--github-output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert NEAR in captured.out
    assert "123456789012" not in captured.out
    assert output.read_text(encoding="utf-8") == (
        f"cache_from={REGISTRY}/{ECR_REPOSITORY}:{NEAR}\n"
    )


def test_step_outputs_are_appended_rather_than_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_stub(monkeypatch, stub_aws(tmp_path, stdout=json.dumps([NEAR])))
    output = tmp_path / "step-output.txt"
    output.write_text("previous=kept\n", encoding="utf-8")

    main(
        [
            "--candidates",
            NEAR,
            "--registry",
            REGISTRY,
            "--ecr-repository",
            ECR_REPOSITORY,
            "--region",
            REGION,
            "--github-output",
            str(output),
        ]
    )

    assert output.read_text(encoding="utf-8").startswith("previous=kept\n")
