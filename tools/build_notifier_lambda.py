"""Package the notifier into the zip CloudFormation deploys.

The notifier is `Code: {S3Bucket, S3Key, S3ObjectVersion}` in `infra/notifications.yaml`, the
same shape the admission validator and the lifecycle recorder use, so something has to produce
the object that key names.

A third command rather than a flag on one of the others, for the reason the second one exists:
three artifacts are three objects with three versions, and a release procedure that confuses
them deploys the wrong code under the right name.

**This one reads configuration and the recorder does not, which is the difference worth
knowing before editing either.** The recorder projects an event into a lineage record and
consults nothing. The notifier resolves a W&B account to a person, a queue to a compute
profile and a profile to an hourly rate, so it carries exactly the three files that answer
those three questions. Three rather than eight: a wider list would move this function's
release digest whenever somebody edited a policy it never opens, and eight team leads hold
CODEOWNERS approval on `/config/**` while only an AWS credential can clear a moved digest.

Everything about how it is built is `tools/build_admission_lambda.py`'s, imported rather than
restated. The wheels are built for `x86_64-manylinux_2_28` and CPython 3.12 with
`--only-binary=:all:`, because pydantic ships a compiled core. The zip is deterministic, so a
rebuild that changes nothing is recognisable as unchanged and mints no new object version.

The sha256 of what was built is printed, because that digest is what the release procedure in
`infra/README.md` compares against the object it uploaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

# Imported by bare module name for the reason build_lifecycle_lambda.py gives: running this
# as a path puts tools/ on sys.path and not the repository root, while pytest does the
# opposite, and importing it as `tools.build_admission_lambda` makes mypy see one file under
# two module names.
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
    "NOTIFIER_CONFIG",
    "NOTIFIER_ENTRYPOINT",
    "build_parser",
    "main",
]

#: Where the artifact is uploaded, and what `infra/notifications.yaml` names as `S3Key`. Its
#: own prefix, so three functions' object versions cannot be confused in a bucket listing.
ARTIFACT_KEY = "notifier/notifier.zip"

#: What the template's `Handler` must be. Printed with the digest so a release that uploaded
#: the right bytes under the wrong handler is visible in the same output.
HANDLER_ENTRY_POINT = "edullm_platform.notifier_handler.handler"

#: What this function imports, which is what its zip carries.
NOTIFIER_ENTRYPOINT = "edullm_platform.notifier_handler"

#: The three reviewed files this function opens, and no others. Held to what the packaged
#: modules actually name, in both directions, by tests/test_lambda_package_closure.py.
NOTIFIER_CONFIG: frozenset[str] = frozenset(
    {"organization.yaml", "workload-catalog.yaml", "execution-targets.yaml"}
)


def build_parser() -> argparse.ArgumentParser:
    """Named so tests/test_workflow_tool_arguments.py can import and read it."""
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
            entrypoint=NOTIFIER_ENTRYPOINT,
            configuration=NOTIFIER_CONFIG,
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


if __name__ == "__main__":
    raise SystemExit(main())
