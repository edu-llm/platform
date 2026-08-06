"""The published tokenizers this platform can build an OLMo-core model for.

**ONE MAP, AND IT MOVED HERE BECAUSE A SECOND READER ARRIVED.** It lived in
``tools/build_gpu_training_submission.py``, which is a maintainer's generator, and that was
the right home while the generator was the only thing that consulted it. ``edullm data``
consults it now, on a laptop, to answer the one column a researcher choosing a corpus cannot
get anywhere else -- whether the corpus they pick will actually run. An installed wheel
carries no ``tools/``, so leaving the map there would have meant a second copy inside the
package, and a second copy of this map is the exact failure the map exists to prevent:
``config/datasets.yaml`` would offer a corpus one copy could build and the other could not,
and the disagreement would be invisible until a container exited 69.

**WHAT A MISSING KEY COSTS, WHICH IS WHY THIS IS WORTH A MODULE.** A corpus whose tokenizer
is not a key here is registered, resolvable, in a trainable family, at a corpus payload
profile and not retired, so every check this platform makes admits it. What it reaches is
``OLMo-core/.edullm/train_on_corpus.py`` looking its tokenizer up in that repository's own
copy of this map, failing, and exiting 69 with
``THIS_IMAGE_HAS_NO_CONFIG_FOR_THAT_TOKENIZER``. That is after the GPU has been allocated.
Five registered corpora are in that state today and
``tests/test_submission_form_options.py`` derives the list rather than carrying one.

**THIS MAP MUST NOT LEAD OLMo-core's.** A key added here before the matching line lands in
that repository offers a corpus every image refuses. The ordering is not optional: the
OLMo-core change lands first and this ships after.
"""

from __future__ import annotations

from typing import Final

__all__ = ["THE_CONTAINERS_REFUSAL", "TOKENIZERS"]

#: What the container prints and the code it exits with when it is handed a corpus whose
#: tokenizer it has no configuration for. Named here rather than written into the verb's
#: prose so that the sentence a researcher reads and the sentence in a test are one string,
#: and so that a reader searching for either finds the map that decides it.
#:
#: A CONSTANT ABOUT ANOTHER REPOSITORY'S BEHAVIOUR, WHICH IS AN UNUSUAL THING TO CARRY AND IS
#: THE POINT. This platform cannot enforce the lookup and cannot see it fail; what it can do
#: is tell somebody, before they spend an approval, exactly what they are about to meet.
THE_CONTAINERS_REFUSAL: Final = "THIS_IMAGE_HAS_NO_CONFIG_FOR_THAT_TOKENIZER"

