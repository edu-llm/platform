"""Read which tokenizers a training image can build, and record it.

THE FACT THIS MEASURES IS THE ONE THE RUNNABILITY VERDICT RESTS ON, AND UNTIL NOW NOTHING
MEASURED IT. ``edullm data`` told a researcher whether the corpus they picked would start,
and derived that from ``src/edullm_platform/tokenizers.py`` -- this platform's map of
published tokenizer ids to the OLMo-core expression that reproduces each one. That map is
about what this platform can express. Whether a run starts is about what the image can
build. They agreed until somebody added two entries ahead of the matching lines in
OLMo-core, at which point three corpora were offered that no image can train, and
``run_019fdd88-3ac4`` spent a GPU allocation finding that out.

TWO WAYS TO READ IT, AND THE STRONG ONE IS A PROBE OF THE ASSEMBLED IMAGE.
``verify_image_accelerator.py`` sets out why at length for a different fact about an image
and the argument transfers whole: a claim about a repository's source protects exactly what
that source produces, and an image is not that -- a ``COPY`` that misses the file, an
entrypoint pinning an older checkout, or a BuildKit cache hit that reuses a layer without
re-running it all leave an image whose map is not the map in the tree. ``--image-reference``
takes the strong reading. ``--repository-root`` takes the weak one, off a checkout, for the
ordinary case of a maintainer with no Docker daemon and no registry session; the record
says which was taken and ``ImageTokenizerReading`` refuses a digest beside the weak one.

WHY THE PROBE PARSES RATHER THAN IMPORTS. Importing ``.edullm/train_on_corpus.py`` would
give the map exactly, and it would also import ``olmo_core``, torch and a CUDA stack --
seconds to minutes, and a failure mode where an image whose torch is unhappy reads as an
image with no tokenizers. The map is a module-level dict literal of string keys, so
``ast`` answers the same question off the source with nothing executed, in both modes, and
an image whose map has stopped being a literal is refused loudly rather than read wrongly.

Like its siblings the first line it prints on a failure is a machine-readable reason, and
nothing it prints is derived from the image it ran: a caller's runner log is world readable
and the image reference names the account.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml

from edullm_platform.contracts.image_tokenizers import (
    ImageTokenizerReading,
    ImageTokenizerRecord,
    ReadingMethod,
)

#: Where the map lives inside a research repository, and therefore inside its image. One
#: path rather than a search, because a probe that went looking would find a copy in a test
#: fixture as readily as the real one and could not tell which it had read.
TRAINER_PATH: Final = ".edullm/train_on_corpus.py"

#: The module-level name holding the map. The container looks its tokenizer up in exactly
#: this dict and exits 69 on a miss, so this is the name and not a name.
MAP_NAME: Final = "TOKENIZERS"

#: Tried in order against ``docker run --entrypoint``, which resolves each against the
#: image's own PATH. The same list ``verify_image_accelerator.py`` uses and for its reasons.
INTERPRETERS: Final = ("python", "python3")

#: Prefixed so that anything the image prints on the way up cannot be mistaken for the
#: answer, exactly as the accelerator probe's sentinel is.
SENTINEL: Final = "EDULLM_TOKENIZER_PROBE"

#: Runs inside the image. Stdlib only, no network, nothing written, and every failure
#: reported as a type name rather than a message: an exception from somebody else's image
#: could carry a path or a token into a world-readable log.
#:
#: IT HANDS BACK THE FILE AND NOT THE ANSWER, WHICH IS THE POINT. Parsing inside the image
#: would put a second reader of this map on the other side of a boundary nothing here can
#: test, and one map with two readers is the shape this whole change exists to remove.
#: :func:`tokenizers_in` is the one reader, and it runs on both paths.
PROBE: Final = f"""
import json, sys

report = {{"probe": "tokenizers", "version": 1}}
try:
    with open({TRAINER_PATH!r}, encoding="utf-8") as handle:
        report["source"] = handle.read()
except OSError:
    report["source"] = None
except BaseException as exc:
    report["source"] = None
    report["error"] = type(exc).__name__

sys.stdout.write("{SENTINEL} " + json.dumps(report) + chr(10))
"""

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

    **A MAP THIS CANNOT READ IS AN ERROR AND NEVER AN EMPTY MAP**, which is the one thing
    that must not be got wrong here. An empty answer is written into the record and the
    record decides the verdict, so a parse that quietly returned nothing would mark every
    corpus unrunnable -- or, on a repository recorded beside others, would silently narrow
    what the platform offers. Absent, unparseable and empty are three different findings and
    only one of them is a fact about the image.

    A key that is not a plain string literal is refused for the same reason. The container
    looks a runtime string up in this dict; a computed key is a key this cannot resolve, and
    guessing at one would put a tokenizer in the record that the image may not answer to.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ProbeError(
            "trainer_unparseable",
            f"{TRAINER_PATH} is not parseable Python, so the map it holds cannot be read.",
        ) from exc

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if MAP_NAME not in names:
            continue
        if not isinstance(node.value, ast.Dict):
            raise ProbeError(
                "map_is_not_a_literal",
                f"{MAP_NAME} in {TRAINER_PATH} is no longer a dict literal, so its keys "
                "cannot be read without running the module. Read it by hand and record it, "
                "or restore the literal.",
            )
        keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise ProbeError(
                    "map_key_is_not_a_string",
                    f"{MAP_NAME} in {TRAINER_PATH} holds a key that is not a string "
                    "literal. The container looks a runtime string up in this map, so a "
                    "computed key cannot be recorded as one an image answers to.",
                )
            keys.append(key.value)
        return tuple(sorted(keys))

    raise ProbeError(
        "map_not_found",
        f"{TRAINER_PATH} holds no module-level {MAP_NAME}. If this repository's image "
        "trains no corpus, it needs no record at all; leave it out rather than recording an "
        "empty one.",
    )


def probe_command(image_reference: str, interpreter: str) -> list[str]:
    """The container this runs, which holds no credential and reaches no network.

    ``--network none`` because the probe opens one file and an image is free to have
    opinions about what to do on the way up. ``--entrypoint`` rather than a command, because
    these Dockerfiles inherit the base image's entrypoint deliberately and it would
    otherwise swallow the arguments. Both are ``verify_image_accelerator``'s reasoning.
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
        PROBE,
    ]


