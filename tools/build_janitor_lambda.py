"""Package the expiry janitor into the zip CloudFormation deploys.

The third function to ship this way, and it is deliberately the shape of the second rather than
of the first. Everything about *how* it is built is tools/build_admission_lambda.py's, imported
rather than restated: wheels for x86_64-manylinux_2_28 and CPython 3.12 with
--only-binary=:all:, because pydantic ships a compiled core and a zip assembled from a laptop's
own environment fails at import with a message about a missing module rather than about an
architecture.

IT CARRIES NO CONFIGURATION AT ALL, which is a decision and not an omission. The two numbers the
sweep runs on live in config/reports/researcher-lane.yaml and reach the deployed function as
environment variables on infra/expiry-janitor.yaml, held equal to the file by
tests/test_janitor_infrastructure.py. Packaging the file instead would put this function's
release digest behind an edit to a number -- which is the coupling both existing builders were
narrowed on 2026-08-04 to break, arriving through a third door.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

# The same path insertion tools/build_lifecycle_lambda.py performs, for the reason its comment
# gives: `python tools/build_janitor_lambda.py` puts tools/ on the path and not the repository
# root, while pytest puts the root on the path and not tools/.
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from build_admission_lambda import (
    DEFAULT_PYTHON_PLATFORM,
    DEFAULT_PYTHON_VERSION,
    LambdaPackageError,
    build_package,
)

__all__ = [
    "ARTIFACT_KEY",
    "HANDLER_ENTRY_POINT",
    "JANITOR_CONFIG",
    "JANITOR_ENTRYPOINT",
    "build_parser",
    "main",
]

#: Its own prefix, so three functions' object versions cannot be confused in a bucket listing.
ARTIFACT_KEY = "expiry-janitor/expiry-janitor.zip"

HANDLER_ENTRY_POINT = "edullm_platform.janitor_handler.handler"

JANITOR_ENTRYPOINT = "edullm_platform.janitor_handler"

#: Empty, for the reason in this module's docstring. Passed explicitly rather than defaulted, so
#: a handler that does start reading configuration has to say so here;
#: tests/test_lambda_package_closure.py compares this against what the packaged modules name, in
#: both directions.
JANITOR_CONFIG: frozenset[str] = frozenset()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path, help="where to write the zip")
    parser.add_argument("--python-platform", default=DEFAULT_PYTHON_PLATFORM)
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        record = build_package(
            PROJECT_ROOT,
            arguments.output,
            entrypoint=JANITOR_ENTRYPOINT,
            configuration=JANITOR_CONFIG,
            python_platform=arguments.python_platform,
            python_version=arguments.python_version,
        )
    except LambdaPackageError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {**record, "artifact_key": ARTIFACT_KEY, "handler": HANDLER_ENTRY_POINT},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
