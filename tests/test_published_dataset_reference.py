import pytest
from pydantic import ValidationError

from edullm_platform.contracts.dataset import DatasetRelease
from edullm_platform.contracts.dataset_registry import PublishedDatasetReference
from tests.test_dataset import dataset_release_payload

PUBLISHED_URI = "s3://edullm-data/pretrain/olmo-150b-dolma2/v1/"

#: The 150B's tokens-group digest, read live 2026-07-31 from its own dataset.json and agreeing
#: with the manifest_sha256 map in its _VALIDATED.json. Bare hex, which is the point.
TOKENS_GROUP_DIGEST = "3f00499dbed01bc01a57097e84ae38ccd670b2b9d7981587d5fc828466ccf699"

#: The same dataset.json's groups[0].depends_on[0].manifest_sha256 -- the tokenizer dependency,
#: role: "tokenizer", dataset_id: "tokenizer/dolma2-bpe" -- read live 2026-07-31. Same file, same
#: group, a different digest for a different claim, and the plausible wrong one to copy: it is
#: also 64 bare hex characters, so nothing about its shape marks it as the wrong candidate.
TOKENIZER_DEPENDENCY_DIGEST = "b37b8954f767a351b726aa66f47867c299be41f96aee7b17171bf8851a772267"

INSIDE_THE_SANDBOX = (
    "s3://sbsandbox-intern-edullm-outputs/teams/platform/runs/x/",
    "s3://sbsandbox-intern-edullm-datasets/dolma/2026-07/",
)

NOT_A_PUBLISHED_PREFIX = (
    "s3://edullm-data/pretrain/olmo-150b-dolma2/v1",
    "s3://edullm-data/",
    "s3://EDULLM-DATA/pretrain/olmo-150b-dolma2/v1/",
    "https://edullm-data.s3.amazonaws.com/pretrain/olmo-150b-dolma2/v1/",
    "edullm-data/pretrain/olmo-150b-dolma2/v1/",
)


def reference_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "reference_id": "olmo-150b-dolma2-v1",
        "uri": PUBLISHED_URI,
        "dataset_id": "pretrain/olmo-150b-dolma2",
        "version": "v1",
        "manifest_sha256": TOKENS_GROUP_DIGEST,
        "tokenizer": "tokenizer/dolma2-bpe",
    }
    payload.update(overrides)
    return payload


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...] | None = None,
    message_fragment: str | None = None,
) -> None:
    matching_errors = [item for item in error.errors() if item["type"] == error_type]
    if loc is not None:
        matching_errors = [item for item in matching_errors if item["loc"] == loc]
    assert matching_errors, f"expected error type {error_type!r}, got {error.errors()}"
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_a_reference_to_a_corpus_somebody_else_published_is_accepted() -> None:
    reference = PublishedDatasetReference.model_validate(
        {
            "reference_id": "olmo-150b-dolma2-v1",
            "uri": PUBLISHED_URI,
            "dataset_id": "pretrain/olmo-150b-dolma2",
            "version": "v1",
            "manifest_sha256": TOKENS_GROUP_DIGEST,
            "tokenizer": "tokenizer/dolma2-bpe",
        }
    )

    assert reference.uri == PUBLISHED_URI
    assert reference.dataset_id == "pretrain/olmo-150b-dolma2"
    assert reference.version == "v1"
    assert reference.tokenizer == "tokenizer/dolma2-bpe"


def test_a_digest_wearing_the_ecr_prefix_is_refused_because_upstream_publishes_bare_hex() -> None:
    """Mutation: type manifest_sha256 as Sha256Digest, which already exists and looks right.

    SHA256_DIGEST_PATTERN in contracts/base.py is ^sha256:[0-9a-f]{64}$ -- correct for the ECR
    image digests it was written for, and wrong here. dataset.json publishes 64 bare hex
    characters. Reusing that type refuses every real value; "fixing" it by prefixing on the way
    in stores a string the publisher never wrote, and the next reader comparing our record to
    theirs finds two digests that differ by six characters and no way to tell whether that is
    a re-encoding or a changed corpus.
    """
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(
            reference_payload(manifest_sha256=f"sha256:{TOKENS_GROUP_DIGEST}")
        )
    assert_validation_error(
        exc_info.value, error_type="string_pattern_mismatch", loc=("manifest_sha256",)
    )


