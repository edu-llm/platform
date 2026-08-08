"""Read what a training image contains, and record it.

THE FACTS THIS MEASURES ARE THE ONES TWO OF THE PLATFORM'S CLAIMS REST ON, AND UNTIL NOW
NOTHING MEASURED EITHER. ``edullm data`` told a researcher whether the corpus they picked would
start and derived it from ``src/edullm_platform/tokenizers.py``, which is about what this
platform can express rather than about what an image can build; they agreed until somebody added
two entries ahead of the matching lines in OLMo-core, and ``run_019fdd88-3ac4`` spent a GPU
allocation finding that out at exit 69. ``guides/olmo-core.md`` tells a researcher to write
``--model-factory olmo2_1B`` and nothing checked that any image has that factory, which would
have been exit 70 on the same path.

ONE PROBE FOR BOTH, AND FOR THE THIRD. What varies between them is a path and a parse;
everything expensive here -- getting into the image, deciding what a reading is worth, keeping
the record diffable -- is shared. :data:`VOCABULARIES` is the whole of the variation, so the next
member of :class:`~edullm_platform.contracts.image_contents.VocabularyName` costs one entry in
that tuple and one reader function, not another afternoon.

TWO WAYS TO READ IT, AND THE STRONG ONE IS A PROBE OF THE ASSEMBLED IMAGE.
``verify_image_accelerator.py`` sets out why at length for a different fact about an image and
the argument transfers whole: a claim about a repository's source protects exactly what that
source produces, and an image is not that -- a ``COPY`` that misses a file, an entrypoint
pinning an older checkout, or a BuildKit cache hit that reuses a layer without re-running it all
leave an image whose contents are not the tree's. ``--image-reference`` takes the strong reading.
``--repository-root`` takes the weak one off a checkout, for the ordinary case of a maintainer
with no Docker daemon and no registry session; the record says which was taken and
``ImageContentsReading`` refuses a digest beside the weak one.

WHY THE PROBE PARSES RATHER THAN IMPORTS. Importing would give both answers exactly, and would
also import ``olmo_core``, torch and a CUDA stack -- seconds to minutes, and a failure mode where
an image whose torch is unhappy reads as an image containing nothing. Every vocabulary here is
recoverable from source with ``ast`` and nothing executed, in both modes, and a source whose
shape has changed is refused loudly rather than read wrongly.

Like its siblings the first line it prints on a failure is a machine-readable reason, and nothing
it prints is derived from the image it ran: a caller's runner log is world readable and the image
reference names the account.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml

from edullm_platform.contracts.image_contents import (
    ImageContentsReading,
    ImageContentsRecord,
    ImageVocabulary,
    ReadingMethod,
    VocabularyName,
)

#: The module-level name holding the tokenizer map. The container looks its tokenizer up in
#: exactly this dict and exits 69 on a miss, so this is the name and not a name.
TOKENIZER_MAP_NAME: Final = "TOKENIZERS"

#: The class the container resolves ``--model-factory`` against, with ``getattr``. Same point:
#: this is the class and not a class.
FACTORY_CLASS_NAME: Final = "TransformerConfig"

#: Tried in order against ``docker run --entrypoint``, which resolves each against the image's
#: own PATH. The same list ``verify_image_accelerator.py`` uses and for its reasons.
INTERPRETERS: Final = ("python", "python3")

#: Prefixed so that anything the image prints on the way up cannot be mistaken for the answer,
#: exactly as the accelerator probe's sentinel is.
SENTINEL: Final = "EDULLM_CONTENTS_PROBE"

#: How long the probe may take. Generous next to the work it does, because the cost of being
#: tight is a red build on a correct image and the cost of being loose is seconds.
PROBE_TIMEOUT_SECONDS: Final = 120


class ProbeError(ValueError):
    def __init__(self, reason: str, guidance: str) -> None:
        self.reason = reason
        self.guidance = guidance
        super().__init__(reason)


def tokenizers_in(source: str) -> tuple[str, ...]:
    """The keys of the module-level tokenizer map, read out of source without running it.

    **A MAP THIS CANNOT READ IS AN ERROR AND NEVER AN EMPTY MAP**, which is the one thing that
    must not be got wrong here. An empty answer is written into the record and the record
    decides a verdict, so a parse that quietly returned nothing would mark every corpus
    unrunnable. Absent, unparseable and empty are three different findings and only one of them
    is a fact about the image.

    A key that is not a plain string literal is refused for the same reason. The container looks
    a runtime string up in this dict; a computed key is one this cannot resolve, and guessing at
    it would put a tokenizer in the record the image may not answer to.
    """
    for node in _parsed(source).body:
        if not isinstance(node, ast.Assign):
            continue
        if TOKENIZER_MAP_NAME not in [
            target.id for target in node.targets if isinstance(target, ast.Name)
        ]:
            continue
        if not isinstance(node.value, ast.Dict):
            raise ProbeError(
                "map_is_not_a_literal",
                f"{TOKENIZER_MAP_NAME} is no longer a dict literal, so its keys cannot be read "
                "without running the module. Read it by hand and record it, or restore the "
                "literal.",
            )
        keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise ProbeError(
                    "map_key_is_not_a_string",
                    f"{TOKENIZER_MAP_NAME} holds a key that is not a string literal. The "
                    "container looks a runtime string up in this map, so a computed key cannot "
                    "be recorded as one an image answers to.",
                )
            keys.append(key.value)
        return tuple(sorted(keys))

    raise ProbeError(
        "tokenizer_map_not_found",
        f"there is no module-level {TOKENIZER_MAP_NAME} in this file. If this repository's "
        "image trains no corpus it needs no reading of this vocabulary; leave it out rather "
        "than recording an empty one.",
    )


def model_factories_in(source: str) -> tuple[str, ...]:
    """Every public classmethod on the config class the container resolves a factory against.

    **WIDER THAN THE FACTORIES ANYBODY WOULD CALL, DELIBERATELY.** The container does
    ``getattr(TransformerConfig, name)`` and calls the result with ``vocab_size=``, so a name
    recorded here that is not really a factory fails inside the call and still exits 70 -- the
    platform has simply declined to predict that one. A name *missing* from here is refused at
    submission. So being narrow costs a refusal of a command that works, being generous costs
    one uncaught failure of a command that does not, and the second is the cheaper mistake.

    Classmethods only, which is what the surface actually is: the factories are all plain
    ``@classmethod`` defs in the class body, while ``build`` and ``with_rope_scaling`` are
    instance methods and the ``num_params`` family are properties. None of the latter would
    build a model, and none of them is worth recording as though it might.
    """
    for node in ast.walk(_parsed(source)):
        if not isinstance(node, ast.ClassDef) or node.name != FACTORY_CLASS_NAME:
            continue
        found = tuple(
            sorted(
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef)
                and not item.name.startswith("_")
                and any(
                    isinstance(decorator, ast.Name) and decorator.id == "classmethod"
                    for decorator in item.decorator_list
                )
            )
        )
        if not found:
            raise ProbeError(
                "factory_class_has_no_classmethods",
                f"{FACTORY_CLASS_NAME} holds no public classmethods, so either the factories "
                "have moved off the class body or this is not the class the container resolves "
                "against. Recording an empty vocabulary here would refuse every submission "
                "naming any factory.",
            )
        return found

    raise ProbeError(
        "factory_class_not_found",
        f"there is no {FACTORY_CLASS_NAME} in this file. If this repository's image trains no "
        "transformer it needs no reading of this vocabulary; leave it out rather than recording "
        "an empty one.",
    )


def _parsed(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise ProbeError(
            "source_unparseable",
            "the file read is not parseable Python, so what it holds cannot be read.",
        ) from exc


@dataclass(frozen=True)
class VocabularySource:
    """One vocabulary, where to find it, and how to read it.

    ``paths`` is candidates in order rather than one path, because the same names live in two
    places in an image and only one of them is authoritative. OLMo-core's Dockerfile copies the
    tree to ``/opt/olmo-core`` and then runs ``pip install .``, so the factory surface exists
    both as the copied ``src/`` tree and as the installed package -- and it is the installed one
    the interpreter would import. Both are tried, the first that reads wins, and the record says
    which, so a build whose install went stale is visible in review rather than averaged over.
    """

    kind: VocabularyName
    paths: tuple[str, ...]
    read: Callable[[str], tuple[str, ...]]


#: THE WHOLE OF WHAT VARIES BETWEEN ONE OF THESE AND THE NEXT. Adding a third is an entry here
#: and a reader above; everything else in this file is shared and everything in the record and
#: its guard test iterates :class:`VocabularyName`.
VOCABULARIES: Final = (
    VocabularySource(
        kind=VocabularyName.TOKENIZERS,
        # One path rather than a search. A probe that went looking would find a copy in a test
        # fixture as readily as the real one and could not tell which it had read.
        paths=(".edullm/train_on_corpus.py",),
        read=tokenizers_in,
    ),
    VocabularySource(
        kind=VocabularyName.MODEL_FACTORIES,
        paths=(
            "olmo_core/nn/transformer/config.py",
            "src/olmo_core/nn/transformer/config.py",
        ),
        read=model_factories_in,
    ),
)


def probe_script() -> str:
    """The program that runs inside the image, built from :data:`VOCABULARIES`.

    Stdlib only, no network, nothing written, and every failure reported as a type name rather
    than a message: an exception from somebody else's image could carry a path or a token into a
    world-readable log.

    IT HANDS BACK THE FILES AND NOT THE ANSWERS, WHICH IS THE POINT. Parsing inside the image
    would put a second reader of each vocabulary on the other side of a boundary nothing here can
    test, and one fact with two readers is the shape this whole change exists to remove. The
    readers above are the only ones, and they run on both paths.

    Each candidate is resolved against the working directory and then against every entry on
    ``sys.path``, which is how the installed copy of a package is found without importing it --
    importing ``olmo_core`` would pull in torch and a CUDA stack to answer a question about a
    file.
    """
    candidates = sorted({path for source in VOCABULARIES for path in source.paths})
    return f"""
