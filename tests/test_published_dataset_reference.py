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

    STILL TRUE AFTER THE FIELD LEARNED TO HOLD NULL, WHICH IS WHY THIS SURVIVED. Nullable and
    optional are different properties and only the first was wanted. Four registered datasets
    genuinely declare no tokenizer, so the honest answer had to become spellable; leaving the
    key out is not that answer, it is the answer nobody wrote down, and it is the one the
    paragraph above is about. The test below covers the null that is now accepted.
    """
    payload = reference_payload()
    del payload["tokenizer"]

    with pytest.raises(ValidationError) as exc_info:
        PublishedDatasetReference.model_validate(payload)
    assert_validation_error(exc_info.value, error_type="missing", loc=("tokenizer",))


def test_a_corpus_that_declares_no_tokenizer_says_so_rather_than_borrowing_one() -> None:
    """Mutation: keep tokenizer non-null and write tokenizer/dolma2-bpe into the sft entries.

    Four registered datasets declare no tokenizer dependency at all, and they are not one
    case. ``sft/pedagogy70-normal30`` and ``sft/math-sft-60m`` are pre-tokenization
    conversation text, so there is nothing to name yet; ``vendor/openai-prm800k`` is a
    verbatim mirror of somebody else's jsonl; and a tokenizer has no tokenizer of its own.

    What a mandatory field buys in that situation is a plausible wrong value, which is the
    exact failure the test above is written about. Writing ``tokenizer/dolma2-bpe`` on the
    pedagogy corpus to satisfy the schema would publish a claim about how that corpus was
    built which nobody made and which nothing downstream could tell from a real one.
    """
    reference = PublishedDatasetReference.model_validate(
        reference_payload(
            reference_id="pedagogy70-normal30-v1",
            uri="s3://edullm-data/sft/pedagogy70-normal30/v1/",
            dataset_id="sft/pedagogy70-normal30",
            manifest_sha256="527f66916a4995f52ea667e6dc2008e7ecf83cdeb1886df9387bf04cc3b495fd",
            tokenizer=None,
        )
    )

    assert reference.tokenizer is None
    assert reference.family == "sft"
    assert reference.is_a_corpus_a_run_may_read


def test_a_tokenizer_is_registrable_and_is_not_a_corpus_a_run_may_read() -> None:
    """Mutation: rely on the null tokenizer to keep tokenizers out, as the shape used to.

    THE TWO PROPERTIES IN THIS TEST ARE INDEPENDENT AND BOTH ARE WANTED. Registrable, because
    ``pretrain/fineweb-edu-1b`` pins this digest in its ``depends_on`` and a lineage record
    naming a corpus built on a tokenizer nobody registered cannot be resolved back to what was
    read. Not a corpus, because a tokenizer declares no partitions and no dtype, so a run
    handed one memmaps ``tokenizer.json`` as uint16 tokens and trains on it without raising.

    ``tokenizer=None`` is passed here on purpose, since that is what the entry really carries
    and it is the state in which the old accidental refusal has been removed. If the family
    rule were not doing the work, this reference would be indistinguishable from the sft one
    above.
    """
    reference = PublishedDatasetReference.model_validate(
        reference_payload(
            reference_id="smollm2-bpe-v1",
            uri="s3://edullm-data/tokenizer/smollm2-bpe/v1/",
            dataset_id="tokenizer/smollm2-bpe",
            manifest_sha256="354a65ca1bd51076f972205fe1fbb8f261c6a022787be84f3bbae4aa13d3c529",
            tokenizer=None,
        )
    )

    assert reference.family == "tokenizer"
    assert not reference.is_a_corpus_a_run_may_read


def test_retiring_a_reference_leaves_it_registered() -> None:
    """Mutation: drop the superseded version from the file instead of flagging it.

    ``pretrain/fineweb-edu-1b`` is published at v2 and v6 and only v6 is current, which is its
    owner's answer rather than anything computable -- the two share a family and a tokenizer
    and pin the same digest. Deleting v2 would make the registry disagree with the bucket and
    leave any record naming it unresolvable, so the flag separates "exists" from "offered",
    exactly as ``RegisteredDatasetRelease`` already does for ``dolma-2026-07``.

    Defaulted false so that every entry written before the flag existed still means what it
    meant, and so retiring one is a deliberate line rather than an omission.
    """
    assert PublishedDatasetReference.model_validate(reference_payload()).retired is False

    retired = PublishedDatasetReference.model_validate(reference_payload(retired=True))

    assert retired.retired is True
    assert retired.is_a_corpus_a_run_may_read, (
        "retirement is about which version the form offers and not about what the dataset is; "
        "conflating them would make un-retiring an entry a safety question as well"
    )


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