def test_the_digest_a_reference_pins_is_the_one_group_manifest_and_not_the_dataset() -> None:
    """Mutation: accept the dataset_sha256 from _VALIDATED.json instead.

    Both are 64 hex characters and both are in the seal, so nothing about their shape tells
    them apart. They are different claims: dataset_sha256 roots the whole seal, and
    manifest_sha256 is per GROUP -- the dataset standard's core example shows two groups
    carrying two digests. What a container can resolve on a read is the group's, because
    dataset_paths reads one group; pinning the other means comparing two values that were
    never meant to be equal and reporting the mismatch as a corrupted corpus.

    There is no dataset-level digest to fall back on -- the digest is per group, full stop --
    and this corpus's own dataset.json offers a second, wrong candidate at that same group:
    groups[0].depends_on[0].manifest_sha256, the tokenizer dependency's digest, also 64 bare hex
    characters and therefore indistinguishable by shape. TOKENIZER_DEPENDENCY_DIGEST names it so
    this test can fail for the reason its name gives: it fails if the tokenizer's digest is
    pinned in place of the group's, not merely if the field's type changes.

    Read live 2026-07-31 from pretrain/olmo-150b-dolma2/v1/dataset.json: groups[0].manifest_sha256
    is TOKENS_GROUP_DIGEST (the one to pin) and groups[0].depends_on[0].manifest_sha256 is
    TOKENIZER_DEPENDENCY_DIGEST (role: "tokenizer", the wrong one to copy).
    """
    reference = PublishedDatasetReference.model_validate(
        reference_payload(manifest_sha256=TOKENS_GROUP_DIGEST)
    )

    assert reference.manifest_sha256 == TOKENS_GROUP_DIGEST
    assert reference.manifest_sha256 != TOKENIZER_DEPENDENCY_DIGEST


def test_a_reference_names_the_tokenizer_the_corpus_declares_and_never_a_default() -> None:
    """Mutation: default tokenizer to "tokenizer/dolma2-bpe" because most corpora use it.

    THE UPSTREAM FAMILY FILE ARGUES AGAINST EXACTLY THIS DEFAULT AND SAYS WHY. families/
    pretrain.json ships a tokenizer_dependency_optional block that is off, with the comment:
    "a wrong family-wide default is dangerous precisely because a mismatched tokenizer's ids
    usually still fall in range and pass silently". The two corpora C2 registers prove the
    point -- pretrain/regmix-10b depends on tokenizer/dolma2-bpe at vocab 100278, and
    pretrain/lean4-mathlib-bytes depends on tokenizer/bytes-utf8 at vocab 256. A default that
    was right for one would be silently wrong for the other.

    Required rather than defaulted for the same reason the standard gives for license.basis:
    an honest declaration beats a convenient one, and a field nobody had to fill in is a field
    nobody checked.
    """
    payload = reference_payload()
    del payload["tokenizer"]

    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing", loc=("tokenizer",))