def read_report(stdout: str) -> dict[str, object] | None:
    """The probe's answer, or ``None`` if this interpreter did not give one.

    The last sentinel line wins, for ``verify_image_accelerator``'s reason: a repeated
    sentinel means something in the image echoed one, and the probe writes its own last.
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


def read_from_image(image_reference: str) -> str:
    """The trainer's source, read out of the assembled image.

    The file rather than the parsed keys crosses the boundary, so that the parsing above is
    one implementation shared by both modes. A probe that parsed inside the image would be a
    second reader of the same map, which is the shape this whole change exists to remove.
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
                "docker is not runnable here. Take the reading with --repository-root "
                "instead, which records itself as the weaker source_at_commit form.",
            ) from exc
        # Neither stream is printed: both are the image's, and a caller's log is world
        # readable. An interpreter that is not in the image writes nothing and the next
        # candidate is tried.
        report = read_report(completed.stdout)
        if report is not None:
            break

    if report is None:
        raise ProbeError(
            "image_probe_unanswered",
            "No interpreter in this image answered on stdout, so what it contains cannot be "
            "established -- which reads the same here as an image that answered wrongly.",
        )
    source = report.get("source")
    if not isinstance(source, str):
        raise ProbeError(
            "trainer_absent_from_image",
            f"This image holds no {TRAINER_PATH}. If it trains no corpus it needs no record; "
            "if it should hold one, the Dockerfile is not copying it and every run naming a "
            "corpus on this image would exit 69.",
        )
    return source


def read_from_checkout(repository_root: Path) -> str:
    path = repository_root / TRAINER_PATH
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProbeError(
            "trainer_absent_from_checkout",
            f"{path} could not be read. If this repository trains no corpus it needs no "
            "record at all.",
        ) from exc


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
            f"{repository_root} is not a git checkout this can read a commit from. A "
            "reading with no commit against it says nothing about which image it describes, "
            "so name one with --commit-sha.",
        )
    return completed.stdout.strip()


def record_with(
    existing: ImageTokenizerRecord | None, reading: ImageTokenizerReading
) -> ImageTokenizerRecord:
    """The record this reading produces, replacing any earlier one for the same repository.

    Sorted by repository, so re-reading one image produces a diff of that image's block and
    not a reshuffle of the file. The contract refuses two readings for one repository, so
    replacement is the only correct merge.
    """
    others = () if existing is None else tuple(
        entry for entry in existing.images if entry.repository != reading.repository
    )
    return ImageTokenizerRecord(
        schema_version=1,
        images=tuple(sorted((*others, reading), key=lambda entry: entry.repository)),
    )


def as_document(record: ImageTokenizerRecord) -> str:
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
                    "commit that image was built from. Both are on the publish workflow's "
                    "own output; neither can be derived from the reference.",
                )
            source = read_from_image(arguments.image_reference)
            method = ReadingMethod.IMAGE_PROBE
            digest = arguments.image_digest
            commit = arguments.commit_sha
        else:
            source = read_from_checkout(arguments.repository_root)
            method = ReadingMethod.SOURCE_AT_COMMIT
            digest = None
            commit = arguments.commit_sha or head_commit(arguments.repository_root)

        reading = ImageTokenizerReading(
            repository=arguments.repository,
            commit_sha=commit,
            read_by=method,
            image_digest=digest,
            read_from=TRAINER_PATH,
            read_at=datetime.now(UTC),
            tokenizers=tokenizers_in(source),
        )
    except ProbeError as exc:
        print(exc.reason, file=sys.stderr)
        print(exc.guidance, file=sys.stderr)
        return 1

    existing = None
    if arguments.record is not None and arguments.record.is_file():
        existing = ImageTokenizerRecord.model_validate(
            yaml.safe_load(arguments.record.read_text(encoding="utf-8"))
        )
    document = as_document(record_with(existing, reading))
    if arguments.record is None:
        print(document, end="")
    else:
        # The prose at the top of the reviewed file is a person's and is not regenerable, so
        # this writes the entries and says so rather than overwriting the header silently.
        print(document, end="")
        print(
            f"\nThe block above is the reading. {arguments.record} carries a header nothing "
            "here can regenerate, so put this under it by hand and leave the header's "
            "argument intact.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
