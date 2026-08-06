from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest
from workflow_support import write_stub

from edullm_platform.build_cache import ANCESTOR_LIMIT, BATCH_GET_IMAGE_LIMIT
from tools.resolve_build_cache_source import main, resolve_cache_source

REGISTRY = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
REGION = "us-east-1"
NEAR = "aaaaaaaaaaaa"
MIDDLE = "bbbbbbbbbbbb"
FAR = "cccccccccccc"
# What an ECR error puts in its text, and the reason it is never echoed.
ACCOUNT_NAMING_ERROR = (
    "An error occurred (ImageNotFoundException) when calling the DescribeImages operation: "
    "The image with imageId {imageTag:aaaaaaaaaaaa} does not exist within the repository "
    "with name 'sbsandbox-intern-edullm-olmo-core' in the registry with id '123456789012'"
)

# HOW THE REAL SERVICE ANSWERS A LIST WITH A HOLE IN IT, WHICH IS THE WHOLE SUBJECT HERE.
# Verified against sbsandbox-intern-edullm-olmo-core on 2026-08-06 with OLMo-core's own
# twenty-five candidates, fourteen of them published:
#
#   describe-images --image-ids <25>   exit 254, no stdout, ImageNotFoundException
#   batch-get-image --image-ids <25>   exit 0, images[] holds 5, failures[] holds 20
#
# The two disagree only on a partial list, so a stub that answers both the same way cannot
# tell the working implementation from the broken one. This one reproduces the difference,
# which is what makes reverting to the single describe-images call a lethal mutation.
FAKE_ECR = '''
import json
import sys

published = set(json.loads(sys.argv[1]))
calls_file = sys.argv[2]
argv = sys.argv[3:]

with open(calls_file, "a", encoding="utf-8") as handle:
    handle.write(" ".join(argv) + "\\n")

operation = argv[1]
requested = [value.split("=", 1)[1] for value in argv if value.startswith("imageTag=")]
found = [tag for tag in requested if tag in published]

if operation == "describe-images":
    # All or nothing, and this is the defect. One absent id fails the whole call.
    if len(found) != len(requested):
        sys.stderr.write(
            "An error occurred (ImageNotFoundException) when calling the DescribeImages "
            "operation: the registry with id '123456789012' does not hold every image id"
        )
        sys.exit(254)
    sys.stdout.write(json.dumps(found))
    sys.exit(0)

if operation == "batch-get-image":
    # Partial results. The absent ids come back under failures and the call exits zero.
    sys.stdout.write(json.dumps(found))
    sys.exit(0)

sys.stderr.write("unstubbed operation: " + operation)
sys.exit(255)
'''


def fake_ecr(tmp_path: Path, published: tuple[str, ...]) -> Path:
    """Install an ``aws`` that distinguishes the two operations the way ECR does."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "fake_ecr.py"
    script.write_text(FAKE_ECR, encoding="utf-8")
    write_stub(
        stub_bin,
        "aws",
        f"exec python3 {script} {shlex.quote(json.dumps(list(published)))} "
        f'"{tmp_path}/aws-calls.txt" "$@"\n',
    )
    return stub_bin


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


def test_a_candidate_list_with_unpublished_entries_still_names_the_published_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REGRESSION, AND THE ONE CASE THE ORIGINAL SUITE NEVER EXERCISED.

    Every test above this one hands the registry a candidate list it holds in full, which
    is precisely the case that already worked. A real list has holes in it: a repository
    builds the branches somebody pushed, so a commit nobody pushed a branch for has no
    image, and eleven of OLMo-core's twenty-five had none on 2026-08-06. The resolver
    answered ``no_published_ancestor`` for four months of builds while ``fc2c4745e377``
    sat published in the registry, and no test failed, because none of them asked.

    The mutation this is written against is reverting ``batch_get_image_tags`` to the
    single ``describe-images`` call it replaced. Confirmed lethal: under that spelling the
    stub fails the whole call and this test reads ``no_published_ancestor``.
    """
    with_stub(monkeypatch, fake_ecr(tmp_path, published=(MIDDLE,)))

    reference, reason = resolve((NEAR, MIDDLE, FAR))

    assert reason is None
    assert reference == f"{REGISTRY}/{ECR_REPOSITORY}:{MIDDLE}"


