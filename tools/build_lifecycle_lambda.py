"""Package the lifecycle recorder into the zip CloudFormation deploys.

The recorder is `Code: {S3Bucket, S3Key, S3ObjectVersion}` in `infra/batch-events.yaml`, the
same shape Phase 2 established for the admission validator, so something has to produce the
object that key names. This is that step, and it exists as a second command rather than a
flag because the two artifacts are two objects with two versions and a release procedure
that confuses them deploys the wrong code under the right name.

**The two zips were once byte-identical and are now deliberately not.** Both functions are
entry points into the same package and `Handler` in each template names which one, so the
first version of this built one artifact and uploaded it under two keys. That coupled the
releases: an admission fix moved the recorder's `S3ObjectVersion` and a template edit
followed for a function whose behaviour had not changed. Two builds cost a minute and keep
each function pinned to a version that only moves when somebody releases it.

What each carries is now measured per entry point, and the recorder is much the smaller of
the two. It imports 15 of the package's modules against the validator's 28, and it reads no
configuration whatsoever -- it projects a Batch state-change event into a lifecycle record
and never consults the policy, the roster, the catalog or anything else under `config/`. It
carried all of it until 2026-08-04 anyway, which meant a one-line roster edit moved this
function's release digest and turned a red required check into work only somebody with AWS
credentials could clear.

Everything about *how* it is built is `tools/build_admission_lambda.py`'s, imported rather
than restated. The wheels are built for `x86_64-manylinux_2_28` and CPython 3.12 with
`--only-binary=:all:`, because pydantic ships a compiled core and a zip assembled from a
laptop's own environment fails at import with a message about a missing module rather than
about an architecture. The dependency list is read from `pyproject.toml`. The zip is
deterministic, so a rebuild that changes nothing is recognisable as unchanged and does not
mint a new object version for no reason.

The sha256 of what was built is printed, because that digest is what the release procedure
in `infra/README.md` compares against the object it uploaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

# The Phase 2 builder is imported by its bare module name rather than as `tools.…`, and
# this line is what makes that work when the command is run as a path. Both halves are
# forced: `python tools/build_lifecycle_lambda.py` puts tools/ on the path and not the
# repository root, while `pytest` puts the root on the path and not tools/. Importing it
# as `tools.build_admission_lambda` instead makes mypy see one file under two module
# names, which it refuses.
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
    "RECORDER_CONFIG",
    "RECORDER_ENTRYPOINT",
    "build_parser",
    "main",
]

#: Where the artifact is uploaded, and what `infra/batch-events.yaml` names as `S3Key`.
#: Its own prefix rather than a second file beside the validator's, so the two functions'
#: object versions cannot be confused for one another in the bucket listing.
ARTIFACT_KEY = "lifecycle-recorder/lifecycle-recorder.zip"

#: What the template's `Handler` must be. Printed with the digest so a release that
#: uploaded the right bytes under the wrong handler is visible in the same output.
HANDLER_ENTRY_POINT = "edullm_platform.lifecycle_handler.handler"

#: What this function imports, which is what its zip carries. Different from the
#: validator's, and deliberately passed rather than defaulted: the two handlers reach
#: different parts of the package, and a shared default would put each one's
#: dependencies into the other's release.
RECORDER_ENTRYPOINT = "edullm_platform.lifecycle_handler"

#: EMPTY, AND THAT IS THE MEASUREMENT RATHER THAN AN OVERSIGHT. An audit hook on CPython's
#: ``open`` event -- which fires below pathlib and yaml alike -- recorded no file under
#: ``config/`` being opened anywhere in this handler's test modules, and no module this
#: entrypoint carries names one. The recorder reads a Batch state-change event and writes a
#: lifecycle record; there is no policy for it to consult and no roster for it to resolve.
#:
#: Passed explicitly rather than defaulted to empty, so that a handler which does start
#: reading configuration has to say so here. ``tests/test_lambda_package_closure.py``
#: compares this against what the packaged modules name, in both directions.
RECORDER_CONFIG: frozenset[str] = frozenset()


def build_parser() -> argparse.ArgumentParser:
    """Named so ``tests/test_workflow_tool_arguments.py`` can import and read it.

    Extracted alongside the admission builder for the same reason and at the same time; the
    docstring there carries the account of what an invisible parser cost.
    """
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
            entrypoint=RECORDER_ENTRYPOINT,
            configuration=RECORDER_CONFIG,
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
