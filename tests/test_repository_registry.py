from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import (
    RegisteredRepository,
    RepositoryRegistry,
    UnknownRepositoryError,
)

BASE_DIGEST = "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"


def repository_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repository": "OLMo-core",
        "github_repository_id": 1306868157,
        "default_branch": "main",
        "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
        "base_image_repository": "docker.io/library/python",
        "base_image_digest": BASE_DIGEST,
        "dockerfile_path": ".edullm/Dockerfile",
        "build_context": ".",
    }
    payload.update(overrides)
    return payload


def registry_payload(*repositories: dict[str, object]) -> dict[str, object]:
    return {"repositories": list(repositories or (repository_payload(),))}


def test_shipped_repository_registry_contains_exact_olmo_core_registration() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)

    assert registry.model_dump() == {
        "repositories": (
            {
                "repository": "OLMo-core",
                "github_repository_id": 1306868157,
                "default_branch": "main",
                "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
                "base_image_repository": "docker.io/library/python",
                "base_image_digest": BASE_DIGEST,
                "dockerfile_path": ".edullm/Dockerfile",
                "build_context": ".",
            },
        )
    }


def test_registered_repository_exposes_full_immutable_base_reference() -> None:
    repository = RegisteredRepository.model_validate(repository_payload())

    assert (
        repository.immutable_base_reference
        == f"docker.io/library/python@{BASE_DIGEST}"
    )


def test_registered_repository_is_strict_and_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(
            repository_payload(github_repository_id="1306868157")
        )
    with pytest.raises(ValidationError) as exc_info:
        RegisteredRepository.model_validate(repository_payload(unexpected=True))
    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())


@pytest.mark.parametrize("field", ["repository", "default_branch"])
@pytest.mark.parametrize("value", ["", "   "])
def test_registered_repository_rejects_empty_names(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(repository_payload(**{field: value}))


@pytest.mark.parametrize("github_repository_id", [0, -1])
def test_registered_repository_requires_positive_github_id(
    github_repository_id: int,
) -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(
            repository_payload(github_repository_id=github_repository_id)
        )


@pytest.mark.parametrize(
    "ecr_repository",
    [
        "edullm-olmo-core",
        "sbsandbox-intern-",
        "sbsandbox-intern-Uppercase",
        "sbsandbox-intern-edullm--olmo",
        "sbsandbox-intern-edullm:olmo",
        "sbsandbox-intern-/olmo",
    ],
)
def test_registered_repository_rejects_invalid_ecr_repository_names(
    ecr_repository: str,
) -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(
            repository_payload(ecr_repository=ecr_repository)
        )


@pytest.mark.parametrize(
    "base_image_repository",
    [
        "",
        "   ",
        "docker.io/library/python:3.12",
        f"docker.io/library/python@{BASE_DIGEST}",
    ],
)
def test_registered_repository_rejects_tagged_or_digested_base_repositories(
    base_image_repository: str,
) -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(
            repository_payload(base_image_repository=base_image_repository)
        )


@pytest.mark.parametrize(
    "base_image_digest",
    [
        "sha256:abc",
        "sha256:" + "A" * 64,
        "sha512:" + "a" * 64,
        "a" * 64,
    ],
)
def test_registered_repository_rejects_invalid_base_image_digests(
    base_image_digest: str,
) -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(
            repository_payload(base_image_digest=base_image_digest)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dockerfile_path", ""),
        ("dockerfile_path", "."),
        ("dockerfile_path", "/Dockerfile"),
        ("dockerfile_path", "../Dockerfile"),
        ("dockerfile_path", "images/../../Dockerfile"),
        ("dockerfile_path", r"images\Dockerfile"),
        ("build_context", ""),
        ("build_context", "/workspace"),
        ("build_context", ".."),
        ("build_context", "images/../workspace"),
        ("build_context", r"images\workspace"),
    ],
)
def test_registered_repository_rejects_unsafe_repository_relative_paths(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        RegisteredRepository.model_validate(repository_payload(**{field: value}))


def test_repository_registry_preserves_authored_order() -> None:
    second = repository_payload(
        repository="dolma",
        github_repository_id=999,
        ecr_repository="sbsandbox-intern-edullm-dolma",
    )
    registry = RepositoryRegistry.model_validate(
        registry_payload(repository_payload(), second)
    )

    assert tuple(item.repository for item in registry.repositories) == (
        "OLMo-core",
        "dolma",
    )


def test_repository_registry_requires_at_least_one_repository() -> None:
    with pytest.raises(ValidationError):
        RepositoryRegistry.model_validate({"repositories": []})


@pytest.mark.parametrize(
    ("duplicate_field", "expected_message"),
    [
        ("repository", "repository names must be unique"),
        ("github_repository_id", "GitHub repository IDs must be unique"),
        ("ecr_repository", "ECR repository names must be unique"),
    ],
)
def test_repository_registry_rejects_duplicate_identifiers(
    duplicate_field: str,
    expected_message: str,
) -> None:
    first = repository_payload()
    second = repository_payload(
        repository="dolma",
        github_repository_id=999,
        ecr_repository="sbsandbox-intern-edullm-dolma",
    )
    second[duplicate_field] = first[duplicate_field]

    with pytest.raises(ValidationError) as exc_info:
        RepositoryRegistry.model_validate(registry_payload(first, second))
    assert any(
        expected_message in error["msg"] for error in exc_info.value.errors()
    )


def test_repository_registry_looks_up_repositories_by_name_and_id() -> None:
    registry = RepositoryRegistry.model_validate(registry_payload())

    assert registry.repository_by_name("OLMo-core") is registry.repositories[0]
    assert registry.repository_by_id(1306868157) is registry.repositories[0]


def test_repository_registry_unknown_lookups_raise_domain_error() -> None:
    registry = RepositoryRegistry.model_validate(registry_payload())

    with pytest.raises(UnknownRepositoryError, match="missing"):
        registry.repository_by_name("missing")
    with pytest.raises(UnknownRepositoryError, match="999"):
        registry.repository_by_id(999)
