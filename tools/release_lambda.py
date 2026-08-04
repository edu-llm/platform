"""Build a Lambda zip, upload it, and edit the two files that must agree — in one step.

**The procedure was three manual steps and the third is a template edit, which is exactly
the shape a person gets wrong at four in the morning.** `infra/README.md` documents it:
build the zip, `put-object` it, paste the returned version id into `S3ObjectVersion` and the
digest into the release record. Nothing enforced that all three happened, or that the values
pasted were the ones just produced.

It went wrong on 2026-08-01. A release was cut, the record edited, and a commit pushed in
one `&&` chain — where a `pytest | tail` in the middle succeeded as a *command* while the
test inside it failed, so the chain continued and `main` landed with a release record that
did not describe the tree. The tripwire was correct and red, on the branch everybody pulls.

So the three steps become one call that either does all of them or none, and the values are
carried in memory rather than copied by hand.

**A CATALOG EDIT WAS TWO RELEASES AND IS NOW ONE.** `build_package` copied `config/*.yaml`
into whatever zip it built and `tools/build_lifecycle_lambda.py` called that same function,
so a change to a config file moved the lifecycle recorder's digest too — even though
nothing the recorder does reads the catalog. Since 2026-08-04 each builder names the config
its own handler reads, and the recorder names none, so a catalog edit moves the validator's
digest alone.

``--function all`` remains the default anyway, and the reason has changed rather than gone.
A change under `src/edullm_platform` still reaches whichever functions import the module,
which is not something a person should be working out at the point of release. Building
both is cheap and deterministic: a function whose bytes did not move rebuilds to the
recorded digest and needs no edit, so releasing both never costs a release that was not
needed.

This uploads, which is a laptop step for the reason `infra/README.md` gives — an S3 write is
not applying a stack. It does not deploy. CI does that when the edited template reaches
`main`, and keeping the deploy on the far side of a review is the point of the template edit
being a diff somebody reads.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXIT_FAILED = 1
EXIT_UNUSABLE = 2

ARTIFACTS_BUCKET = "sbsandbox-intern-edullm-artifacts"


@dataclass(frozen=True)
class Function:
    """One deployed Lambda, and the four places its identity is written down."""

    name: str
    builder: str
    s3_key: str
    #: Where the deployed code is pinned. CloudFormation reads this, so it is what makes a
    #: code change a stack change rather than an empty change set.
    template: Path
    #: The record kept beside the template so the two can be compared. A version id edited
    #: in one and not the other is a template pointing at a zip nobody recorded.
    release_record: Path
    #: The test module holding this function's record against a zip built from the tree.
    #: Named on the function rather than looked up by display name, because two callers now
    #: need it: `verify` below runs it after a release, and tools/verify_deployed_lambdas.py
    #: cites it when the deployed digest disagrees -- it is what tells a stale record from
    #: an out-of-band deployment, and a reader who is not pointed at it cannot choose which
    #: side to change.
    tripwire: str


FUNCTIONS = {
    "validator": Function(
        name="admission validator",
        builder="tools/build_admission_lambda.py",
        s3_key="admission-validator/admission-validator.zip",
        template=PROJECT_ROOT / "infra" / "admission-state-machine.yaml",
        release_record=PROJECT_ROOT / "infra" / "admission-validator-release.yaml",
        tripwire="tests/test_phase2_lambda_package.py",
    ),
    "recorder": Function(
        name="lifecycle recorder",
        builder="tools/build_lifecycle_lambda.py",
        s3_key="lifecycle-recorder/lifecycle-recorder.zip",
        template=PROJECT_ROOT / "infra" / "batch-events.yaml",
        release_record=PROJECT_ROOT / "infra" / "lifecycle-recorder-release.yaml",
        tripwire="tests/test_phase3_lifecycle_package.py",
    ),
}


class ReleaseError(Exception):
    """Something in the chain refused, and nothing after it should be attempted."""


def _run(command: Sequence[str]) -> str:
    answer = subprocess.run(command, capture_output=True, text=True, check=False)
    if answer.returncode != 0:
        raise ReleaseError(
            f"`{' '.join(command[:3])} …` exited {answer.returncode}: "
            f"{answer.stderr.strip() or answer.stdout.strip()}"
        )
    return answer.stdout


def build(function: Function, destination: Path) -> str:
    """Build the zip and return its sha256, as the builder reports it.

    The builder is the authority on the digest rather than this tool hashing the file
    afterwards: it is what the tripwire test compares against, and two hashers are two
    chances to disagree.
    """
    output = _run(
        ["uv", "run", "python", function.builder, "--output", str(destination)]
    )
    try:
        recorded = json.loads(output)
    except ValueError as error:
        raise ReleaseError(f"{function.builder} did not print a JSON record") from error
    digest = recorded.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ReleaseError(f"{function.builder} reported no usable sha256")
    return digest


def upload(function: Function, source: Path, *, profile: str | None, region: str) -> str:
    version = _run(
        [
            "aws", "s3api", "put-object",
            "--bucket", ARTIFACTS_BUCKET,
            "--key", function.s3_key,
            "--body", str(source),
            "--content-type", "application/zip",
            *(["--profile", profile] if profile else []),
            "--region", region,
            "--query", "VersionId",
            "--output", "text",
        ]
    ).strip()
    if not version or version == "None":
        raise ReleaseError("the upload returned no VersionId, so the bucket may not be versioned")
    return version


def _substitute(path: Path, pattern: str, replacement: str) -> None:
    """Replace the one line that matches, and refuse when that is not exactly one line.

    Counted before substituting rather than read off the substitution, because ``re.subn``
    with ``count=1`` stops at the first match and reports 1 whether the file held one match
    or five. The obvious spelling therefore catches an absent line and silently picks a
    winner among several.

    Two matches is as ambiguous as none. A second Lambda added to a template would make this
    edit a coin flip, and a coin flip that lands wrong deploys one function's code under
    another's name.
    """
    text = path.read_text(encoding="utf-8")
    matches = len(re.findall(pattern, text, flags=re.MULTILINE))
    if matches != 1:
        raise ReleaseError(
            f"{path} carries {matches} line(s) matching {pattern!r} and this tool edits "
            "exactly one, so it cannot say which. Fix the file by hand and re-run."
        )
    path.write_text(
        re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE), encoding="utf-8"
    )


def record(function: Function, *, digest: str, version: str) -> None:
    """Write the same two values into both files, or leave neither changed.

    Both edits happen after both uploads have succeeded, so a failure partway does not
    leave a record describing a zip that was never stored.
    """
    _substitute(function.release_record, r"^s3_object_version: .*$", f"s3_object_version: {version}")
    _substitute(function.release_record, r"^sha256: .*$", f"sha256: {digest}")
    _substitute(function.template, r"^(\s*)S3ObjectVersion: .*$", rf"\1S3ObjectVersion: {version}")


def verify(selected: Sequence[Function]) -> None:
    """Run the tripwires themselves rather than trusting that the edits were right.

    This is the step whose absence caused the incident in the module docstring. It is run
    as a subprocess with its exit code checked directly -- not piped anywhere, because a
    pipe is what swallowed the failure last time.
    """
    targets = sorted({function.tripwire for function in selected})
    answer = subprocess.run(
        ["uv", "run", "pytest", "-q", *targets],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if answer.returncode != 0:
        raise ReleaseError(
            "the release tripwire is still red after the edits, so the deployed zip and "
            "this tree still disagree. Nothing was committed."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--function",
        choices=[*FUNCTIONS, "all"],
        default="all",
        help="which to release; both by default, because a config edit moves both digests",
    )
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and report the digests without uploading or editing anything",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    selected = (
        list(FUNCTIONS.values())
        if options.function == "all"
        else [FUNCTIONS[options.function]]
    )

    try:
        built: list[tuple[Function, Path, str]] = []
        for function in selected:
            destination = Path("/tmp") / Path(function.s3_key).name
            digest = build(function, destination)
            built.append((function, destination, digest))
            print(f"built   {function.name}: {digest[:12]}")

        if options.dry_run:
            print("dry run, so nothing was uploaded and no file was edited")
            return 0

        # Every upload before any edit. A record naming a zip that failed to upload is
        # worse than no record, because the tripwire would then pass against a lie.
        uploaded: list[tuple[Function, str, str]] = []
        for function, destination, digest in built:
            version = upload(function, destination, profile=options.profile, region=options.region)
            uploaded.append((function, digest, version))
            print(f"uploaded {function.name}: {version}")

        for function, digest, version in uploaded:
            record(function, digest=digest, version=version)
            print(f"recorded {function.name} in {function.release_record.name} and "
                  f"{function.template.name}")

        verify(selected)
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    print()
    print("Released. Nothing is deployed yet: commit the edited templates and let CI apply")
    print("them, which is what keeps the deploy on the far side of a review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
