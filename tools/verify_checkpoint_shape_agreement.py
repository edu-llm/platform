"""Hold this platform's idea of a loadable checkpoint equal to OLMo-core's own.

:mod:`edullm_platform.checkpoints` decides whether a run has something to resume from by
looking for a fixed set of files under a ``step{N}`` directory. That set was copied by hand
out of ``Checkpointer.dir_is_checkpoint``, and nothing has ever held the two together. If
OLMo-core changes what it accepts, this platform goes on answering the old question: it
would refuse a checkpoint a resume would load, or promise one it would not, and the first
symptom either way is an operator being told the opposite of what the trainer beside them
is doing.

**Read rather than imported, and that is the constraint that shapes everything here.**
Importing ``olmo_core.train.checkpoint`` pulls in torch, and this runs on the image-build
gate, which is a bare runner holding no AWS identity and nothing installed. So the file is
parsed. The paths ``dir_is_checkpoint`` tests are string literals in the source, which is
what makes that possible at all.

**Every uncertainty is drift.** A parser that cannot find what it is looking for reports
that, rather than finding nothing and reading nothing as agreement. Renaming the function,
moving the paths out of literals, or adding a branch this does not model are all changes to
the contract, and a check that stayed quiet through them would be worse than absent: it
would be a green light nobody had earned.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from edullm_platform.checkpoints import (
    OLMO_CORE_FULL_CHECKPOINT,
    OLMO_CORE_WEIGHTS_ONLY,
)

#: Where the checkpointer lives inside an OLMo-core checkout.
CHECKPOINT_MODULE: Final = Path("src/olmo_core/train/checkpoint.py")

#: The class and method the whole contract hangs off.
CHECKPOINTER_CLASS: Final = "Checkpointer"
SHAPE_METHOD: Final = "dir_is_checkpoint"

#: The parameter every checked path is built from, as ``dir_is_checkpoint`` names it.
DIRECTORY_PARAMETER: Final = "dir"

__all__ = [
    "CHECKPOINT_MODULE",
    "CheckpointShapeDrift",
    "build_parser",
    "compare_shapes",
    "main",
    "read_library_shapes",
]


class CheckpointShapeDrift(ValueError):
    """The library no longer accepts what this platform believes it accepts.

    Carries a machine-readable reason first, the way the sibling verifiers do, and a
    sentence naming what to do about it. Both are safe to print: everything they quote
    comes from this repository or is a path literal, never from anything a caller supplied.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _class_attribute_strings(node: ast.ClassDef) -> dict[str, str]:
    """The class's string-valued attributes, which is how ``METADATA_FNAME`` is resolved.

    Only plain string assignments are collected. Anything computed is left out, so a
    reference to it fails to resolve and is reported as drift rather than guessed at.
    """
    found: dict[str, str] = {}
    for statement in node.body:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _relative_path(node: ast.AST, attributes: dict[str, str]) -> str | None:
    """The path an f-string builds under the checkpoint directory, or None if not one.

    ``f"{dir}/train/rank0.pt"`` yields ``train/rank0.pt``. A segment interpolated from a
    class attribute is resolved from ``attributes``; one interpolated from anything else
    raises, because a path this cannot read is a path it cannot compare.
    """
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return None

    head, *rest = node.values
    if not isinstance(head, ast.FormattedValue):
        return None
    if not isinstance(head.value, ast.Name) or head.value.id != DIRECTORY_PARAMETER:
        return None

    segments: list[str] = []
    for part in rest:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            segments.append(part.value)
            continue
        if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Attribute):
            name = part.value.attr
            resolved = attributes.get(name)
            if resolved is None:
                raise CheckpointShapeDrift(
                    "checkpoint_path_unresolvable",
                    f"{SHAPE_METHOD} builds a path from {name}, which is not a plain "
                    "string attribute on the class, so what file it checks for cannot be "
                    "read out of the source",
                )
            segments.append(resolved)
            continue
        raise CheckpointShapeDrift(
            "checkpoint_path_unresolvable",
            f"{SHAPE_METHOD} builds a path this parser cannot read as a literal, so the "
            "set of files the library requires can no longer be compared against ours",
        )

    return "".join(segments).lstrip("/")


