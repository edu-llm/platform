"""Require the registered Dockerfile to build from the base image the platform passes.

The registered base digest reaches ``docker build`` only as ``--build-arg BASE_IMAGE``,
which a Dockerfile is free to ignore by hardcoding its own ``FROM``. Nothing downstream
would notice: ``write_image_provenance`` records ``base_image_digest`` from the registry,
so the provenance record would assert a fact about the image that nothing established.
An unverified assertion in a provenance record is worse than an absent field, and
provenance is the deliverable, so this gate runs before the build and fails closed.

``FROM`` is not the only instruction that reaches outside the build: ``COPY --from=`` and
``RUN --mount=type=bind,from=`` name an image too, and what they pull in ends up in a
published layer just the same. They are held to the same rule. What no static gate can
promise is anything about the *contents* of the registered base, so the guarantee stops
at "every image this build reads was registered", not at "this image contains only
registered bytes".

Like its siblings it prints only a machine-readable reason: the runner log is world
readable for any public caller repository.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from edullm_platform.build_tooling import RegistryUnreadableError, load_registry
from edullm_platform.contracts.repository_registry import UnknownRepositoryError

ARG_PATTERN = re.compile(r"^ARG\s+(?P<names>\S.*)$", re.IGNORECASE)
FROM_PATTERN = re.compile(r"^FROM\s*(?P<arguments>.*)$", re.IGNORECASE)
COPY_FROM_PATTERN = re.compile(r"(?<!\S)--from=(?P<value>\S+)", re.IGNORECASE)
MOUNT_PATTERN = re.compile(r"(?<!\S)--mount=(?P<value>\S+)", re.IGNORECASE)
MOUNT_FROM_PATTERN = re.compile(r"^from=(?P<value>.*)$", re.IGNORECASE)
STAGE_INDEX_PATTERN = re.compile(r"0|[1-9][0-9]*")
BASE_IMAGE_ARG = "BASE_IMAGE"
BASE_IMAGE_REFERENCES = frozenset({"${BASE_IMAGE}", "$BASE_IMAGE"})

__all__ = [
    "DockerfileBaseError",
    "build_parser",
    "main",
    "require_base_image_contract",
]


class DockerfileBaseError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _logical_lines(text: str) -> list[str]:
    """Collapse a Dockerfile into instructions, the way the builder reads it.

    Blank lines and whole-line comments disappear, including comment lines that interrupt
    a continuation. An inline ``#`` is not a comment and is deliberately left in place.
    """
    instructions: list[str] = []
    buffer = ""
    continuing = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            continue
        if not continuing:
            if not line:
                continue
            buffer = ""
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continuing = True
            continue
        buffer += line
        instructions.append(buffer.strip())
        buffer = ""
        continuing = False

    if continuing:
        raise DockerfileBaseError("unterminated_line_continuation")
    return instructions


def _parse_from(arguments: str) -> tuple[str, str | None]:
    tokens = arguments.split()
    flags = 0
    while flags < len(tokens) and tokens[flags].startswith("--"):
        flags += 1
    remainder = tokens[flags:]
    if not remainder:
        raise DockerfileBaseError("malformed_from_instruction")
    image, alias = remainder[0], remainder[1:]
    if not alias:
        return image, None
    if len(alias) != 2 or alias[0].upper() != "AS":
        raise DockerfileBaseError("malformed_from_instruction")
    return image, alias[1]


def _stage_references(instruction: str) -> list[str]:
    """Every ``--from=`` an instruction resolves against the set of build stages.

    ``COPY --from=`` spells it directly; a bind or cache mount spells it as one field of
    the comma-separated ``--mount=`` value.
    """
    references = [match.group("value") for match in COPY_FROM_PATTERN.finditer(instruction)]
    for mount in MOUNT_PATTERN.finditer(instruction):
        for field in mount.group("value").strip("\"'").split(","):
            source = MOUNT_FROM_PATTERN.match(field)
            if source is not None:
                references.append(source.group("value"))
    return [reference.strip("\"'") for reference in references]


def require_base_image_contract(text: str) -> None:
    """Raise unless every build stage derives from the platform-supplied base image.

    A stage may name an earlier stage, which is how an ordinary multi-stage build works,
    but the root of every chain has to be ``${BASE_IMAGE}``. A default on the ``ARG`` is
    refused too: it is a base image nobody registered, waiting for the day the build-arg
    is dropped.

    ``COPY --from=`` and ``RUN --mount=...,from=`` reach outside the build just as a
    ``FROM`` does, and what they reach for lands in a published layer, so they are held to
    the same rule: an earlier stage, by name or by index, and nothing else.
    """
    declares_base_image = False
    declared_names: set[str] = set()
    earlier_names: set[str] = set()
    stages = 0

    for instruction in _logical_lines(text):
        argument = ARG_PATTERN.match(instruction)
        # Only a global-scope ARG, declared before the first FROM, is in scope for FROM.
        if argument is not None and stages == 0:
            for name in argument.group("names").split():
                if name == BASE_IMAGE_ARG:
                    declares_base_image = True
                elif name.startswith(f"{BASE_IMAGE_ARG}="):
                    raise DockerfileBaseError("base_image_arg_has_default")

        stage = FROM_PATTERN.match(instruction)
        if stage is None:
            for reference in _stage_references(instruction):
                index = STAGE_INDEX_PATTERN.fullmatch(reference)
                # A stage can name neither itself nor a stage declared after it, so the
                # current stage is excluded from both the names and the index range.
                if index is not None and int(reference) < stages - 1:
                    continue
                if index is None and reference.casefold() in earlier_names:
                    continue
                raise DockerfileBaseError("unregistered_stage_reference")
            continue

        if not declares_base_image:
            raise DockerfileBaseError("missing_base_image_arg")
        image, alias = _parse_from(stage.group("arguments"))
        if image not in BASE_IMAGE_REFERENCES and image.casefold() not in declared_names:
            raise DockerfileBaseError("unregistered_base_image")
        stages += 1
        earlier_names = set(declared_names)
        if alias is not None:
            # Docker matches stage names case-insensitively, so the record has to as well.
            declared_names.add(alias.casefold())

    if stages == 0:
        raise DockerfileBaseError("no_build_stage")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        registry = load_registry(arguments.registry)
    except RegistryUnreadableError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    try:
        registered = registry.repository_by_name(arguments.repository)
    except UnknownRepositoryError:
        print("unregistered_repository", file=sys.stderr)
        return 1

    repository_root = arguments.repository_root.resolve()
    dockerfile_path = (repository_root / registered.dockerfile_path).resolve()
    # The registry already constrains the path, but resolve() follows symlinks, so the
    # file the builder would read still has to be shown to live inside the checkout.
    if not dockerfile_path.is_relative_to(repository_root):
        print("dockerfile_outside_repository", file=sys.stderr)
        return 1

    try:
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print("dockerfile_undecodable", file=sys.stderr)
        return 2
    except OSError:
        print("dockerfile_unreadable", file=sys.stderr)
        return 2

    try:
        require_base_image_contract(dockerfile)
    except DockerfileBaseError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
