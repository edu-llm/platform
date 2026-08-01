"""Hold the code AWS is running against the release records that claim to describe it.

Two committed tripwires already compare each release record against a zip built from this
tree, which settles record against tree.
``tests/test_phase2_lambda_package.py::test_the_released_zip_is_the_one_this_tree_builds``
is one and ``tests/test_phase3_lifecycle_package.py`` carries the other. Nothing settled
deployed against record. AWS reports the running code as ``CodeSha256``, base64 of the same
sha256 the records write as hex, and until this tool nothing had ever read it outside a
maintainer doing it by hand.

**The window that leaves open is one where CI is green and the account is not.** A function
deployed out of band, a release recorded from a tree that did not build it, or two people
releasing at once -- which happened on 2026-08-01, minutes apart, and was harmless only
because both built from the same commit -- all produce a repository whose records describe
something other than what is running. The validator's guarantees are the ones at stake:
roster membership, policy bounds, the approval gate and self-approval blocking would all be
enforced by whatever bytes are actually deployed, and nothing would say they were not the
reviewed ones. The recorder's drift is quieter still, because lineage written by the wrong
code looks exactly like lineage written by the right code.

**The two non-zero exits mean different things and a caller must not merge them.** Exit 1
says the account is running something the record does not describe, and sends a reader to a
deployment. Exit 2 says this check did not manage to look, and sends them to a credential or
a grant. Reporting the second as the first sends somebody hunting a release on the morning
an IAM stack lapsed; reporting it as a pass silently stops the check covering anything.

**It does not compare S3ObjectVersion, and that is a decision rather than an omission.** The
release records keep a copy of the object version the template's ``Code`` block names so the
two can be held together, and both tripwire modules already do exactly that -- between two
committed files, with no AWS identity, failing on the pull request that causes it rather
than on a schedule. Lambda could not contribute to that comparison in any case:
``GetFunctionConfiguration`` reports the digest of the deployed code and not the object
version it was deployed from, and the digest is the stronger question, because it is about
the bytes rather than about a pointer to them.

**Nothing this prints carries an account id.** An ``AccessDenied`` from Lambda names the
calling role's ARN and the resource ARN, both of which carry the number, and this runs in a
scheduled job whose log and step summary are public. Only the error code is repeated, which
is the actionable half and carries nothing.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import yaml

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

# The release tool is imported by its bare module name rather than as `tools.…`, for the
# reason tools/build_lifecycle_lambda.py gives at the same line: running this as a path puts
# tools/ on sys.path and not the repository root, while pytest does the opposite, and
# importing it both ways makes mypy see one file under two module names.
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from release_lambda import FUNCTIONS, Function

__all__ = [
    "DIGEST_PREFIX",
    "EXIT_DISAGREES",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "FUNCTIONS",
    "DeployedLambdaFinding",
    "build_parser",
    "check",
    "decode_code_sha256",
    "deployed_function_name",
    "main",
    "read_deployed_digest",
    "read_release_record",
]

EXIT_OK: Final = 0

#: The account is running code the record does not describe. A definite answer about the
#: account, which is why an absent function is here too rather than under EXIT_UNUSABLE:
#: the reader's next move is the same, and it is to go and look at what is deployed.
EXIT_DISAGREES: Final = 1

#: Nothing was read, so nothing is claimed. Never reported as a pass, because a check that
#: cannot look is not a check that found nothing.
EXIT_UNUSABLE: Final = 2

#: How much of a digest a passing line prints. The whole value was compared before the line
#: was written, so the line is reporting a match rather than offering one to be checked by
#: eye, and sixty-four characters twice a night is not read by anybody.
DIGEST_PREFIX: Final = 12

#: What a sha256 looks like written the way a release record writes it. Lowercase is part of
#: the shape rather than pedantry: an uppercase digest never compares equal to a decoded one
#: and would be reported as drift in the account, which sends a reader to AWS for a defect
#: in a file.
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")

#: How the CLI opens every service error. The code inside the brackets is a fixed token, and
#: it is the only part of the message repeated anywhere, because the rest of the line is an
#: ARN or two and every ARN carries the account id.
ERROR_CODE = re.compile(r"An error occurred \(([A-Za-z]+)\)")

#: What Lambda answers for a name that is not there. Read as a finding about the account
#: rather than as a failure to look.
NOT_FOUND_CODE = "ResourceNotFoundException"


class DeployedLambdaFinding(Exception):
    """One function is not what its record says, or could not be established to be.

    Carries a machine-readable reason first and a sentence naming what to do, the way the
    sibling verifiers do. ``code`` is the third field because this check has two ways to be
    non-zero and they send a reader to different places; it travels with the reason so that
    a new failure mode has to choose, rather than inheriting whichever the caller assumed.

    Everything a finding quotes is a path in this repository, a function name the account
    already publishes, a digest, or an AWS error code. None of it is an account id.
    """

    def __init__(self, reason: str, detail: str, *, code: int) -> None:
        self.reason = reason
        self.detail = detail
        self.code = code
        super().__init__(f"{reason}: {detail}")


def read_release_record(path: Path) -> str:
    """The sha256 the release record claims is deployed, as sixty-four lowercase hex."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DeployedLambdaFinding(
            "release_record_unusable",
            f"{_relative(path)} could not be read ({error.__class__.__name__}), so there is "
            "nothing to hold the deployed function against. It is committed, so a missing "
            "file means this was run from somewhere other than a checkout of this "
            "repository.",
            code=EXIT_UNUSABLE,
        ) from error
    except yaml.YAMLError as error:
        raise DeployedLambdaFinding(
            "release_record_unusable",
            f"{_relative(path)} did not parse as YAML, so the digest it records cannot be "
            "read. Repair the file; tools/release_lambda.py writes it and expects one "
            "`sha256:` line.",
            code=EXIT_UNUSABLE,
        ) from error

    digest = loaded.get("sha256") if isinstance(loaded, dict) else None
    if not isinstance(digest, str) or HEX_DIGEST.fullmatch(digest) is None:
        raise DeployedLambdaFinding(
            "release_record_unusable",
            f"{_relative(path)} carries no `sha256:` line holding sixty-four lowercase "
            "hexadecimal characters, so this check has nothing to compare and would "
            "otherwise report the account as wrong for a defect in a committed file. "
            "tools/release_lambda.py writes that line as part of cutting a release.",
            code=EXIT_UNUSABLE,
        )
    return digest


