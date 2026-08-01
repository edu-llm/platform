from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import (
    DatasetRegistry,
    PublishedDatasetReference,
    RegisteredDatasetRelease,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SHIPPED_RELEASE_ID = "dolma-2026-07"
NO_DATASET_ID = "none"

#: The same corpus C1's own test module names, reused here rather than invented, so a
#: reader who has already seen PublishedDatasetReference validated once does not have to
#: check whether a second sample means something different.
PUBLISHED_URI = "s3://edullm-data/pretrain/olmo-150b-dolma2/v1/"
PUBLISHED_DIGEST = "3f00499dbed01bc01a57097e84ae38ccd670b2b9d7981587d5fc828466ccf699"


def load_dataset_registry() -> DatasetRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)


def registry_payload(*release_ids: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "releases": [
            {"release_id": release_id}
            for release_id in (release_ids or (SHIPPED_RELEASE_ID,))
        ],
    }
    payload.update(overrides)
    return payload


def published_reference_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "reference_id": "olmo-150b-dolma2-v1",
        "uri": PUBLISHED_URI,
        "dataset_id": "pretrain/olmo-150b-dolma2",
        "version": "v1",
        "manifest_sha256": PUBLISHED_DIGEST,
        "tokenizer": "tokenizer/dolma2-bpe",
    }
    payload.update(overrides)
    return payload


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    message_fragment: str | None = None,
) -> None:
    matching_errors = [item for item in error.errors() if item["type"] == error_type]
    assert matching_errors, f"expected error type {error_type!r}, got {error.errors()}"
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_shipped_dataset_registry_loads_and_registers_the_pilot_release() -> None:
    registry = load_dataset_registry()

    assert registry.schema_version == 1
    assert registry.release_ids == frozenset({SHIPPED_RELEASE_ID, NO_DATASET_ID})
    assert registry.is_registered(SHIPPED_RELEASE_ID) is True


def test_a_run_that_reads_no_data_can_say_so_rather_than_naming_a_release_it_never_opened() -> (
    None
):
    # `dataset_release` is required, so before `none` existed the only way to submit was to
    # name a release. Every run so far therefore recorded that it read `dolma-2026-07`, and
    # none of them read anything -- the bucket does not exist. A required field with one
    # option does not collect a fact, it manufactures one, and it manufactures it into
    # immutable lineage where it cannot later be corrected.
    registry = load_dataset_registry()

    assert registry.is_registered(NO_DATASET_ID) is True

    options = _submit_run_dataset_options()
    assert options[0] == NO_DATASET_ID, (
        "the honest answer for a smoke run should be the one a first-time submitter "
        f"reaches first, and the options begin {options!r}"
    )