def _find(tree: ast.Module) -> tuple[ast.ClassDef, ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == CHECKPOINTER_CLASS:
            for member in node.body:
                if (
                    isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                    and member.name == SHAPE_METHOD
                    and isinstance(member, ast.FunctionDef)
                ):
                    return node, member
            raise CheckpointShapeDrift(
                "shape_method_missing",
                f"{CHECKPOINTER_CLASS} no longer defines {SHAPE_METHOD}, so the rule this "
                "platform mirrors has either moved or been replaced",
            )
    raise CheckpointShapeDrift(
        "checkpointer_class_missing",
        f"{CHECKPOINT_MODULE} no longer defines {CHECKPOINTER_CLASS}",
    )


def read_library_shapes(source: str) -> tuple[frozenset[str], frozenset[str]]:
    """The two shapes ``dir_is_checkpoint`` accepts, read out of its source.

    Returns the weights-only shape and the full shape, in that order. The first is the
    early return: a directory carrying that one file is accepted outright, with no trainer
    state. The second is the list every member of which must be present.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CheckpointShapeDrift(
            "checkpoint_module_unparseable",
            f"{CHECKPOINT_MODULE} did not parse as Python: {exc.msg}",
        ) from exc

    owner, method = _find(tree)
    attributes = _class_attribute_strings(owner)

    weights_only: set[str] = set()
    full: set[str] = set()

    for statement in ast.walk(method):
        # The early return: `if file_exists(f"{dir}/.metadata"): return True`. Accepting a
        # directory on one file is the weights-only shape, and it is identified by the
        # return rather than by the filename so that renaming the file is still read.
        if isinstance(statement, ast.If) and any(
            isinstance(inner, ast.Return)
            and isinstance(inner.value, ast.Constant)
            and inner.value.value is True
            for inner in statement.body
        ):
            for node in ast.walk(statement.test):
                path = _relative_path(node, attributes)
                if path is not None:
                    weights_only.add(path)

        # The conjunction: every path in the list has to exist.
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.List):
            for element in statement.value.elts:
                path = _relative_path(element, attributes)
                if path is not None:
                    full.add(path)

    if not weights_only:
        raise CheckpointShapeDrift(
            "weights_only_shape_missing",
            f"{SHAPE_METHOD} no longer accepts a directory on a single file, so the "
            "weights-only shape this platform reports has no counterpart in the library",
        )
    if not full:
        raise CheckpointShapeDrift(
            "full_shape_missing",
            f"{SHAPE_METHOD} no longer checks a list of required files, so the full "
            "checkpoint shape this platform reports has no counterpart in the library",
        )
    return frozenset(weights_only), frozenset(full)


def compare_shapes(source: str) -> None:
    """Raise unless the library requires exactly the files this platform looks for."""
    library_weights_only, library_full = read_library_shapes(source)
    ours_weights_only = frozenset(OLMO_CORE_WEIGHTS_ONLY)
    ours_full = frozenset(OLMO_CORE_FULL_CHECKPOINT)

    for label, ours, theirs in (
        ("weights-only", ours_weights_only, library_weights_only),
        ("full", ours_full, library_full),
    ):
        if ours == theirs:
            continue
        missing = sorted(theirs - ours)
        extra = sorted(ours - theirs)
        parts = []
        if missing:
            parts.append(
                f"the library now requires {', '.join(missing)} and we do not check for it"
            )
        if extra:
            parts.append(f"we require {', '.join(extra)} and the library no longer does")
        raise CheckpointShapeDrift(
            "checkpoint_shape_drift",
            f"the {label} shape no longer matches: " + "; ".join(parts) + ". Update "
            "OLMO_CORE_WEIGHTS_ONLY and OLMO_CORE_FULL_CHECKPOINT in "
            "src/edullm_platform/checkpoints.py to match, and check whether "
            "olmo_core_checkpoint_shape still describes what it returns",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--olmo-core-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    root = arguments.olmo_core_root.resolve()
    module = (root / CHECKPOINT_MODULE).resolve()
    if not module.is_relative_to(root):
        print("checkpoint_module_outside_repository", file=sys.stderr)
        return 2

    try:
        source = module.read_text(encoding="utf-8")
    except OSError:
        print("checkpoint_module_unreadable", file=sys.stderr)
        print(
            f"Expected to find {CHECKPOINT_MODULE} under {root}. If the checkpointer has "
            "moved, this check has to move with it rather than be dropped.",
            file=sys.stderr,
        )
        return 2

    try:
        compare_shapes(source)
    except CheckpointShapeDrift as exc:
        print(exc.reason, file=sys.stderr)
        print(exc.detail, file=sys.stderr)
        return 1

    print("The checkpoint shapes this platform checks for are the ones the library requires.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