def test_a_hole_between_two_published_ancestors_does_not_hide_the_nearer_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The nearest published ancestor is the one whose pyproject.toml is likeliest to match,
    # so returning merely *a* published ancestor is not the same as returning the right
    # one. NEAR is absent, MIDDLE and FAR are both published, MIDDLE must win.
    with_stub(monkeypatch, fake_ecr(tmp_path, published=(MIDDLE, FAR)))

    reference, reason = resolve((NEAR, MIDDLE, FAR))

    assert reason is None
    assert reference == f"{REGISTRY}/{ECR_REPOSITORY}:{MIDDLE}"


def test_a_candidate_list_the_registry_holds_none_of_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first build of a repository reaches this, and BatchGetImage answers it with an
    # empty images list and a zero exit rather than by failing.
    with_stub(monkeypatch, fake_ecr(tmp_path, published=()))

    reference, reason = resolve((NEAR, MIDDLE, FAR))

    assert reference == ""
    assert reason is not None and reason.value == "no_published_ancestor"


def test_more_candidates_than_one_call_takes_are_batched_rather_than_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # THE CEILING, MADE STRUCTURAL. BatchGetImage refuses more than a hundred image ids,
    # and a refusal reads exactly like an unreachable registry: no cache, a printed reason,
    # a green build. Raising ANCESTOR_LIMIT must cost another round trip and never a silent
    # stop, so the only published ancestor here sits past the first batch boundary.
    candidates = tuple(f"{index:012x}" for index in range(BATCH_GET_IMAGE_LIMIT + 30))
    published = candidates[BATCH_GET_IMAGE_LIMIT + 10]
    with_stub(monkeypatch, fake_ecr(tmp_path, published=(published,)))

    reference, reason = resolve(candidates)

    assert reason is None
    assert reference == f"{REGISTRY}/{ECR_REPOSITORY}:{published}"
    calls = (tmp_path / "aws-calls.txt").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    assert all(call.count("imageTag=") <= BATCH_GET_IMAGE_LIMIT for call in calls)


def test_todays_ancestor_limit_still_costs_exactly_one_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The publish job budgeted one call between the ECR login and the build. Batching is
    # there for a limit nobody has raised yet, and must not have cost a call today.
    candidates = tuple(f"{index:012x}" for index in range(ANCESTOR_LIMIT))
    with_stub(monkeypatch, fake_ecr(tmp_path, published=(candidates[-1],)))

    resolve(candidates)

    assert len((tmp_path / "aws-calls.txt").read_text(encoding="utf-8").splitlines()) == 1


def test_every_candidate_goes_in_one_call_rather_than_one_call_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reaching the registry costs the build time this exists to save, and a loop would
    # spend twenty-five round trips to learn what one call can say. One call per candidate
    # is the other way to tolerate an absent id, and it is rejected on this cost.
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
    # An empty images list, which is what BatchGetImage returns when it holds none of them.
    # This test used to stub a failed call and assert the same answer, which is how the
    # all-or-nothing behaviour of DescribeImages got written down as correct.
    with_stub(monkeypatch, stub_aws(tmp_path, stdout="[]"))

    reference, reason = resolve((NEAR,))

    assert reference == ""
    assert reason is not None and reason.value == "no_published_ancestor"


def test_a_registry_that_will_not_answer_is_a_slow_build_and_not_a_failed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every non-zero exit is this now. An absent image is not one of them, because
    # BatchGetImage reports it under failures and still exits zero, so a call that did
    # fail is a registry that would not answer rather than a history with a hole in it.
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
    # An ECR error names the registry id, and the runner log is world readable for any
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
    assert "registry_unreadable" in captured.err
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
