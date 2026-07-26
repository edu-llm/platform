from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.read_image_config_digest import (
    ImageManifestError,
    main,
    read_config_digest,
)

CONFIG_DIGEST = "sha256:" + "c" * 64
LAYER_DIGEST = "sha256:" + "d" * 64
DOCKER_MANIFEST_MEDIA_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"


def manifest(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": 2,
        "mediaType": DOCKER_MANIFEST_MEDIA_TYPE,
        "config": {
            "mediaType": "application/vnd.docker.container.image.v1+json",
            "size": 4096,
            "digest": CONFIG_DIGEST,
        },
        "layers": [
            {
                "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                "size": 1024,
                "digest": LAYER_DIGEST,
            }
        ],
    }
    payload.update(overrides)
    return payload


def argv(tmp_path: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--manifest": str(tmp_path / "manifest.json"),
        "--output": str(tmp_path / "config-digest.txt"),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_a_docker_image_manifest_names_its_configuration_blob() -> None:
    assert read_config_digest(manifest()) == CONFIG_DIGEST


def test_an_oci_image_manifest_names_its_configuration_blob() -> None:
    assert read_config_digest(manifest(mediaType=OCI_MANIFEST_MEDIA_TYPE)) == CONFIG_DIGEST


def test_a_manifest_without_a_declared_media_type_is_still_read() -> None:
    # An OCI manifest may omit mediaType, and BatchGetImage is already asked for exactly
    # the two image-manifest types, so the absence carries no ambiguity worth rejecting.
    payload = manifest()
    del payload["mediaType"]

    assert read_config_digest(payload) == CONFIG_DIGEST


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("not a mapping", "manifest_malformed"),
        ({"config": {"digest": CONFIG_DIGEST}}, "manifest_schema_unsupported"),
        (manifest(schemaVersion=1), "manifest_schema_unsupported"),
        (
            manifest(mediaType="application/vnd.oci.image.index.v1+json"),
            "manifest_media_type_unsupported",
        ),
        (
            {
                "schemaVersion": 2,
                "mediaType": DOCKER_MANIFEST_MEDIA_TYPE,
                "manifests": [{"digest": CONFIG_DIGEST}],
            },
            "manifest_is_a_list",
        ),
        (manifest(config={}), "manifest_config_digest_malformed"),
        (manifest(config={"digest": "sha512:" + "c" * 128}), "manifest_config_digest_malformed"),
        (manifest(config={"digest": "sha256:" + "C" * 64}), "manifest_config_digest_malformed"),
        (manifest(config=[CONFIG_DIGEST]), "manifest_without_config"),
    ],
)
def test_a_manifest_that_does_not_name_one_image_configuration_is_rejected(
    payload: object,
    reason: str,
) -> None:
    with pytest.raises(ImageManifestError) as raised:
        read_config_digest(payload)

    assert raised.value.reason == reason


def test_the_digest_is_written_where_the_next_command_reads_it(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")

    assert main(argv(tmp_path)) == 0
    assert (tmp_path / "config-digest.txt").read_text(encoding="utf-8") == f"{CONFIG_DIGEST}\n"


def test_a_rejected_manifest_prints_only_a_machine_readable_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": []}), encoding="utf-8"
    )

    exit_code = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.strip() == "manifest_is_a_list"
    assert captured.out == ""
    assert str(tmp_path) not in captured.err
    assert not (tmp_path / "config-digest.txt").exists()


def test_a_manifest_that_is_not_json_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "manifest.json").write_text("None\n", encoding="utf-8")

    exit_code = main(argv(tmp_path))

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "manifest_malformed"


def test_a_missing_manifest_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "manifest_unreadable"


def test_a_manifest_that_is_not_utf_8_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "manifest.json").write_bytes(b'{"schemaVersion": 2, "x": "\xff\xfe"}')

    exit_code = main(argv(tmp_path))

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "manifest_undecodable"