def _submit_run_dataset_options() -> list[str]:
    document = yaml.safe_load(
        (PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml").read_text(encoding="utf-8")
    )
    # PyYAML reads a bare `on:` key as the boolean True under YAML 1.1.
    triggers = document.get(True) or document.get("on")
    inputs = triggers["workflow_dispatch"]["inputs"]
    return list(inputs["dataset_release"]["options"])


def test_the_shipped_registry_is_the_set_the_representative_manifests_name() -> None:
    manifest_releases = {
        line.split(":", maxsplit=1)[1].strip()
        for path in sorted((PROJECT_ROOT / "fixtures" / "manifests").glob("*.yaml"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("dataset_release:")
    }

    assert manifest_releases, "no fixture declared a dataset release, so this proves nothing"
    assert manifest_releases <= load_dataset_registry().release_ids


def test_a_registry_entry_carries_a_release_identifier_and_whether_it_is_offered() -> None:
    """Mutation: add uri, dataset_id and version here instead of to a second model.

    STILL TRUE AFTER TWO THINGS ARRIVED, WHICH IS WHY THIS TEST SURVIVED RATHER THAN BEING
    DELETED. Admission asks one question of this model -- is this registered -- and
    unregistered_dataset is a denied-outright condition evaluated on the identifier alone. A
    corpus somebody else published needs a URI, a dataset id and a version, and those are on
    PublishedDatasetReference, where the fields have a reader that consumes them.

    ``retired`` is the second, and it is the one exception because it answers a question
    about this list rather than about the dataset: whether the form offers the identifier,
    which is separate from whether admission accepts it. Conflating those forced a bad
    choice -- de-register ``dolma-2026-07`` and make every historical record unresolvable,
    or keep offering a dataset nothing is bound to.
    """
    assert tuple(RegisteredDatasetRelease.model_fields) == ("release_id", "retired")


@pytest.mark.parametrize(
    "release_ids",
    [
        ("dolma-2026-08", "dolma-2026-07"),
        ("dolma-2026-07", "dolma-2026-09", "dolma-2026-08"),
        ("zzz", "aaa"),
    ],
)
def test_registry_rejects_releases_that_are_not_in_ascending_order(
    release_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(registry_payload(*release_ids))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="registered dataset releases must be listed once each in ascending order",
    )


@pytest.mark.parametrize(
    "release_ids",
    [
        ("dolma-2026-07", "dolma-2026-07"),
        ("dolma-2026-07", "dolma-2026-07", "dolma-2026-08"),
    ],
)
def test_registry_rejects_a_release_listed_more_than_once(
    release_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(registry_payload(*release_ids))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="registered dataset releases must be listed once each in ascending order",
    )


@pytest.mark.parametrize(
    "release_ids",
    [
        ("dolma-2026-07",),
        ("dolma-2026-07", "dolma-2026-08"),
        ("a", "b", "c"),
    ],
)
def test_registry_accepts_a_sorted_list_of_distinct_releases(
    release_ids: tuple[str, ...],
) -> None:
    registry = DatasetRegistry.model_validate(registry_payload(*release_ids))

    assert registry.release_ids == frozenset(release_ids)
    assert tuple(entry.release_id for entry in registry.releases) == release_ids


@pytest.mark.parametrize(
    ("registered", "queried", "expected"),
    [
        (("dolma-2026-07",), "dolma-2026-07", True),
        (("dolma-2026-07",), "dolma-2026-08", False),
        (("dolma-2026-07",), "dolma-2026-0", False),
        (("dolma-2026-07",), "dolma-2026-070", False),
        (("dolma-2026-07",), "", False),
        (("dolma-2026-07", "dolma-2026-08"), "dolma-2026-08", True),
        (("c4-2026-01", "dolma-2026-07"), "c4-2026-01", True),
        (("c4-2026-01", "dolma-2026-07"), "pile-2020", False),
    ],
)
def test_is_registered_answers_for_exactly_the_listed_releases(
    registered: tuple[str, ...],
    queried: str,
    expected: bool,
) -> None:
    registry = DatasetRegistry.model_validate(registry_payload(*registered))

    assert registry.is_registered(queried) is expected
    assert (queried in registry.release_ids) is expected


def test_an_empty_registry_cannot_be_expressed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(registry_payload(releases=[]))
    assert_validation_error(exc_info.value, error_type="too_short")
    assert exc_info.value.errors()[0]["loc"] == ("releases",)


def test_registry_rejects_an_unordered_container_of_releases() -> None:
    payload = registry_payload("dolma-2026-07", "dolma-2026-08")
    payload["releases"] = iter(list(payload["releases"]))  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="ordered sequences must be provided as a list or tuple",
    )


@pytest.mark.parametrize(
    "release_id",
    ["", "Dolma-2026-07", "dolma 2026 07", "dolma--2026", "-dolma", "dolma-", "dolma/2026"],
)
def test_registry_rejects_a_release_identifier_that_is_not_a_release_identifier(
    release_id: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(registry_payload(release_id))
    assert exc_info.value.errors()[0]["loc"] == ("releases", 0, "release_id")


def test_a_release_described_by_more_than_its_identifier_belongs_to_the_other_model() -> None:
    """Mutation: let a uri through here because a published corpus needs one.

    It does need one, and it has one, on a model whose other fields make it usable. A uri on
    this model would be a second place to look for the same fact and a first place to find it
    incomplete.
    """
    payload = registry_payload()
    payload["releases"] = [{"release_id": SHIPPED_RELEASE_ID, "uri": "s3://somewhere/"}]
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="extra_forbidden")


def test_a_registry_may_carry_no_published_references_at_all() -> None:
    registry = DatasetRegistry.model_validate(registry_payload())

    assert registry.published == ()
    assert registry.reference_for("olmo-150b-dolma2-v1") is None


@pytest.mark.parametrize(
    "reference_ids",
    [
        ("v-second", "a-first"),
        ("a-first", "a-first"),
    ],
)
def test_registry_rejects_published_references_that_are_not_in_ascending_order(
    reference_ids: tuple[str, ...],
) -> None:
    payload = registry_payload()
    payload["published"] = [
        published_reference_payload(reference_id=reference_id) for reference_id in reference_ids
    ]
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="published dataset references must be listed once each in ascending order",
    )


def test_reference_for_answers_for_exactly_the_listed_published_references() -> None:
    payload = registry_payload()
    payload["published"] = [
        published_reference_payload(reference_id="a-first"),
        published_reference_payload(reference_id="olmo-150b-dolma2-v1"),
    ]

    registry = DatasetRegistry.model_validate(payload)

    resolved = registry.reference_for("olmo-150b-dolma2-v1")
    assert resolved is not None
    assert isinstance(resolved, PublishedDatasetReference)
    assert resolved.dataset_id == "pretrain/olmo-150b-dolma2"
    assert registry.reference_for("not-registered") is None


def test_is_registered_answers_for_published_references_too() -> None:
    """Mutation: answer from ``releases`` alone, which is what this used to do.

    THIS TEST USED TO ASSERT THE OPPOSITE, AND THE REVERSAL IS THE POINT. When published
    references were added there was nothing that could name one: the submission form offered
    release ids only, so keeping the two lists as separate namespaces cost nothing and read
    as the tidier design -- ``is_registered`` for admission's question, ``reference_for`` for
    resolving a corpus.

    Offering the published corpora on the form is what made it wrong. ``phase0_gate`` denies
    a manifest outright with ``unregistered_dataset`` when this returns False, so a dropdown
    option backed only by the ``published`` list is a menu item whose sole outcome is a
    refusal -- and the refusal lands after the submission has been classified, routed and
    approved by a lead. That is the precise failure the form-options tests exist to prevent,
    arriving through the registry instead of through the YAML.

    Widened here rather than at the two call sites, deliberately. ``phase0_gate`` and
    ``manifest_helpers`` both ask this one question, and a third caller is a matter of time;
    asking each of them to remember to check a second list makes forgetting the default, and
    what forgetting produces is a denial after an approval rather than a visible error.
    ``reference_for`` keeps its narrow meaning -- resolve a corpus to its uri, digest and
    tokenizer -- because that genuinely is a different question, and nothing about it wants
    to be answered for ``dolma-2026-07``.
    """
    payload = registry_payload()
    payload["published"] = [published_reference_payload()]

    registry = DatasetRegistry.model_validate(payload)

    assert registry.is_registered("olmo-150b-dolma2-v1") is True
    assert registry.reference_for("olmo-150b-dolma2-v1") is not None

    # The widening is a union, not a replacement: the release list still answers, and
    # reference_for still refuses to resolve something that is only a release id.
    assert registry.is_registered(SHIPPED_RELEASE_ID) is True
    assert registry.reference_for(SHIPPED_RELEASE_ID) is None
    assert registry.is_registered("nothing-registers-this") is False


def test_registry_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DatasetRegistry.model_validate(registry_payload(schema_version=2))
    assert_validation_error(exc_info.value, error_type="literal_error")
    assert exc_info.value.errors()[0]["loc"] == ("schema_version",)


def test_registry_is_frozen_so_a_caller_cannot_register_a_dataset_at_runtime() -> None:
    registry = load_dataset_registry()
    with pytest.raises(ValidationError):
        registry.__setattr__("releases", ())