@pytest.mark.parametrize("uri", NOT_A_PUBLISHED_PREFIX)
def test_a_uri_that_is_not_a_published_prefix_is_refused(uri: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(reference_payload(uri=uri))
    assert_validation_error(
        exc_info.value, error_type="string_pattern_mismatch", loc=("uri",)
    )


@pytest.mark.parametrize("uri", INSIDE_THE_SANDBOX)
def test_a_prefix_in_our_own_namespace_is_not_a_published_reference(uri: str) -> None:
    """Mutation: accept any s3:// URI.

    The two types are not interchangeable in either direction. A checkpoint destination
    inside our namespace is a place this platform writes, and calling it a published
    reference would let a run declare its own output as its input -- which reads coherent
    and is a loop nothing would catch.
    """
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(reference_payload(uri=uri))
    assert_validation_error(
        exc_info.value, error_type="string_pattern_mismatch", loc=("uri",)
    )


def test_a_published_reference_cannot_be_expressed_as_a_dataset_release() -> None:
    """THE TEST THAT RECORDS WHY THIS MODEL EXISTS. Mutation: reach for DatasetRelease with a
    widened URI type.

    Widening the URI is necessary and is nowhere near sufficient. validate_release requires
    either a parent release or the run that produced it, and a corpus somebody else built has
    neither: derived_from holds slash-free identifiers this platform registers, and
    produced_by_run_id is a run_ uuid7 we mint. So no aliasing of the URI type makes
    DatasetRelease able to describe one, and this asserts the refusal so that a later reader
    reaching for the simpler-looking option finds the reason rather than the wall.
    """
    with pytest.raises(ValidationError) as exc_info:
        DatasetRelease.model_validate(
            dataset_release_payload(derived_from=[], produced_by_run_id=None)
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="either a parent release or the run that produced it",
    )


def test_the_identifier_cannot_carry_the_slashes_the_dataset_id_needs() -> None:
    """Mutation: use the dataset_id as the reference_id.

    DATASET_RELEASE_ID_PATTERN forbids slashes, so pretrain/olmo-150b-dolma2 is not
    expressible as an identifier and the URI is the only join key between the two systems.
    That is a constraint worth a test rather than a comment, because the obvious tidy-up is
    to drop one of the two fields.
    """
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(
            reference_payload(reference_id="pretrain/olmo-150b-dolma2")
        )
    assert exc_info.value.errors()[0]["loc"] == ("reference_id",)


def test_the_reader_arguments_are_stored_apart_rather_than_split_from_the_uri() -> None:
    """Mutation: store the URI alone and parse it at each call site.

    dataset_paths takes dataset_id and version as two arguments. A single stored string means
    every caller splits it, and the caller that splits it differently is the one that reads a
    version nobody registered.
    """
    reference = PublishedDatasetReference.model_validate(reference_payload())

    assert reference.uri.endswith(f"/{reference.dataset_id}/{reference.version}/")


def test_a_dataset_id_missing_its_family_segment_is_refused_even_though_it_is_a_suffix_match() -> (
    None
):
    """Mutation: check ``uri.endswith(f"/{dataset_id}/{version}/")`` instead of an exact
    reconstruction of the uri from its parts.

    ``"olmo-150b-dolma2"`` is a proper suffix of the real id ``"pretrain/olmo-150b-dolma2"`` --
    the uri still ends with ``"/olmo-150b-dolma2/v1/"`` -- but it is missing its ``pretrain/``
    segment and is therefore not the corpus's real id. A suffix test accepts this and would
    store a dataset_id that is not the dataset's id, which a later task passes straight to the
    upstream reader. This is exactly the case a suffix check lets through and the one a future
    simplification back to ``endswith`` would reintroduce.
    """
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(reference_payload(dataset_id="olmo-150b-dolma2"))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="cannot describe different objects",
    )


def test_a_dataset_id_naming_a_different_corpus_than_the_uri_is_refused() -> None:
    """A plain mismatch, not merely a truncation: the uri and dataset_id name two different
    corpora entirely, and the reference must be refused rather than silently pinned to whichever
    one the reader assumes.
    """
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(
            reference_payload(dataset_id="pretrain/regmix-10b")
        )
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="cannot describe different objects",
    )


def test_a_version_that_disagrees_with_the_uri_is_refused() -> None:
    """The uri, dataset_id and version must all describe the same object; a version field that
    disagrees with the uri's path segment is refused the same way a disagreeing dataset_id is.
    """
    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(reference_payload(version="v2"))
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        message_fragment="cannot describe different objects",
    )