#: Keyed by the dataset id a corpus names in its own ``groups[].depends_on[]`` entry with
#: role ``tokenizer``, and valued by the expression the training program evaluates.
#:
#: NOT A DEFAULT AND NOT A FALLBACK. A constant here would be right for one corpus and
#: silently wrong for another: a byte corpus read with a dolma2 vocab puts every id inside an
#: embedding sized 100,352, so nothing raises and the loss curve is merely bad. The upstream
#: family file turns its own family-wide tokenizer default off for exactly this reason, in
#: writing. So the corpus states its tokenizer, the registry carries it, `batch_submit_request`
#: sends it, and this map turns it into a config.
#:
#: ONE ENTRY, NOT TWO, AND THE MISSING ONE IS A MEASUREMENT RATHER THAN AN OVERSIGHT.
#: ``tokenizer/bytes-utf8`` is published and sealed and `pretrain/lean4-mathlib-bytes` depends
#: on it, but OLMo-core has no byte tokenizer: `TokenizerConfig` offers dolma2, dolma2_sigdig,
#: gpt_neox_olmo_dolma_v1_5, gpt2 and from_hf, and nothing under `olmo_core/data/` mentions
#: bytes or utf8 at all -- read from the checkout at OLMO_CORE_CHECKOUT on 2026-08-01 and
#: confirmed against that repository's main branch.
#:
#: `TokenizerConfig` is a plain dataclass, so `TokenizerConfig(vocab_size=..., eos_token_id=...,
#: pad_token_id=...)` would construct one. That is not done here, because the three numbers
#: are facts about a published tokenizer and only two of them are guessable: a 256-entry
#: vocabulary of raw bytes has no room for an end-of-sentence id, so whatever
#: `tokenizer/bytes-utf8` does about that is something to read out of its own tokenizer.json
#: rather than to infer. An invented eos id is the quiet kind of wrong this whole map exists
#: to refuse.
#:
#: THREE ENTRIES NOW, AND THE ARGUMENT ABOVE SURVIVES FOR ONE OF THEM AND NOT THE OTHER TWO.
#: The paragraph beginning "ONE ENTRY, NOT TWO" is still exactly right about
#: `tokenizer/bytes-utf8` and was wrong to be read as covering the two entries beside it. The
#: symmetry is the trap: every tokenizer missing from this map fails the same way, so a reader
#: who diagnosed one concluded the same about the rest, and that reading is what kept
#: `pretrain/fineweb-edu-1b` unusable and sent the diagnosis to `edullm-data` to fix manifests
#: that were never broken. Only some of them are missing upstream features.
#: `tokenizer/bytes-utf8` still is. The other two are not, and each names an exact OLMo-core
#: equivalent rather than one recognised from its name.
#:
#: `tokenizer/smollm2-bpe` has one: `s3://edullm-data/tokenizer/smollm2-bpe/v1/dataset.json`
#: names `HuggingFaceTB/SmolLM2-135M` as its source in those words, and `TokenizerConfig.from_hf`
#: reproduces it. Called for real before this line was written, it reports vocab_size 49,152.
#:
#: `tokenizer/qwen25-vendored` has one too --
#: `s3://edullm-data/tokenizer/qwen25-vendored/v1/dataset.json` names
#: `https://huggingface.co/Qwen/Qwen2.5-0.5B` in `sources[].uri`, so the identifier below is
#: read out of the published tokenizer rather than recognised from its name, and `from_hf`
#: reproduces it.
#:
#: CALLED FOR REAL BEFORE THIS LINE WAS WRITTEN, against the checkout at OLMO_CORE_CHECKOUT on
#: 2026-08-02, it reports vocab_size 151,936, eos_token_id 151,643, pad_token_id 151,643 and
#: bos_token_id 151,643. `from_hf` takes those off the Hub's config.json rather than off
#: anything in the bucket, so what had to be established is that the vendored copy and the Hub
#: agree about ids, and it was checked rather than assumed: the vendored `tokenizer.json`
#: holds 151,643 model entries plus 22 added tokens reaching 151,664, every one of those id
#: assignments is identical to the Hub's, and the merge list matches once the two
#: serialisations of it are normalised. The files are not byte identical -- upstream now
#: writes merges as pairs rather than space-joined strings and spells the ByteLevel decoder's
#: defaults explicitly -- and neither difference moves an id. Its `tokenizer_config.json`
#: names `<|endoftext|>` as both eos and pad, which its own added_tokens put at 151,643, so
#: the bucket and the Hub state the same eos independently.
#:
#: 151,936 IS LARGER THAN THE 151,665 IDS THAT EXIST, which is Qwen's own padding rather than
#: a mismatch, and it matters in the safe direction: every id the corpus can hold is inside
#: the embedding. It also puts this corpus past uint16, so unlike dolma2's 100,278 its ids
#: could not have been written narrower -- which changes nothing about the hazard this map
#: exists for, because a uint32 shard read at OLMo-core's default still decodes into ids this
#: vocabulary accepts. The dtype comes from the manifest and is never inferred.
#:
#: WHAT THIS ENTRY COSTS THAT THE DOLMA2 ONE DOES NOT. `from_hf` fetches the tokenizer's
#: config from the HuggingFace Hub when the config is built, so a corpus resolved through it
#: needs network egress at container runtime that a dolma2 corpus does not. Batch hosts sit in
#: public subnets with allow-all egress and the payload is a few hundred kilobytes, so this is
#: a real dependency rather than a real risk -- but a Hub outage can now refuse a run that
#: dolma2 would have started, and that is worth knowing before it happens.
#:
#: AND THE HAZARD THIS MAP EXISTS FOR APPLIES TO SmolLM2 TOO, AT A DIFFERENT NUMBER. SmolLM2's
#: 49,152 fits in uint16 exactly as dolma2's 100,278 does, so a fineweb shard read without the
#: manifest's explicit dtype would be decoded two bytes at a time into in-range ids and a loss
#: curve that is merely bad. The dtype comes from the manifest and is never inferred, which is
#: what makes adding this safe.
#:
#: THE CONTAINER'S OWN COPY LIVES AT `OLMo-core/.edullm/train_on_corpus.py`, and on that
#: repository's `origin/main` -- commit d663baeb, fetched 2026-08-02 -- it holds
#: `tokenizer/dolma2-bpe` alone; the smollm2 entry exists there only on branch
#: `edullm/smollm2-tokenizer`. So a submitter picking the corpus either entry unlocks resolves
#: it, reaches the container, and is refused at the tokenizer lookup with
#: :data:`THE_CONTAINERS_REFUSAL` and exit 69. That refusal is loud, immediate and names the
#: tokenizer, so it costs minutes rather than a GPU day -- but it is a refusal, and the
#: ordering is not optional: the OLMo-core change lands first and this ships after.
TOKENIZERS: Final[dict[str, str]] = {
    "tokenizer/dolma2-bpe": "TokenizerConfig.dolma2()",
    "tokenizer/gigatoken-bpe": (
        "TokenizerConfig(vocab_size=100002, eos_token_id=100000, pad_token_id=100001, "
        "identifier=None)"
    ),
    "tokenizer/gigatoken-superbpe": (
        "TokenizerConfig(vocab_size=100002, eos_token_id=100000, pad_token_id=100001, "
        "identifier=None)"
    ),
    "tokenizer/qwen25-vendored": 'TokenizerConfig.from_hf("Qwen/Qwen2.5-0.5B")',
    "tokenizer/smollm2-bpe": 'TokenizerConfig.from_hf("HuggingFaceTB/SmolLM2-135M")',
}