def deployed_function_name(template: Path) -> str:
    """The name the template gives CloudFormation, which is the name AWS knows.

    Read from the template rather than spelled here, so the name is decided in one place.
    A second spelling would survive a rename and leave this check asking about a function
    that no longer exists, which reports as an absent function and reads as a failed
    deployment rather than as a stale constant.
    """
    try:
        loaded = yaml.safe_load(template.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise DeployedLambdaFinding(
            "lambda_template_unusable",
            f"{_relative(template)} could not be read as YAML, so the name of the function "
            "it declares is unknown and there is nothing to ask AWS about.",
            code=EXIT_UNUSABLE,
        ) from error

    resources = loaded.get("Resources", {}) if isinstance(loaded, dict) else {}
    functions = [
        resource["Properties"]
        for resource in resources.values()
        if isinstance(resource, dict) and resource.get("Type") == "AWS::Lambda::Function"
    ]
    if len(functions) != 1:
        raise DeployedLambdaFinding(
            "lambda_template_unusable",
            f"{_relative(template)} declares {len(functions)} Lambda functions and this "
            "check reads one, so which one the release record beside it describes would be "
            "a guess. The release procedure has the same constraint, which "
            "tools/release_lambda.py states where it edits the template.",
            code=EXIT_UNUSABLE,
        )

    name = functions[0].get("FunctionName") if isinstance(functions[0], dict) else None
    if not isinstance(name, str) or not name:
        raise DeployedLambdaFinding(
            "lambda_template_unusable",
            f"{_relative(template)} declares a function with no explicit FunctionName, so "
            "CloudFormation names it and the name cannot be predicted from the tree. Every "
            "template here sets one, for the reason infra/README.md gives about role names.",
            code=EXIT_UNUSABLE,
        )
    return name


def decode_code_sha256(reported: str, *, function_name: str) -> str:
    """``CodeSha256`` as hex. AWS reports base64 of the bytes the record writes as hex."""
    value = reported.strip()
    if not value or value == "None":
        raise DeployedLambdaFinding(
            "deployed_lambda_unreadable",
            f"Lambda returned no CodeSha256 for {function_name}, so what it is running has "
            "not been read. That is not a statement that the deployment is wrong.",
            code=EXIT_UNUSABLE,
        )
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise DeployedLambdaFinding(
            "deployed_lambda_unreadable",
            f"the CodeSha256 Lambda reported for {function_name} is not base64, so it "
            "cannot be compared with a recorded digest. Comparing it unparsed would report "
            "a mismatch this check has no basis for claiming.",
            code=EXIT_UNUSABLE,
        ) from error
    if len(raw) != 32:
        raise DeployedLambdaFinding(
            "deployed_lambda_unreadable",
            f"the CodeSha256 Lambda reported for {function_name} decoded to {len(raw)} "
            "bytes rather than the thirty-two a sha256 occupies, so it is not the digest "
            "this check compares.",
            code=EXIT_UNUSABLE,
        )
    return raw.hex()


def read_deployed_digest(function_name: str, *, profile: str | None, region: str) -> str:
    """What AWS says the function is running, through the CLI.

    The CLI rather than boto3, for the reason tools/verify_wandb_credential.py gives: this
    project does not depend on an AWS SDK, and the two Lambda zips are size-limited enough
    that adding one to the runtime dependencies would be paid for by both functions.
    """
    call = [
        "aws",
        "lambda",
        "get-function-configuration",
        "--function-name",
        function_name,
        "--region",
        region,
        *(["--profile", profile] if profile else []),
        "--query",
        "CodeSha256",
        "--output",
        "text",
    ]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeployedLambdaFinding(
            "deployed_lambda_unreadable",
            f"asking Lambda about {function_name} did not complete "
            f"({error.__class__.__name__}), so nothing was read and nothing is claimed "
            "about what is deployed.",
            code=EXIT_UNUSABLE,
        ) from error

    if finished.returncode != 0:
        raise _refusal(function_name, finished.returncode, finished.stderr)
    return decode_code_sha256(finished.stdout, function_name=function_name)


def _refusal(function_name: str, status: int, stderr: str) -> DeployedLambdaFinding:
    """Turn a CLI failure into a finding, repeating the error code and nothing else.

    The message the CLI prints continues into the caller's ARN and the resource ARN, and
    both carry the account id. This runs in a scheduled job whose log is public and whose
    every committed capture masks that number, so the code inside the brackets is the only
    part repeated. It is also the part that decides what to do.
    """
    found = ERROR_CODE.search(stderr)
    code = found.group(1) if found else None

    if code == NOT_FOUND_CODE:
        return DeployedLambdaFinding(
            "deployed_lambda_absent",
            f"Lambda has no function called {function_name}, so the code the release "
            "record describes is not running: nothing is. Either the stack that declares "
            "it was never applied, or this was pointed at an account or a region other "
            "than the one it is deployed in, which answers identically and is worth ruling "
            "out first.",
            code=EXIT_DISAGREES,
        )

    named = f"{code} " if code else ""
    return DeployedLambdaFinding(
        "deployed_lambda_unreadable",
        f"asking Lambda about {function_name} was refused with {named}"
        f"(the CLI exited {status}), so what is deployed has not been read and this run "
        "says nothing about it either way. A denial here is usually the grant: the nightly "
        "reader needs lambda:GetFunctionConfiguration on this function, which "
        "infra/iam/nightly-reader-role.yaml declares and which is applied from a laptop "
        "like every IAM stack in infra/README.md. The full message is not printed because "
        "it names the calling and resource ARNs, and both carry the account id.",
        code=EXIT_UNUSABLE,
    )


def check(function: Function, *, profile: str | None, region: str) -> tuple[str, str]:
    """The function's deployed name and the digest both sides agree on.

    The record is read before AWS is called. There is nothing to compare an answer against
    otherwise, so the call would be a credential spent on a question the run cannot answer,
    and reading the local files first means a broken record fails the same way offline.
    """
    recorded = read_release_record(function.release_record)
    name = deployed_function_name(function.template)
    deployed = read_deployed_digest(name, profile=profile, region=region)

    if deployed != recorded:
        raise DeployedLambdaFinding(
            "deployed_lambda_is_not_the_release",
            f"{name} is running {deployed} and {_relative(function.release_record)} records "
            f"{recorded}, so the account is running code this tree does not describe. "
            f"Which side is out of step is what {function.tripwire} answers: it holds the "
            "record against a zip built from this tree, so while it is green the record "
            "still describes the tree and the account is the side to look at -- either "
            "something was deployed out of band, or the deploy this record was cut for has "
            "not landed yet, which CI does when the edited template reaches main.",
            code=EXIT_DISAGREES,
        )
    return name, recorded


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--function",
        choices=[*FUNCTIONS, "all"],
        default="all",
        help="which to check; both by default, because one config edit releases both",
    )
    # No default profile. The nightly runs on an assumed role and passes none, and a
    # default of `sbsandbox` would send it looking for an SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    selected = (
        list(FUNCTIONS.values())
        if options.function == "all"
        else [FUNCTIONS[options.function]]
    )

    findings: list[DeployedLambdaFinding] = []
    for function in selected:
        # Every function, rather than a return on the first finding. One edit under config/
        # is two releases, so the ordinary way for this to fail is for both to have drifted
        # together, and a report that stopped at the validator would have the recorder
        # repaired on a second morning after a second red run.
        #
        # Each answer is written where it happens and both streams are flushed, so the
        # order a reader sees is the order the functions were checked in. Collecting the
        # findings and printing them at the end put them ahead of a passing line written
        # earlier, because stdout is block-buffered into a pipe and stderr is not -- and a
        # log that reports the second function before the first is a log that gets
        # misattributed.
        try:
            name, digest = check(function, profile=options.profile, region=options.region)
        except DeployedLambdaFinding as finding:
            findings.append(finding)
            print(finding.reason, file=sys.stderr, flush=True)
            print(finding.detail, file=sys.stderr, flush=True)
            continue
        print(
            f"{name} is running {digest[:DIGEST_PREFIX]}, which is what "
            f"{_relative(function.release_record)} records.",
            flush=True,
        )

    if not findings:
        print("Every function checked is running the code its release record describes.")
        return EXIT_OK

    # A definite finding outranks an unanswered question. Somebody with one function to
    # repair has to repair it whatever happened to the other, and the other is printed
    # above rather than hidden behind the exit code.
    if any(reported.code == EXIT_DISAGREES for reported in findings):
        return EXIT_DISAGREES
    return EXIT_UNUSABLE


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
