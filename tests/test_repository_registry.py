import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform import admission, manifest_helpers, submission
from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.repository_registry import (
    RegisteredRepository,
    RepositoryRegistry,
    UnknownRepositoryError,
)

BASE_DIGEST = "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"


def function_body_source(target: Any) -> str:
    """One function's code without its docstring, so prose cannot satisfy a code check."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(target)))
    definition = tree.body[0]
    assert isinstance(definition, ast.FunctionDef)
    if ast.get_docstring(definition) is not None:
        definition.body = definition.body[1:]
    return ast.unparse(definition)


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


def test_the_shipped_registry_registers_olmo_core_exactly_as_it_was_reviewed() -> None:
    """Mutation: change any field of the registration that is deployed and running.

    Pinned field for field rather than by count. This used to assert the whole registry
    equalled one entry, which was the same thing while one repository was registered and
    became a barrier to registering a second -- the invariant was never "there is one
    repository", it was "this repository's registration is what was reviewed".
    """
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)
    olmo = next(
        entry for entry in registry.repositories if entry.repository == "OLMo-core"
    )

    assert olmo.model_dump() == {
        "repository": "OLMo-core",
        "github_repository_id": 1306868157,
        "default_branch": "main",
        "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
        "base_image_repository": "docker.io/library/python",
        "base_image_digest": BASE_DIGEST,
        "dockerfile_path": ".edullm/Dockerfile",
        "build_context": ".",
    }


def test_the_registry_and_the_pilot_list_are_asked_different_questions() -> None:
    """TWO FILES LIST REPOSITORIES AND ONLY ONE OF THEM ANSWERS THIS. Mutation: derive
    ``repository_registered`` from the roster again.

    ``config/organization.yaml``'s ``pilot_repositories`` is a declaration of what the pilot
    programme covers, which is why the Phase 0 inventory check holds it to exactly OLMo-core
    and dolma. ``config/repositories.yaml`` is where a repository acquires an ECR repository,
    a registered base image and a Dockerfile path -- the things that let an image exist for
    it. They were the same list once and are not the same question, and admission derived
    "is this repository registered" from the first of them.

    Both directions were live at once. ``dolma`` is a pilot with no registration, and a
    submission naming it with ``dolma-tokenize-smoke`` was accepted, routed to a lead and
    would have been submitted to the CPU queue -- where it would have run the OLMo-core
    image, because the image is pinned in the job definition rather than chosen by the
    submission. ``edullm-data`` is registered and is not a pilot, so the first workload
    written against it would have been denied outright.

    The two lists are left as they are, because the fix is not to make them equal: the
    programme's scope and the build registry can legitimately differ. What must not happen
    again is a fact reading the one that does not answer it, and the assertion below is over
    the derivation rather than over the files.
    """
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)
    inventory = load_yaml(root / "config" / "organization.yaml", OrganizationInventory)
    registered = {entry.repository for entry in registry.repositories}
    pilots = set(inventory.pilot_repositories)

    # Recorded rather than asserted away. The day these coincide again this still passes,
    # and the derivation below is what keeps the answer right either way.
    assert pilots - registered == {"dolma"}
    assert registered - pilots == {"edullm-data"}

    derivation = manifest_helpers.build_request_facts
    assert "repositories.is_registered(manifest.repository)" in inspect.getsource(derivation)
    assert "inventory" not in inspect.signature(derivation).parameters
    # Over the code rather than over the file. The docstring records the defect and
    # therefore quotes the expression that caused it, so a text search would fail for
    # describing what it prevents.
    assert "pilot_repositories" not in function_body_source(derivation)


def test_both_production_callers_derive_registration_from_the_repository_registry() -> None:
    """The seam, written the way the image-scan gate's is. Mutation: read a set elsewhere.

    Nothing stops a caller passing a registry it built itself, and a registry assembled
    beside a caller makes that caller -- rather than reviewed configuration -- the authority
    on what admission accepts. Both production paths take the argument and both pass it
    through, and the compile step and admission have to agree or an approval is spent on a
    submission the far side will refuse.
    """
    for module, function in ((admission, "admit"), (submission, "compile_submission")):
        target = getattr(module, function)
        assert "repositories" in inspect.signature(target).parameters
        assert "repositories=repositories" in inspect.getsource(target), (
            f"{module.__name__}.{function} no longer passes the repository registry into "
            "build_request_facts, so its answer to 'is this repository registered' comes "
            "from somewhere else"
        )


def test_is_registered_answers_for_every_repository_the_catalog_names() -> None:
    """Mutation: let a workload name a repository nothing registers and call it fine.

    ``is_registered`` is the one answer, so this checks it against the file rather than
    against a remembered list, and names the one workload repository that fails it -- which
    is why ``dolma-tokenize-smoke`` is absent from the submission form.
    """
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)
    catalog = yaml.safe_load(
        (root / "config" / "workload-catalog.yaml").read_text(encoding="utf-8")
    )
    named = {workload["repository"] for workload in catalog["workloads"]}

    assert {name for name in named if not registry.is_registered(name)} == {"dolma"}
    assert registry.is_registered("OLMo-core")
    assert registry.is_registered("edullm-data")
    assert not registry.is_registered("Olmo-Core"), "registration is not case insensitive"
    assert not registry.is_registered("")


def test_every_registration_names_a_distinct_ecr_repository() -> None:
    """Mutation: copy a registration and forget to change where its images go.

    Two repositories pushing to one ECR repository is not an error anywhere -- the tags
    are per-commit and would not collide -- so nothing fails. What is lost is that the
    repository an image came from stops being answerable from where it is stored, and the
    image tag immutability that the whole provenance chain rests on is protecting one
    namespace shared by two codebases.
    """
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)

    destinations = [entry.ecr_repository for entry in registry.repositories]
    identifiers = [entry.github_repository_id for entry in registry.repositories]

    assert len(set(destinations)) == len(destinations)
    assert len(set(identifiers)) == len(identifiers)


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


# ---------------------------------------------------------------------------------------
# A registration is inert unless the publisher role can act on it
# ---------------------------------------------------------------------------------------


def publisher_role() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    template = yaml.safe_load(
        (root / "infra" / "iam" / "ecr-publisher-role.yaml").read_text(encoding="utf-8")
    )
    role = next(
        resource
        for resource in template["Resources"].values()
        if resource.get("Type") == "AWS::IAM::Role"
    )
    return dict(role["Properties"])


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def arn_text(resource: Any) -> str:
    """One Resource entry as text, whether it is a literal or an ``Fn::Sub``.

    ``ecr:GetAuthorizationToken`` is granted on a plain ``"*"`` because it is not a
    resource-level action, so a reader that assumed every Resource is a mapping trips on
    the first statement.
    """
    return str(resource.get("Fn::Sub", resource)) if isinstance(resource, dict) else str(resource)


def test_the_publisher_role_trusts_every_registered_repository() -> None:
    """Reads BOTH sides. Mutation: register a repository and leave the role alone.

    THIS WAS SHIPPED AND THIS TEST IS WHY IT WAS FOUND. `edullm-data` was added to
    `config/repositories.yaml` with its ECR repository created and its base pinned, and the
    registration was inert: the trust policy matches `repository_id` against OLMo-core's
    `1306868157` with StringEquals, so no other repository's token can assume the role at
    all.

    The failure is the worst shape available. It is not a refusal anybody reads -- it is an
    AssumeRole denial inside a publish job, which reads like a broken role ARN rather than
    like a repository nobody authorised. Registration says where images go; this says who
    may put them there, and one without the other is a repository that looks onboarded.
    """
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)
    condition = publisher_role()["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
    trusted = {
        str(value)
        for value in as_list(
            condition["StringEquals"]["token.actions.githubusercontent.com:repository_id"]
        )
    }

    assert trusted == {str(entry.github_repository_id) for entry in registry.repositories}


def test_the_publisher_role_may_push_to_every_registered_destination() -> None:
    """Reads BOTH sides. Mutation: trust a repository and not grant it its ECR repository.

    The half that fails later and reads even less like its cause. A token that assumes the
    role successfully and is then denied `ecr:PutImage` produces an access-denied deep in a
    docker push, after the image has already been built.
    """
    root = Path(__file__).resolve().parents[1]
    registry = load_yaml(root / "config" / "repositories.yaml", RepositoryRegistry)
    statements = publisher_role()["Policies"][0]["PolicyDocument"]["Statement"]
    granted = {
        arn_text(resource).rsplit("repository/", 1)[-1]
        for statement in statements
        for resource in as_list(statement["Resource"])
        if "repository/" in arn_text(resource)
    }

    assert granted == {entry.ecr_repository for entry in registry.repositories}
