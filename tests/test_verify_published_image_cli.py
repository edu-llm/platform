from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.verify_published_image import (
    BASE_NAME_LABEL,
    REVISION_LABEL,
    PublishedImageError,
    main,
    require_matching_labels,
)

BASE_REFERENCE = "public.ecr.aws/example/base@sha256:" + "e" * 64
COMMIT_SHA = "f" * 40


def image_config(**labels: str) -> dict[str, Any]:
    declared = {BASE_NAME_LABEL: BASE_REFERENCE, REVISION_LABEL: COMMIT_SHA}
    declared.update(labels)
    return {
        "created": "2026-07-26T11:00:00.000000000Z",
        "architecture": "amd64",
        "os": "linux",
        "config": {"Env": ["PATH=/usr/bin"], "Labels": declared},
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "d" * 64]},
    }


def argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--image-config": str(tmp_path / "config.json"),
        "--base-reference": BASE_REFERENCE,
        "--commit-sha": COMMIT_SHA,
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_an_image_built_from_this_base_at_this_commit_is_accepted() -> None:
    require_matching_labels(
        image_config(),
        base_reference=BASE_REFERENCE,
        commit_sha=COMMIT_SHA,
    )


def test_labels_this_run_does_not_assert_are_allowed_to_differ() -> None:
    # edullm.workflow.run.url is why a retry cannot reuse the manifest in the first place,
    # so a resumed run must not require the published image to carry its own run URL.
    require_matching_labels(
        image_config(**{"edullm.workflow.run.url": "https://example.invalid/runs/1"}),
        base_reference=BASE_REFERENCE,
        commit_sha=COMMIT_SHA,
    )


def test_an_image_built_from_a_base_the_registry_no_longer_names_is_rejected() -> None:
    # This is the finding: base_image_digest is re-read from config/repositories.yaml at
    # provenance time, so a resumed older commit would otherwise be recorded against a
    # base image it was never built from.
    with pytest.raises(PublishedImageError) as raised:
        require_matching_labels(
            image_config(**{BASE_NAME_LABEL: "public.ecr.aws/example/base@sha256:" + "0" * 64}),
            base_reference=BASE_REFERENCE,
            commit_sha=COMMIT_SHA,
        )

    assert raised.value.reason == "published_base_image_mismatch"


def test_an_image_built_from_a_different_commit_is_rejected() -> None:
    # The tag carries only twelve hex characters of the commit. A collision used to be
    # caught loudly by the immutable push; on the resume path only this check catches it.
    with pytest.raises(PublishedImageError) as raised:
        require_matching_labels(
            image_config(**{REVISION_LABEL: "0" * 40}),
            base_reference=BASE_REFERENCE,
            commit_sha=COMMIT_SHA,
        )

    assert raised.value.reason == "published_revision_mismatch"


@pytest.mark.parametrize(
    "payload",
    [
        "not a mapping",
        {},
        {"config": "not a mapping"},
        {"config": {}},
        {"config": {"Labels": None}},
        {"config": {"Labels": {BASE_NAME_LABEL: BASE_REFERENCE}}},
        {"config": {"Labels": {REVISION_LABEL: COMMIT_SHA}}},
        {"config": {"Labels": {BASE_NAME_LABEL: BASE_REFERENCE, REVISION_LABEL: None}}},
    ],
)
def test_an_image_that_does_not_carry_both_labels_is_rejected(payload: object) -> None:
    # An unlabelled image cannot be shown to match, and "cannot be shown to match" has to
    # read the same as "does not match" for a gate whose whole job is to fail closed.
    with pytest.raises(PublishedImageError) as raised:
        require_matching_labels(
            payload,
            base_reference=BASE_REFERENCE,
            commit_sha=COMMIT_SHA,
        )

    assert raised.value.reason in {"published_image_unlabelled", "image_config_malformed"}


def test_a_matching_image_exits_quietly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "config.json").write_text(json.dumps(image_config()), encoding="utf-8")

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""


def test_a_mismatch_prints_only_a_machine_readable_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "config.json").write_text(json.dumps(image_config()), encoding="utf-8")

    exit_code = main(argv(tmp_path, **{"--commit-sha": "0" * 40}))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.strip() == "published_revision_mismatch"
    assert captured.out == ""
    assert str(tmp_path) not in captured.err


def test_an_image_config_that_is_not_json_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "config.json").write_text("<?xml version='1.0'?>\n", encoding="utf-8")

    exit_code = main(argv(tmp_path))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "image_config_malformed"


def test_a_missing_image_config_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "image_config_unreadable"


def test_an_image_config_that_is_not_utf_8_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "config.json").write_bytes(b'{"config": {"Labels": {"a": "\xff\xfe"}}}')

    exit_code = main(argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "image_config_undecodable"