import json, os, sys

report = {{"probe": "contents", "version": 1, "read": {{}}}}
roots = [os.getcwd()] + [entry for entry in sys.path if entry]
for candidate in {candidates!r}:
    for root in roots:
        try:
            with open(os.path.join(root, candidate), encoding="utf-8") as handle:
                report["read"][candidate] = handle.read()
            break
        except OSError:
            continue
        except BaseException as exc:
            report.setdefault("errors", {{}})[candidate] = type(exc).__name__
            break

sys.stdout.write("{SENTINEL} " + json.dumps(report) + chr(10))
"""


def probe_command(image_reference: str, interpreter: str) -> list[str]:
    """The container this runs, which holds no credential and reaches no network.

    ``--network none`` because the probe opens files and an image is free to have opinions about
    what to do on the way up. ``--entrypoint`` rather than a command, because these Dockerfiles
    inherit the base image's entrypoint deliberately and it would otherwise swallow the
    arguments. Both are ``verify_image_accelerator``'s reasoning.
    """
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        interpreter,
        image_reference,
        "-c",
        probe_script(),
    ]


def read_report(stdout: str) -> dict[str, object] | None:
    """The probe's answer, or ``None`` if this interpreter did not give one.

    The last sentinel line wins, for ``verify_image_accelerator``'s reason: a repeated sentinel
    means something in the image echoed one, and the probe writes its own last.
    """
    found: dict[str, object] | None = None
    for line in stdout.splitlines():
        if not line.startswith(f"{SENTINEL} "):
            continue
        try:
            payload = json.loads(line[len(SENTINEL) + 1 :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            found = payload
    return found


def sources_from_image(image_reference: str) -> dict[str, str]:
    """Every candidate file the image holds, by the path it was asked for.

    The files rather than the parsed names cross the boundary, so the readers above are one
    implementation shared by both modes.
    """
    report: dict[str, object] | None = None
    for interpreter in INTERPRETERS:
        try:
            completed = subprocess.run(
                probe_command(image_reference, interpreter),
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("image_probe_timed_out", "The image did not answer in time.") from exc
        except OSError as exc:
            raise ProbeError(
                "docker_unavailable",
                "docker is not runnable here. Take the reading with --repository-root instead, "
                "which records itself as the weaker source_at_commit form.",
            ) from exc
        # Neither stream is printed: both are the image's, and a caller's log is world readable.
        # An interpreter that is not in the image writes nothing and the next candidate is tried.
        report = read_report(completed.stdout)
        if report is not None:
            break

    if report is None:
        raise ProbeError(
            "image_probe_unanswered",
            "No interpreter in this image answered on stdout, so what it contains cannot be "
            "established -- which reads the same here as an image that answered wrongly.",
        )
    read = report.get("read")
    if not isinstance(read, dict):
        raise ProbeError(
            "image_probe_malformed",
            "The probe answered without the map of files it was asked to read.",
        )
    return {path: text for path, text in read.items() if isinstance(text, str)}


def sources_from_checkout(repository_root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted({candidate for source in VOCABULARIES for candidate in source.paths}):
        try:
            found[path] = (repository_root / path).read_text(encoding="utf-8")
        except OSError:
            continue
    return found


def vocabularies_in(sources: dict[str, str]) -> tuple[ImageVocabulary, ...]:
    """Every vocabulary these files establish, as the record holds them.

    A vocabulary whose files are all absent is *omitted* rather than recorded empty, which is
    the distinction the contract's header is about: absent means nobody read it and empty means
    somebody read it and found none. A reader that returns a reason is not silenced, though --
    a file that is present and whose shape this cannot read is a loud failure, because it is the
    case where the record would otherwise be confidently wrong.
    """
    read: list[ImageVocabulary] = []
    for source in VOCABULARIES:
        for path in source.paths:
            text = sources.get(path)
            if text is None:
                continue
            read.append(
                ImageVocabulary(kind=source.kind, read_from=path, names=source.read(text))
            )
            break
    if not read:
        raise ProbeError(
            "nothing_was_read",
            "None of the files any vocabulary is read from is present here. A reading that "
            "established nothing is not a reading: if this repository's image holds none of "
            "them it needs no entry in the record at all, and adding one would say 'asked and "
            "answered' while holding no answer.",
        )
    return tuple(sorted(read, key=lambda entry: entry.kind))


def head_commit(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProbeError(
            "commit_unreadable",
            f"{repository_root} is not a git checkout this can read a commit from. A reading "
            "with no commit against it says nothing about which image it describes, so name one "
            "with --commit-sha.",
        )
    return completed.stdout.strip()


def record_with(
    existing: ImageContentsRecord | None, reading: ImageContentsReading
) -> ImageContentsRecord:
    """The record this reading produces, replacing any earlier one for the same repository.

    Sorted by repository, so re-reading one image produces a diff of that image's block and not
    a reshuffle of the file. The contract refuses two readings for one repository, so replacement
    is the only correct merge.
    """
    others = (
        ()
        if existing is None
        else tuple(entry for entry in existing.images if entry.repository != reading.repository)
    )
    return ImageContentsRecord(
        schema_version=1,
        images=tuple(sorted((*others, reading), key=lambda entry: entry.repository)),
    )


def as_document(record: ImageContentsRecord) -> str:
    return yaml.safe_dump(
        record.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        default_flow_style=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="the registry key, such as OLMo-core")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--image-reference",
        help="a published image to open, which is the reading that cannot be wrong",
    )
    source.add_argument(
        "--repository-root",
        type=Path,
        help="a checkout to read instead, recorded as the weaker source_at_commit form",
    )
    parser.add_argument(
        "--image-digest",
        help="the digest of the image being opened; required with --image-reference",
    )
    parser.add_argument(
        "--commit-sha",
        help="the commit the image was built from; read from the checkout when omitted",
    )
    parser.add_argument(
        "--record",
        type=Path,
        help="the reviewed record to update in place; printed to stdout when omitted",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        if arguments.image_reference is not None:
            if not arguments.image_digest or not arguments.commit_sha:
                raise ProbeError(
                    "probe_needs_a_digest_and_a_commit",
                    "A reading taken from an image records which image it opened and which "
                    "commit that image was built from. Both are on the publish workflow's own "
                    "output; neither can be derived from the reference.",
                )
            sources = sources_from_image(arguments.image_reference)
            method = ReadingMethod.IMAGE_PROBE
            digest = arguments.image_digest
            commit = arguments.commit_sha
        else:
            sources = sources_from_checkout(arguments.repository_root)
            method = ReadingMethod.SOURCE_AT_COMMIT
            digest = None
            commit = arguments.commit_sha or head_commit(arguments.repository_root)

        reading = ImageContentsReading(
            repository=arguments.repository,
            commit_sha=commit,
            read_by=method,
            image_digest=digest,
            read_at=datetime.now(UTC),
            vocabularies=vocabularies_in(sources),
        )
    except ProbeError as exc:
        print(exc.reason, file=sys.stderr)
        print(exc.guidance, file=sys.stderr)
        return 1

    existing = None
    if arguments.record is not None and arguments.record.is_file():
        existing = ImageContentsRecord.model_validate(
            yaml.safe_load(arguments.record.read_text(encoding="utf-8"))
        )
    document = as_document(record_with(existing, reading))
    print(document, end="")
    if arguments.record is not None:
        # The prose at the top of the reviewed file is a person's and is not regenerable, so this
        # writes the entries and says so rather than overwriting the header silently.
        print(
            f"\nThe block above is the reading. {arguments.record} carries a header nothing here "
            "can regenerate, so put this under it by hand and leave the header's argument "
            "intact.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
