"""What makes a written checkpoint a resumable one, and what makes one unusable.

Two Phase 4 checks live here and they are opposite halves of the same property: a
checkpoint is resumable only once its marker exists, and an incomplete checkpoint is
ignored. Both are easy to pass by accident and easy to break silently, because every
unusable state still looks like a directory with a large file in it.

The store this exercises against attests its own digest over the bytes it received, the
way S3 does, rather than echoing whatever the writer claimed. That distinction is what
makes it possible to write a payload and a marker that disagree, which is the case a fake
built the obvious way cannot express -- and it is the case that a retry after a half-
finished commit actually produces.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from edullm_platform.checkpoints import (
    CHECKSUM_ALGORITHM,
    MARKER_OBJECT,
    CheckpointState,
    UnreadableCheckpointError,
    commit_checkpoint,
    crc32c,
    inspect_checkpoint,
    olmo_core_checkpoint_shape,
    resumable_checkpoint,
    success_marker_bytes,
)
from edullm_platform.contracts.results import CheckpointNotResumableError
from tests.fake_object_store import STORED_AT, FakeObjectStore, throttled

BUCKET = "sbsandbox-intern-edullm-outputs"
RUN_ID = "run_019fab9d-d1d0-7009-935f-b0189a9c8a86"
PREFIX = f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/step-20/"
PAYLOAD = b"the weights, or something the size of them"
WRITTEN_AT = STORED_AT

FakeStore = FakeObjectStore


def committed() -> FakeStore:
    store = FakeStore()
    commit_checkpoint(store, prefix=PREFIX, step=20, payload=PAYLOAD, created_at=WRITTEN_AT)
    return store


# ---------------------------------------------------------------------------------------
# Committing
# ---------------------------------------------------------------------------------------


def test_the_payload_is_written_before_the_marker_that_certifies_it() -> None:
    """Mutation: swap the two put_object calls.

    This is the whole protocol and the mutation passes every other test in this file. An
    interruption between the two writes is the case that decides which order is correct:
    payload-then-marker leaves a payload nobody certified, which is refused; marker-then-
    payload leaves a marker certifying nothing, which reads as a checkpoint that exists.

    A spot reclaim or an OOM kill between two S3 calls is not hypothetical on a GPU
    instance, and the reversed order fails in the direction that loses work silently.
    """
    store = committed()

    assert store.written_keys == [
        f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt",
        f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/{MARKER_OBJECT}",
    ]


def test_every_write_asks_the_store_to_attest_a_digest_over_what_it_received() -> None:
    """Mutation: drop ChecksumAlgorithm. It shipped once already, in the lineage handler.

    Here it is load-bearing rather than merely good practice. Without the store's own
    digest there is nothing to compare the marker's claim against, so the reader would
    have to either trust the marker or refuse everything -- and the first of those is the
    defect the verification exists to prevent.
    """
    store = committed()

    assert store.writes, "a test over every write must observe at least one"
    assert all(write["ChecksumAlgorithm"] == CHECKSUM_ALGORITHM for write in store.writes)


def test_the_marker_certifies_the_digest_of_the_bytes_that_were_actually_written() -> None:
    """Mutation: digest anything other than the payload -- the manifest, a re-encoding.

    Recomputed here from the payload rather than read from the manifest the function
    returned, so the two sides of the comparison do not come from the same place.
    """
    store = committed()
    marker = json.loads(store.objects[f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/{MARKER_OBJECT}"]["Body"])

    assert marker["sha256"] == f"sha256:{hashlib.sha256(PAYLOAD).hexdigest()}"
    assert marker["bytes"] == len(PAYLOAD)
    assert marker["payload"] == "model.pt"
    assert marker["step"] == 20


def test_committing_the_same_checkpoint_twice_produces_byte_identical_markers() -> None:
    """Mutation: put a clock in the function, or serialize without sorting the keys.

    Two attempts that wrote the same weights at the same step should be comparable by
    their markers and not only by a digest computed over them, and a marker whose bytes
    move on every call cannot be compared at all.
    """
    first = success_marker_bytes(
        step=20, payload_name="model.pt", digest="sha256:" + "a" * 64,
        size_bytes=41, created_at=WRITTEN_AT,
    )
    second = success_marker_bytes(
        step=20, payload_name="model.pt", digest="sha256:" + "a" * 64,
        size_bytes=41, created_at=WRITTEN_AT,
    )

    assert first == second


def test_the_marker_lands_inside_the_prefix_it_certifies() -> None:
    """Mutation: write the marker beside the checkpoint rather than inside it.

    CheckpointManifest already refuses a success marker outside its own prefix, so the
    mutation is caught -- but only because this function builds a manifest at all. A
    commit that returned nothing would place the object and never reach the validator.
    """
    manifest = commit_checkpoint(
        committed(), prefix=PREFIX, step=20, payload=PAYLOAD, created_at=WRITTEN_AT
    )

    assert manifest.success_marker_uri == PREFIX + MARKER_OBJECT
    assert manifest.is_resumable


def test_a_checkpoint_with_no_payload_is_refused_rather_than_certified() -> None:
    """Mutation: allow an empty body.

    A zero-byte payload with a marker beside it is the most convincing unusable checkpoint
    available: it has the right names in the right places and restores nothing.
    CheckpointManifest's ``size_bytes > 0`` would catch it one layer later, after both
    objects were already in the bucket.
    """
    with pytest.raises(UnreadableCheckpointError, match="certifies nothing"):
        commit_checkpoint(
            FakeStore(), prefix=PREFIX, step=20, payload=b"", created_at=WRITTEN_AT
        )


@pytest.mark.parametrize(
    "prefix",
    ["https://example/x/", f"s3://{BUCKET}/teams/platform/no-trailing-slash", ""],
    ids=["not-s3", "no-trailing-slash", "empty"],
)
def test_a_prefix_that_is_not_a_checkpoint_location_is_refused(prefix: str) -> None:
    """Mutation: concatenate the object name onto whatever was passed.

    Without the trailing slash the payload key becomes ``step-20model.pt``, a sibling of
    the prefix rather than a member of it -- and the marker lands beside it, so the pair
    is self-consistent and invisible until somebody lists the bucket.
    """
    with pytest.raises(UnreadableCheckpointError):
        commit_checkpoint(
            FakeStore(), prefix=prefix, step=20, payload=PAYLOAD, created_at=WRITTEN_AT
        )


# ---------------------------------------------------------------------------------------
# Reading back: the two checks, and the states between them
# ---------------------------------------------------------------------------------------


def test_a_committed_checkpoint_reads_back_as_resumable() -> None:
    """The round trip, which every negative case below is a departure from."""
    inspected = inspect_checkpoint(committed(), prefix=PREFIX)

    assert inspected.state is CheckpointState.COMMITTED
    assert inspected.is_resumable
    assert inspected.manifest is not None
    assert inspected.manifest.step == 20
    assert inspected.manifest.size_bytes == len(PAYLOAD)
    assert inspected.manifest.checksum == f"sha256:{hashlib.sha256(PAYLOAD).hexdigest()}"
    assert inspected.manifest.resume_reference().uri == PREFIX


def test_a_payload_with_no_marker_beside_it_is_not_resumable() -> None:
    """Check: a checkpoint is resumable only after its success marker exists.

    Mutation: return a manifest whenever the payload is there. That is the natural reading
    of "read the checkpoint", it passes the round-trip test above, and it makes every
    interrupted commit look like a complete one -- which is the exact failure the protocol
    was ordered to prevent.
    """
    store = FakeStore()
    store.put(f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt", PAYLOAD)

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.UNCOMMITTED
    assert inspected.manifest is None
    assert "did not finish" in inspected.detail


def test_a_marker_with_no_payload_beside_it_is_not_resumable() -> None:
    """Check: an incomplete checkpoint is ignored, from the other direction.

    Mutation: trust the marker and skip the head. A marker alone is what a lifecycle rule
    that deleted large objects, or a commit written in the wrong order, leaves behind --
    and it is the shape that reads most convincingly as a finished checkpoint, because the
    only object a reader looks for is present.
    """
    store = committed()
    del store.objects[f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt"]

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.ORPHANED
    assert inspected.manifest is None


def test_a_marker_certifying_bytes_the_store_does_not_hold_is_refused() -> None:
    """Mutation: compare nothing, and take the marker's word for the digest.

    This is the case the unconditional write makes reachable and is the reason the reader
    verifies at all. An attempt dies after its payload and before its marker; the retry
    writes a different payload and its own marker; if the first payload had somehow
    survived, the marker would certify bytes nobody can restore. Verified rather than
    prevented, because preventing it with a write-once rule would refuse the retry's
    payload and keep the dead attempt's.
    """
    store = committed()
    store.put(f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt", b"different weights")

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert inspected.manifest is None
    assert "different bytes" in inspected.detail


def test_a_payload_the_store_will_not_attest_is_refused_rather_than_assumed_good() -> None:
    """Mutation: treat a missing ChecksumSHA256 as agreement.

    That is the tempting shortcut -- there is nothing to disagree with -- and it quietly
    turns verification off for every object written without ChecksumAlgorithm. The reader
    would then be trusting the marker again, which is the state this whole mechanism
    exists to leave.
    """
    store = committed()
    store.put(
        f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt", PAYLOAD, attest=False
    )

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert "cannot be verified" in inspected.detail


def test_a_multipart_digest_is_not_compared_as_though_it_were_the_object_digest() -> None:
    """Mutation: read ChecksumSHA256 without looking at ChecksumType.

    A composite value is the digest of concatenated part digests, so it never equals the
    SHA-256 of the object. Compared naively, every multipart checkpoint reads as corrupt
    -- which fails closed but sends the reader looking for a corruption that is not there,
    and a large checkpoint is exactly the one that gets uploaded in parts.
    """
    store = committed()
    store.put(
        f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt", PAYLOAD, composite=True
    )

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert "whole-object" in inspected.detail


def test_the_crc32c_this_module_computes_is_the_one_the_published_constant_names() -> None:
    """The check value every CRC-32C implementation is measured against.

    CRC-32C is not in the standard library, so this module carries sixteen lines of
    table-driven arithmetic rather than a dependency the admission validator's zip would
    have to hold. That trade is only defensible if the arithmetic is checked against
    something outside this repository, and 0xE3069283 over ``123456789`` is that something.
    A transposed constant or an unreflected polynomial produces a plausible-looking value
    that agrees with nothing S3 computes, and every checkpoint would read as corrupt.
    """
    assert crc32c(b"123456789") == 0xE3069283
    assert crc32c(b"") == 0x00000000
    assert crc32c(b"a") == 0xC1D04330


def test_a_checkpoint_the_store_attests_with_crc32c_is_read_rather_than_called_corrupt() -> None:
    """THE ONE THIS CHANGE EXISTS FOR. Mutation: read only ChecksumSHA256.

    For a multipart upload S3 supports SHA-256 as a composite checksum only; full-object
    checksums on multipart are available for CRC-32, CRC-32C and CRC-64/NVME and nothing
    else. ``_attested_digest`` refuses a composite value on purpose, so under the previous
    SHA-256 write path every checkpoint large enough for a managed transfer to go multipart
    -- above 8 MB, by boto3's default -- read as CORRUPT, and a resuming run would have
    thrown away its own good checkpoint and started from step zero.

    Nothing caught it because the only checkpoint the platform ever wrote came from a demo
    that holds the payload in memory and calls put_object, which is a single part.
    """
    store = committed()

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.COMMITTED
    assert inspected.manifest is not None
    # The store was asked for CRC32C and attested only that, so a reader still looking for
    # a SHA-256 attestation finds nothing and reports the payload as unverifiable.
    head = store.head_object(Key=f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt")
    assert "ChecksumCRC32C" in head
    assert "ChecksumSHA256" not in head


def test_a_marker_carrying_no_counterpart_for_what_the_store_attests_is_refused() -> None:
    """Mutation: fall back to the marker's sha256 when the attested algorithm is missing.

    That comparison cannot fail, because a CRC32C and a SHA-256 of the same bytes are
    different lengths and different functions -- so the fallback would report every
    CRC32C-attested checkpoint as corrupt, or, written the other way round, would accept
    one without comparing anything at all. Neither is a verification.
    """
    store = committed()
    marker_key = f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/{MARKER_OBJECT}"
    marker = json.loads(store.objects[marker_key]["Body"])
    del marker["crc32c"]
    store.put(marker_key, json.dumps(marker, sort_keys=True, separators=(",", ":")).encode())

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert "no readable crc32c" in inspected.detail


def test_a_crc32c_that_disagrees_with_the_marker_is_still_caught() -> None:
    """The property the whole comparison exists for, in the new algorithm.

    Switching the write path would be worth nothing if the reader stopped detecting a
    marker and a payload that came from different attempts, which is the state a retry
    after a half-finished commit produces.
    """
    store = committed()
    store.put(
        f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt",
        PAYLOAD + b" but different",
        algorithm="CRC32C",
    )

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert "describe different bytes" in inspected.detail


def test_the_manifest_records_a_sha256_even_though_the_store_attested_a_crc32c() -> None:
    """``CheckpointManifest.checksum`` is typed ``Sha256Digest`` and must stay honest.

    Widening the contract to admit a CRC32C would regenerate four proof bundles for a field
    nothing reads as bytes; storing a CRC32C in a field named and patterned for a SHA-256
    would satisfy the type by lying about it. The marker carries a real SHA-256 of the
    payload, so that is what the manifest records.
    """
    store = committed()

    manifest = inspect_checkpoint(store, prefix=PREFIX).manifest

    assert manifest is not None
    assert manifest.checksum == f"sha256:{hashlib.sha256(PAYLOAD).hexdigest()}"


def _library_checkpoint(step: int, names: list[str]) -> FakeStore:
    """A prefix holding a step directory the way OLMo-core's checkpointer writes one.

    No ``_SUCCESS`` anywhere, because the library writes none and will not be made to.
    """
    store = FakeStore()
    for name in names:
        store.put(
            f"teams/platform/runs/{RUN_ID}/checkpoints/step{step}/{name}",
            b"tensor bytes",
            algorithm="CRC32C",
        )
    return store


def test_a_directory_olmo_core_would_load_is_committed_and_says_which_shape() -> None:
    """THE SHAPE A REAL TRAINING RUN WRITES. Mutation: keep requiring one payload object.

    ``_sole_payload`` resolves a prefix holding exactly one non-marker object and refuses
    otherwise, so a directory-shaped checkpoint was CORRUPT by construction -- and a
    directory is what OLMo-core writes. The rules here are the library's own
    ``dir_is_checkpoint``, read out of its source rather than invented, so a checkpoint this
    accepts is one its loader accepts.
    """
    store = _library_checkpoint(200, ["train/rank0.pt", "model_and_optim/.metadata", ".metadata.json"])

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.state is CheckpointState.COMMITTED
    assert "step200" in inspected.detail
    assert "model, optimizer and trainer state" in inspected.detail
    assert inspected.manifest is not None
    assert inspected.manifest.step == 200
    # No marker of ours exists, and pointing at one would name a file the library neither
    # writes nor reads.
    assert inspected.manifest.success_marker_uri is None


def test_a_weights_only_directory_is_committed_and_says_the_trainer_starts_cold() -> None:
    """Mutation: report both shapes as plain COMMITTED.

    ``.metadata`` alone restores weights and starts the trainer cold; the three-file shape
    continues the run. A verifier that did not distinguish them would let a twelve-hour
    run's second attempt believe it was continuing when it was starting over with warm
    weights -- correct-looking, expensive, and invisible.
    """
    store = _library_checkpoint(400, [".metadata"])

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.state is CheckpointState.COMMITTED
    assert "no trainer state" in inspected.detail


def test_a_half_written_directory_is_uncommitted_rather_than_committed() -> None:
    """The case the marker protocol existed for, now covered by the library's own rules.

    An attempt reclaimed mid-write leaves a directory missing one of the three files. It
    fails ``dir_is_checkpoint`` for exactly the reason it would fail the loader, so
    resuming from it is refused here rather than at the point a trainer tries to read it.
    """
    store = _library_checkpoint(600, ["train/rank0.pt", ".metadata.json"])

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.state is CheckpointState.UNCOMMITTED
    assert inspected.manifest is None


def test_the_newest_step_directory_is_the_one_resumed_from() -> None:
    """Mutation: take the first, or the lowest, or all of them.

    This platform requires keeping every checkpoint -- the workload role is denied the delete
    a prune starts with, so a trainer configured to prune stops instead -- which means a long
    run accumulates complete directories that are all irrelevant except the last. Sorted as
    integers rather than as strings, because step1000 sorts before step200 as text.
    """
    store = _library_checkpoint(200, ["train/rank0.pt", "model_and_optim/.metadata", ".metadata.json"])
    for name in ("train/rank0.pt", "model_and_optim/.metadata", ".metadata.json"):
        store.put(
            f"teams/platform/runs/{RUN_ID}/checkpoints/step1000/{name}",
            b"later tensor bytes",
            algorithm="CRC32C",
        )

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.manifest is not None
    assert inspected.manifest.step == 1000


FULL_SHAPE = ("train/rank0.pt", "model_and_optim/.metadata", ".metadata.json")


def _add_step(store: FakeStore, step: int, names: tuple[str, ...] | list[str]) -> None:
    for name in names:
        store.put(
            f"teams/platform/runs/{RUN_ID}/checkpoints/step{step}/{name}",
            b"tensor bytes",
            algorithm="CRC32C",
        )


def test_a_torn_newest_directory_does_not_hide_the_complete_one_below_it() -> None:
    """The state an instance kill leaves, and the state this got wrong.

    A reclaimed attempt dies part-way through writing step 400, so the prefix holds a
    complete step 200 and a step 400 missing two of its three files. OLMo-core resumes:
    ``find_checkpoints`` skips a directory failing ``dir_is_checkpoint`` and
    ``latest_checkpoint`` takes the highest of what is left, so the second attempt
    continues from step 200. Reading only the highest step answered UNCOMMITTED here,
    which is this module telling an operator there is nothing to resume from while the
    trainer beside them resumes.
    """
    store = FakeStore()
    _add_step(store, 200, FULL_SHAPE)
    _add_step(store, 400, ["train/rank0.pt"])

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.state is CheckpointState.COMMITTED
    assert inspected.manifest is not None
    assert inspected.manifest.step == 200
    assert inspected.manifest.uri.endswith("/step200/")
    # Sized from step 200 alone. Folding the torn directory's objects in would report a
    # checkpoint larger than the one a resume actually reads.
    assert inspected.manifest.size_bytes == len(FULL_SHAPE) * len(b"tensor bytes")


def test_the_newer_unfinished_directory_is_named_rather_than_passed_over_silently() -> None:
    """Mutation: return the older checkpoint and say nothing about the newer one.

    Resuming from step 200 when step 400 exists is right and is also a loss of two
    hundred steps of GPU time. An operator reading this has to be able to tell that case
    from a run that simply had not reached step 400, because they look identical in the
    manifest and only one of them is worth investigating.
    """
    store = FakeStore()
    _add_step(store, 200, FULL_SHAPE)
    _add_step(store, 400, ["train/rank0.pt"])

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert "step200" in inspected.detail
    assert "step400" in inspected.detail
    assert "unfinished" in inspected.detail


#: What a lost host actually leaves, taken from run_019fbe1f-b84f-703a-8eb8-2b4504232948.
#: The instance was terminated at step 100 immediately after this object was written and
#: before the first ``model_and_optim`` shard started, and ``step100/`` then held exactly one
#: object of 15,317 bytes. Not a hypothesis about which file might be missing: the
#: checkpointer writes the train state, then the shards, then ``.metadata.json``, so this is
#: the widest window a kill can land in and the shape it leaves most often.
TORN_BY_A_LOST_HOST = ("train/rank0.pt",)


def test_the_shape_a_lost_host_leaves_is_not_one_the_loader_accepts() -> None:
    """The observed torn shape, asserted against the shape reader directly.

    THE READ PATH BEING RIGHT ABOUT THIS IS WHAT MADE THE WRITE PATH'S FAILURE HARD TO SEE.
    The tests above establish that a resume skips this directory and continues from the
    complete one below it, which is correct and was proven on a real run. It is also only
    half of what the shape decides. The other half is that the trainer reaches that step
    number again and ``Checkpointer._prepare_dir`` raises ``FileExistsError`` on a directory
    that is not empty, so the run dies at the step it resumed past -- deterministically, on
    every attempt.

    Asserted here rather than only through ``inspect_checkpoint`` because this function is
    the single judgement both halves rest on. ``.edullm/train_on_corpus.py`` clears exactly
    the directories this returns None for, using OLMo-core's own ``dir_is_checkpoint``, so a
    reading of "torn" that drifted from the library's would put the repair and the loader out
    of step with each other and leave the run failing again.
    """
    assert olmo_core_checkpoint_shape(TORN_BY_A_LOST_HOST) is None

    # And the same directory once the write finishes. The three names are what the loader
    # requires, so a checkpoint is one object away from torn until the last of them lands.
    assert (
        olmo_core_checkpoint_shape(
            (*TORN_BY_A_LOST_HOST, "model_and_optim/.metadata", ".metadata.json")
        )
        == "model, optimizer and trainer state"
    )


def test_the_torn_directory_is_named_by_the_step_it_poisons() -> None:
    """The operator's version of the same fact, and the number they need.

    A resume that continued from step 200 and a run that never reached step 400 read the
    same in a manifest. The step of the directory that did not finish is what tells them
    apart, and it is also the step number a rewrite has to clear before the retry can save
    there, so it is the one number worth having in the detail line.
    """
    store = FakeStore()
    _add_step(store, 50, FULL_SHAPE)
    _add_step(store, 100, TORN_BY_A_LOST_HOST)

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.state is CheckpointState.COMMITTED
    assert inspected.manifest is not None
    assert inspected.manifest.step == 50
    assert "step100 is newer but unfinished" in inspected.detail


def test_a_prefix_whose_every_step_directory_is_torn_is_still_uncommitted() -> None:
    """The half the fix must not give away.

    Skipping past an unfinished directory is only correct while something below it is
    finished. A prefix holding nothing the loader accepts is a run that died before its
    first whole checkpoint, and resuming from it is the failure the marker protocol was
    written for.
    """
    store = FakeStore()
    _add_step(store, 200, ["train/rank0.pt"])
    _add_step(store, 400, [".metadata.json"])

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.state is CheckpointState.UNCOMMITTED
    assert inspected.manifest is None
    # Named by the newest, because that is the write that did not finish.
    assert "step400" in inspected.detail


def test_a_listing_that_arrives_in_pages_is_read_to_the_end() -> None:
    """Mutation: read the first page and stop.

    ListObjectsV2 answers at most a thousand keys and reports the rest through
    ``IsTruncated``. Thirteen objects per checkpoint puts that boundary at seventy-six of
    them, which the twelve-hour bound on ``olmo-core-train-1gpu`` reaches at a nine-minute
    save interval. It is not the oldest checkpoints that go missing either: S3 orders keys
    lexicographically, so ``step1000/`` sorts before ``step2000/`` sorts before ``step200/``
    and the cut falls in an arbitrary place in the step sequence. Here the first page holds
    step 1000, so a reader that stopped there answers with a real checkpoint at a plausible
    step and is wrong by a thousand steps rather than visibly empty.
    """
    store = FakeStore()
    for step in (200, 1000, 2000):
        _add_step(store, step, FULL_SHAPE)
    store.page_size = len(FULL_SHAPE)

    inspected = inspect_checkpoint(
        store, prefix=f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/"
    )

    assert inspected.manifest is not None
    assert inspected.manifest.step == 2000


def test_a_marker_whose_byte_count_disagrees_with_the_store_is_refused() -> None:
    """Mutation: drop the length comparison because the digest already covers it.

    It does, for any payload the marker was actually computed over. The comparison earns
    its place on the marker that was written by hand or by a different tool, where the
    digest may be of something else entirely and a length that disagrees is the cheaper
    signal to read in a log.
    """
    store = committed()
    key = f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/{MARKER_OBJECT}"
    marker = json.loads(store.objects[key]["Body"])
    marker["bytes"] = len(PAYLOAD) + 1
    store.put(key, json.dumps(marker).encode())

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert "bytes" in inspected.detail


def test_an_empty_prefix_is_absent_rather_than_incomplete() -> None:
    """Mutation: collapse ABSENT into UNCOMMITTED.

    Both refuse to resume, so a caller that only asks ``is_resumable`` cannot tell. The
    difference is what gets logged: nothing there is an ordinary first run, and a payload
    with no marker is a run that died most of the way through and is worth saying.
    """
    inspected = inspect_checkpoint(FakeStore(), prefix=PREFIX)

    assert inspected.state is CheckpointState.ABSENT
    assert inspected.manifest is None


def test_a_marker_naming_no_payload_among_several_candidates_is_refused() -> None:
    """Mutation: pick the first object that is not the marker.

    The fallback that resolves an unnamed payload is only sound when there is exactly one
    thing it could mean. Picking one of several would head an arbitrary shard, compare its
    digest against a marker describing a different file, and report corruption in a
    checkpoint that is merely sharded.
    """
    store = FakeStore()
    base = f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/"
    store.put(base + "shard-0.pt", PAYLOAD)
    store.put(base + "shard-1.pt", PAYLOAD)
    store.put(
        base + MARKER_OBJECT,
        json.dumps({"step": 20, "sha256": hashlib.sha256(PAYLOAD).hexdigest()}).encode(),
    )

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.CORRUPT
    assert "exactly one" in inspected.detail


@pytest.mark.parametrize("key", ["model.pt", MARKER_OBJECT])
def test_a_store_failure_that_is_not_a_missing_object_is_raised_rather_than_read_as_absence(
    key: str,
) -> None:
    """Mutation: catch every exception and report the object as missing.

    A throttle or a network error would then be reported as "no checkpoint here", and a
    resuming job would start again from step zero on a bad afternoon -- spending the whole
    training budget a second time and looking, from the outside, exactly like a first run.
    """
    store = committed()
    store.refuse[f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/{key}"] = throttled()

    with pytest.raises(RuntimeError, match="SlowDown"):
        inspect_checkpoint(store, prefix=PREFIX)


def test_the_manifest_is_populated_for_the_committed_state_and_for_no_other() -> None:
    """Read over every state at once, so a new one cannot be added without a decision.

    Mutation: return a manifest alongside a non-committed state. Every caller that reaches
    for ``manifest`` would then get one for a checkpoint that must not be resumed, and the
    check would depend on each caller remembering to test the state first.
    """
    stores: dict[CheckpointState, FakeStore] = {}
    base = f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/"

    stores[CheckpointState.ABSENT] = FakeStore()

    uncommitted = FakeStore()
    uncommitted.put(base + "model.pt", PAYLOAD)
    stores[CheckpointState.UNCOMMITTED] = uncommitted

    orphaned = committed()
    del orphaned.objects[base + "model.pt"]
    stores[CheckpointState.ORPHANED] = orphaned

    corrupt = committed()
    corrupt.put(base + "model.pt", b"different weights")
    stores[CheckpointState.CORRUPT] = corrupt

    stores[CheckpointState.COMMITTED] = committed()

    assert set(stores) == set(CheckpointState), (
        "every state a reader can report needs a case here, or a new one arrives untested"
    )
    for state, store in stores.items():
        inspected = inspect_checkpoint(store, prefix=PREFIX)
        assert inspected.state is state, f"{state} was set up wrong"
        assert (inspected.manifest is not None) == (state is CheckpointState.COMMITTED)
        assert inspected.is_resumable == (state is CheckpointState.COMMITTED)


def test_the_resume_helper_answers_nothing_for_every_state_that_is_not_committed() -> None:
    """Mutation: raise instead of returning None.

    No usable checkpoint is the ordinary condition at the start of every first run. A
    resume path that had to catch an exception to start from scratch would end up catching
    the ones that mean something else too -- including the throttle above.
    """
    store = FakeStore()
    store.put(f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/model.pt", PAYLOAD)

    assert resumable_checkpoint(store, prefix=PREFIX) is None
    assert resumable_checkpoint(committed(), prefix=PREFIX) is not None


def test_a_manifest_with_no_marker_refuses_to_produce_a_resume_reference() -> None:
    """The contract's own half of the check, reached from this module's output.

    Mutation: none here -- this asserts that the two halves join. ``resume_reference``
    raises for a manifest carrying no success marker, and this module never produces one,
    so the refusal is a backstop for a manifest that arrived from somewhere else.
    """
    inspected = inspect_checkpoint(committed(), prefix=PREFIX)
    assert inspected.manifest is not None
    uncertified = inspected.manifest.model_copy(update={"success_marker_uri": None})

    assert uncertified.is_resumable is False
    with pytest.raises(CheckpointNotResumableError):
        uncertified.resume_reference()


# ---------------------------------------------------------------------------------------
# The one marker that was written before this module existed
# ---------------------------------------------------------------------------------------


def test_the_marker_the_first_gpu_training_run_wrote_still_reads_as_committed() -> None:
    """Mutation: require the schema_version, the payload name, or the sha256: prefix.

    Written out in the shape the live run produced, hex digest and all, because that
    object is in the bucket and is the only real checkpoint this platform has made. A
    reader that could not read it would mean the module and the runs disagreed from the
    day it was written, and the disagreement would be found by whoever next tried to
    resume rather than here.

    The tolerance is bounded: this reads a marker, and nothing writes one. Every marker
    ``commit_checkpoint`` produces carries all three fields.
    """
    store = FakeStore()
    base = f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/"
    store.put(base + "model.pt", PAYLOAD)
    store.put(
        base + MARKER_OBJECT,
        json.dumps(
            {
                "step": 20,
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "bytes": len(PAYLOAD),
            }
        ).encode(),
    )

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.COMMITTED
    assert inspected.manifest is not None
    assert inspected.manifest.step == 20
    assert inspected.manifest.created_at == WRITTEN_AT, (
        "a marker that recorded no time falls back to when the store says the payload "
        "landed, which is the more defensible of the two anyway"
    )


def test_a_marker_that_is_not_json_is_refused_loudly_rather_than_read_as_absent() -> None:
    """Mutation: swallow the decode error and report no marker.

    A marker nobody can parse is a checkpoint in an unknown state, and reporting it as
    absent would send a resuming job back to step zero while a perfectly good payload sat
    beside it.
    """
    store = committed()
    store.put(
        f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/{MARKER_OBJECT}", b"not json at all"
    )

    with pytest.raises(UnreadableCheckpointError, match="not JSON"):
        inspect_checkpoint(store, prefix=PREFIX)
