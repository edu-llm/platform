import pytest

from edullm_platform.contracts.dataset_registry import (
    TRAINABLE_FAMILIES,
    WEIGHTS_FAMILIES,
    DatasetRegistry,
    PublishedDatasetReference,
    RegisteredDatasetRelease,
)
from edullm_platform.contracts.vocabulary import InputRole

DIGEST = "0" * 64


def reference(dataset_id: str, *, tokenizer: str | None = None) -> PublishedDatasetReference:
    return PublishedDatasetReference(
        reference_id=dataset_id.replace("/", "-") + "-v1",
        uri=f"s3://edullm-data/{dataset_id}/v1/",
        dataset_id=dataset_id,
        version="v1",
        manifest_sha256=DIGEST,
        tokenizer=tokenizer,
    )


def registry() -> DatasetRegistry:
    return DatasetRegistry(
        schema_version=1,
        releases=(RegisteredDatasetRelease(release_id="none"),),
        published=tuple(
            sorted(
                (
                    reference("model/smollm2-135m"),
                    reference("pretrain/fineweb-edu-1b", tokenizer="tokenizer/smollm2-bpe"),
                    reference("tokenizer/dolma2-bpe"),
                ),
                key=lambda entry: entry.reference_id,
            )
        ),
    )


def test_the_two_family_sets_are_disjoint_and_neither_is_empty():
    # The bug this catches: someone adds "model" to TRAINABLE_FAMILIES to make an eval
    # submission pass, which would let a training run memmap model weights as tokens.
    assert TRAINABLE_FAMILIES == frozenset({"pretrain", "sft"})
    assert WEIGHTS_FAMILIES == frozenset({"model"})
    assert not (TRAINABLE_FAMILIES & WEIGHTS_FAMILIES)


@pytest.mark.parametrize(
    ("dataset_id", "role", "permitted"),
    [
        ("model/smollm2-135m", InputRole.WEIGHTS, True),
        ("model/smollm2-135m", InputRole.CORPUS, False),
        ("pretrain/fineweb-edu-1b", InputRole.CORPUS, True),
        ("pretrain/fineweb-edu-1b", InputRole.WEIGHTS, False),
        ("tokenizer/dolma2-bpe", InputRole.CORPUS, False),
        ("tokenizer/dolma2-bpe", InputRole.WEIGHTS, False),
    ],
)
def test_a_reference_fills_the_role_its_family_names_and_no_other(dataset_id, role, permitted):
    # The bug this catches: a one-directional check. Asserting only that model/ fills WEIGHTS
    # passes even if may_fill returns True for every (id, role) pair, which is the filter-equals-
    # its-input defect. Each id is asserted in both roles, so a permissive answer fails a row.
    entry = registry().reference_for(dataset_id.replace("/", "-") + "-v1")
    assert entry is not None
    assert registry().may_fill(entry.reference_id, role=role) is permitted


def test_an_unresolvable_reference_fills_no_role():
    # Deliberately the opposite polarity from is_a_trainable_corpus, which answers True for
    # anything it cannot resolve. There is nothing to start from when nothing resolves, so
    # weights must fail closed, and corpus is asked here too so the asymmetry is pinned.
    assert registry().may_fill("none", role=InputRole.WEIGHTS) is False
    assert registry().may_fill("none", role=InputRole.CORPUS) is False
    assert registry().may_fill("no-such-thing", role=InputRole.WEIGHTS) is False
