"""That the two functions AWS is running are the ones the release records describe.

The tripwires in ``tests/test_phase2_lambda_package.py`` and
``tests/test_phase3_lifecycle_package.py`` hold each release record against a zip built from
this tree, which is one half of the chain. Nothing held the other half: AWS reports the
running code as ``CodeSha256``, and until this tool nothing compared it to anything. A
function deployed out of band, a release recorded from the wrong tree, or two releasers
racing each other all leave CI green over an account running code ``main`` does not
describe, and the validator's guarantees -- roster membership, policy bounds, the approval
gate, self-approval blocking -- would be absent with nothing saying so.

**The two non-zero exits are the point of the module rather than a detail of it.** "The
digests disagree" is a statement about the account and sends a reader to a deployment;
"I could not look" is a statement about this check and sends them to a credential or a
grant. A check that collapsed them would send somebody hunting a release on the morning an
IAM stack lapsed, so the cases below assert the two separately and assert that neither
reason appears in the other's output.

Nothing here reaches AWS. ``subprocess.run`` is replaced, which exercises the tool's own
decoding and error handling rather than only the comparison sitting on top of them.
"""

from __future__ import annotations

import base64
import importlib.util
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "verify_deployed_lambdas.py"

VALIDATOR = "sbsandbox-intern-edullm-admission-validator"
RECORDER = "sbsandbox-intern-edullm-lifecycle-recorder"

#: A digest of the right shape that is not either recorded one. Written out rather than
#: derived, so a test that stopped comparing anything cannot pass by comparing a value to
#: itself.
SOME_OTHER_DIGEST = "5" * 64

#: What the CLI prints when the call was refused. The real message continues with the
#: caller's ARN, which carries the account id; that is exactly what the tool must not
#: repeat into a public log, and `test_a_denial_does_not_put_the_account_id_in_the_log`
#: is the case that holds it to that.
DENIED = (
    "An error occurred (AccessDeniedException) when calling the GetFunctionConfiguration "
    "operation: User: arn:aws:sts::123456789012:assumed-role/"
    "sbsandbox-intern-edullm-nightly-reader/session is not authorized to perform: "
    "lambda:GetFunctionConfiguration on resource: "
    "arn:aws:lambda:us-east-1:123456789012:function:"
    "sbsandbox-intern-edullm-admission-validator"
)

NOT_FOUND = (
    "An error occurred (ResourceNotFoundException) when calling the "
    "GetFunctionConfiguration operation: Function not found: "
    "arn:aws:lambda:us-east-1:123456789012:function:"
    "sbsandbox-intern-edullm-admission-validator"
)


def load() -> Any:
    specification = importlib.util.spec_from_file_location("verify_deployed_lambdas", TOOL)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module() -> Any:
    return load()


@pytest.fixture
def recorded(module: Any) -> dict[str, str]:
    """The digest each release record claims, keyed by the deployed function name.

    Read from the committed records rather than written here, so the agreement case
    exercises the files the account is actually held against.
    """
    return {
        module.deployed_function_name(function.template): module.read_release_record(
            function.release_record
        )
        for function in module.FUNCTIONS.values()
    }


def as_aws_reports_it(hex_digest: str) -> str:
    """``CodeSha256`` is base64 of the same bytes the record writes as hex."""
    return base64.b64encode(bytes.fromhex(hex_digest)).decode()


def answer_lambda_with(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    replies: dict[str, tuple[int, str, str]],
) -> list[list[str]]:
    """Stand in for the CLI, answering per function so both can be reported at once.

    Returns the calls that were made, because a tool that asked about one function and
    reported on two would otherwise pass every case below.
    """
    calls: list[list[str]] = []

    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        name = command[command.index("--function-name") + 1]
        code, out, err = replies[name]
        return subprocess.CompletedProcess(args=list(command), returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def agreeing(recorded: dict[str, str]) -> dict[str, tuple[int, str, str]]:
    return {name: (0, as_aws_reports_it(digest) + "\n", "") for name, digest in recorded.items()}


def run_main(
    module: Any,
    capsys: pytest.CaptureFixture[str],
    argv: Sequence[str] = (),
) -> tuple[int, str, str]:
    code = int(module.main(list(argv)))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ----------------------------------------------------------------------------------------
# The claim holds
# ----------------------------------------------------------------------------------------


def test_a_deployed_digest_equal_to_the_recorded_one_is_the_whole_check(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    calls = answer_lambda_with(monkeypatch, module, agreeing(recorded))

    code, out, err = run_main(module, capsys)

    assert code == module.EXIT_OK, err
    assert err == ""
    for name in recorded:
        assert name in out
    assert len(calls) == len(recorded)


def test_the_call_asks_lambda_for_the_configuration_and_nothing_else(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: reach for get-function, which returns a presigned download of the code.

    The configuration call answers the whole question and is the only action the nightly
    reader role is granted. Widening it to `lambda:GetFunction` would hand a scheduled job
    a link to the deployed artifact, which is a different power than reading a digest.
    """
    calls = answer_lambda_with(monkeypatch, module, agreeing(recorded))

    run_main(module, capsys)

    for command in calls:
        assert command[:3] == ["aws", "lambda", "get-function-configuration"]
        assert "--query" in command
        assert command[command.index("--query") + 1] == "CodeSha256"


def test_the_digest_is_never_printed_in_full(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """A green line is read at a glance, and sixty-four characters twice is not that.

    Truncation is safe here in a way it would not be in the failure case: the tool has
    already compared the whole digest, and what the line reports is that it matched.
    """
    answer_lambda_with(monkeypatch, module, agreeing(recorded))

    _, out, _ = run_main(module, capsys)

    for digest in recorded.values():
        assert digest not in out
        assert digest[: module.DIGEST_PREFIX] in out


# ----------------------------------------------------------------------------------------
# The claim is false
# ----------------------------------------------------------------------------------------


def test_a_deployed_digest_that_is_not_the_recorded_one_fails(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """THE ONE THAT MATTERS. Mutation: report the difference and exit zero.

    There is no alerting on this platform, so a red scheduled run is the whole signal.
    """
    replies = agreeing(recorded)
    replies[VALIDATOR] = (0, as_aws_reports_it(SOME_OTHER_DIGEST) + "\n", "")
    answer_lambda_with(monkeypatch, module, replies)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "deployed_lambda_is_not_the_release" in err
    assert VALIDATOR in err
    # Both sides, because a message naming one of them leaves the reader to go and find
    # the other before they can tell which way the drift runs.
    assert SOME_OTHER_DIGEST in err
    assert recorded[VALIDATOR] in err
    # The tripwire is what tells a stale record from an out-of-band deployment, and a
    # reader who does not know that has no way to choose which side to change.
    assert "tests/test_phase2_lambda_package.py" in err


def test_both_functions_are_reported_rather_than_the_first_one_that_disagrees(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: return on the first finding.

    One config edit is two releases, so the ordinary way for this to fail is for both
    functions to have drifted together. A tool that stopped at the validator would have
    the recorder repaired on a second morning after a second red run.
    """
    replies = {
        name: (0, as_aws_reports_it(SOME_OTHER_DIGEST) + "\n", "") for name in recorded
    }
    calls = answer_lambda_with(monkeypatch, module, replies)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert len(calls) == len(recorded)
    for name in recorded:
        assert name in err
    assert err.count("deployed_lambda_is_not_the_release") == len(recorded)


def test_each_function_is_pointed_at_its_own_release_tripwire(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: name both tripwires in every message, which is one string and always true.

    It is also the message that stops being read. The two functions have different
    tripwires and a reader chasing a drifted recorder has no use for the validator's, so
    the citation is carried on the function rather than written into the sentence.
    """
    answer_lambda_with(
        monkeypatch,
        module,
        {name: (0, as_aws_reports_it(SOME_OTHER_DIGEST) + "\n", "") for name in recorded},
    )

    _, _, err = run_main(module, capsys, ["--function", "recorder"])

    assert "tests/test_phase3_lifecycle_package.py" in err
    assert "tests/test_phase2_lambda_package.py" not in err


def test_a_function_that_is_not_there_is_a_finding_about_the_account(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: treat a missing function as something that could not be checked.

    It could be checked, and the answer is that nothing is deployed. The reader's next
    move is the same as for a mismatch -- go and look at what is in the account -- which
    is what puts it on the same exit code and off the other one.
    """
    replies = agreeing(recorded)
    replies[RECORDER] = (255, "", NOT_FOUND)
    answer_lambda_with(monkeypatch, module, replies)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "deployed_lambda_absent" in err
    assert RECORDER in err
    assert "deployed_lambda_unreadable" not in err


# ----------------------------------------------------------------------------------------
# The check could not be made
# ----------------------------------------------------------------------------------------


def test_a_call_that_failed_is_read_as_neither_agreement_nor_disagreement(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: fold a failed call into the mismatch exit, or swallow it into a pass.

    Both are worse than a red run. Swallowing it means the check silently stops covering
    anything on the day the grant lapses, and folding it in sends whoever reads the
    failure looking for a release that never happened.
    """
    replies = agreeing(recorded)
    replies[VALIDATOR] = (255, "", DENIED)
    answer_lambda_with(monkeypatch, module, replies)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert "deployed_lambda_unreadable" in err
    assert "deployed_lambda_is_not_the_release" not in err
    assert "AccessDeniedException" in err
    # The remedy is a grant rather than a release, so the template that carries it is
    # named where the failure is read.
    assert "nightly-reader-role.yaml" in err


def test_a_denial_does_not_put_the_account_id_in_the_log(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: print the CLI's stderr, which is the obvious way to be helpful.

    An AccessDenied from Lambda names the calling role's ARN and the resource ARN, and
    both carry the account id. This job writes to a scheduled log and a step summary in a
    public repository, and every committed capture in this repository masks that number.
    The error code is the actionable half and carries nothing.
    """
    replies = agreeing(recorded)
    replies[VALIDATOR] = (255, "", DENIED)
    answer_lambda_with(monkeypatch, module, replies)

    _, out, err = run_main(module, capsys)

    assert "123456789012" not in out + err
    assert "assumed-role" not in out + err


def test_a_disagreement_outranks_a_call_that_could_not_be_made(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Both are printed, and the exit is the one that names a definite finding.

    A reader who has one function to repair has to repair it whatever happened to the
    other, and the other is on the line above rather than hidden behind the exit code.
    """
    answer_lambda_with(
        monkeypatch,
        module,
        {
            VALIDATOR: (0, as_aws_reports_it(SOME_OTHER_DIGEST) + "\n", ""),
            RECORDER: (255, "", DENIED),
        },
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "deployed_lambda_is_not_the_release" in err
    assert "deployed_lambda_unreadable" in err


@pytest.mark.parametrize(
    ("stdout", "why"),
    [
        ("", "the query matched nothing and the CLI printed a blank line"),
        ("None\n", "the field was absent, which --output text renders as the word None"),
        ("not base64 at all!\n", "the value did not decode"),
        (base64.b64encode(b"too short").decode() + "\n", "the value decoded to the wrong length"),
    ],
    ids=["empty", "none", "undecodable", "wrong-length"],
)
def test_an_answer_that_is_not_a_digest_is_unusable_rather_than_a_mismatch(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
    stdout: str,
    why: str,
) -> None:
    """A call that returned something unreadable is still a call that read nothing.

    Comparing an unparsed value against the record would report a mismatch, which is a
    claim about the account that this tool has no basis for making.
    """
    replies = agreeing(recorded)
    replies[VALIDATOR] = (0, stdout, "")
    answer_lambda_with(monkeypatch, module, replies)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE, why
    assert "deployed_lambda_unreadable" in err
    assert "deployed_lambda_is_not_the_release" not in err


def test_a_cli_that_is_not_installed_is_unusable(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise OSError("no aws on PATH")

    monkeypatch.setattr(module.subprocess, "run", run)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert "deployed_lambda_unreadable" in err


# ----------------------------------------------------------------------------------------
# The record, and the template that names the function
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("schema_version: 1\n", "no sha256 field at all"),
        ("schema_version: 1\nsha256:\n", "the field is present and empty"),
        ("schema_version: 1\nsha256: e0e420a5\n", "a truncated digest"),
        (f"schema_version: 1\nsha256: {'g' * 64}\n", "sixty-four characters that are not hex"),
        (f"schema_version: 1\nsha256: {'E0' * 32}\n", "uppercase, which never compares equal"),
        ("- not a mapping\n", "a list where a mapping belongs"),
        ("sha256: [1, 2]\n", "a list where the digest belongs"),
        ("{unclosed\n", "not YAML"),
    ],
    ids=["absent", "empty", "truncated", "not-hex", "uppercase", "not-mapping", "not-str", "not-yaml"],
)
def test_a_release_record_that_does_not_carry_one_digest_is_unusable(
    module: Any, tmp_path: Path, body: str, why: str
) -> None:
    """Mutation: read the field and compare whatever comes back.

    A record the tool cannot read is not a record saying the deployment is wrong, and it
    is not one saying it is right either. Uppercase is in the list because it is the one
    that would otherwise compare unequal and be reported as drift in the account, which
    sends a reader to AWS for a defect in a file.
    """
    record = tmp_path / "release.yaml"
    record.write_text(body, encoding="utf-8")

    with pytest.raises(module.DeployedLambdaFinding) as raised:
        module.read_release_record(record)

    assert raised.value.reason == "release_record_unusable", why
    assert raised.value.code == module.EXIT_UNUSABLE


def test_a_release_record_that_is_not_there_is_unusable(module: Any, tmp_path: Path) -> None:
    with pytest.raises(module.DeployedLambdaFinding) as raised:
        module.read_release_record(tmp_path / "absent.yaml")

    assert raised.value.reason == "release_record_unusable"
    assert raised.value.code == module.EXIT_UNUSABLE


def test_an_unusable_record_stops_the_account_being_asked_about(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    """Mutation: call AWS first and read the record afterwards.

    There is nothing to compare the answer against, so the call is a credential spent on
    a question this run cannot answer. Reading the local file first also means a broken
    record fails identically with no network at all.
    """
    calls = answer_lambda_with(monkeypatch, module, agreeing(recorded))
    monkeypatch.setattr(
        module,
        "read_release_record",
        _raising(module, "release_record_unusable", module.EXIT_UNUSABLE),
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert calls == []
    assert "release_record_unusable" in err


def _raising(module: Any, reason: str, code: int) -> Callable[..., str]:
    def refuse(*_: object, **__: object) -> str:
        raise module.DeployedLambdaFinding(reason, "as it happens, no", code=code)

    return refuse


def test_the_deployed_name_is_read_from_the_template_that_declares_it(module: Any) -> None:
    """Mutation: spell the two function names in this tool as well.

    The template is what tells CloudFormation the name, so it is the one place the name is
    decided. A second spelling here would let a rename deploy cleanly and leave this check
    asking about a function that no longer exists -- reported as an absent function, which
    reads as a failed deployment rather than as a stale constant.
    """
    names = {
        module.deployed_function_name(function.template)
        for function in module.FUNCTIONS.values()
    }

    assert names == {VALIDATOR, RECORDER}


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("Resources: {}\n", "no function in the template"),
        (
            (
                "Resources:\n"
                "  One:\n"
                "    Type: AWS::Lambda::Function\n"
                "    Properties:\n"
                "      FunctionName: one\n"
                "  Two:\n"
                "    Type: AWS::Lambda::Function\n"
                "    Properties:\n"
                "      FunctionName: two\n"
            ),
            "two functions, so which one this record describes is a guess",
        ),
        (
            (
                "Resources:\n"
                "  One:\n"
                "    Type: AWS::Lambda::Function\n"
                "    Properties:\n"
                "      Handler: x\n"
            ),
            "a function CloudFormation names for itself, which this cannot predict",
        ),
    ],
    ids=["none", "two", "unnamed"],
)
def test_a_template_that_does_not_name_one_function_is_unusable(
    module: Any, tmp_path: Path, body: str, why: str
) -> None:
    template = tmp_path / "template.yaml"
    template.write_text(body, encoding="utf-8")

    with pytest.raises(module.DeployedLambdaFinding) as raised:
        module.deployed_function_name(template)

    assert raised.value.reason == "lambda_template_unusable", why
    assert raised.value.code == module.EXIT_UNUSABLE


# ----------------------------------------------------------------------------------------
# Which functions are covered, and which comparison belongs somewhere else
# ----------------------------------------------------------------------------------------


def test_every_function_the_release_tool_can_release_is_checked(module: Any) -> None:
    """Mutation: list the two functions here instead of importing them.

    `tools/release_lambda.py` already declares each function's template and release
    record, and it is what a third Lambda would be added to. Reading its table means the
    third one is verified from the day it can be released, rather than from the day
    somebody remembers this file.

    Imported by bare name, which is the spelling the tool itself uses and therefore the
    one that resolves to the same module object; `tools.release_lambda` is a second entry
    in `sys.modules` holding an equal table that is not the same table.
    """
    import release_lambda

    assert module.FUNCTIONS is release_lambda.FUNCTIONS
    assert set(module.FUNCTIONS) == {"validator", "recorder"}


def test_the_object_version_comparison_is_owned_by_the_release_tripwires() -> None:
    """WHY THIS TOOL DOES NOT COMPARE S3ObjectVersion, asserted rather than left in a PR.

    The release record keeps a copy of the object version the template's `Code` block
    names, so that a version id edited in one place and not the other is visible. That
    comparison is between two committed files: it needs no AWS identity, it fails on the
    change that causes it, and it already exists once per function in the two tripwire
    modules. Re-implementing it here would move it to a schedule and put the same
    property in two places, which is the arrangement
    `tests/test_phase3_lifecycle_package.py` argues against in its own docstring.

    Lambda could not answer it in any case. `GetFunctionConfiguration` reports the digest
    of the code and not the S3 object version it was deployed from, so "deployed against
    record" has no object-version half to check; the digest settles the bytes, which is
    the stronger of the two questions.

    Reading the citations rather than trusting them, because the reason this tool omits
    the comparison is that those two tests make it, and a deletion or a rename there
    would leave nothing making it at all.
    """
    import test_phase2_lambda_package
    import test_phase3_lifecycle_package

    assert callable(
        test_phase2_lambda_package.test_the_template_pins_the_object_the_release_record_names
    )
    assert callable(
        test_phase3_lifecycle_package.test_the_template_and_the_record_name_the_same_object
    )


def test_the_tool_exposes_a_parser(module: Any) -> None:
    parser = module.build_parser()

    assert parser.parse_args([]).profile is None, (
        "the nightly runs on an assumed role and passes no profile, so a default here "
        "would send it looking for a laptop's SSO session"
    )
    assert parser.parse_args([]).region == "us-east-1"
    assert parser.parse_args([]).function == "all"
    assert parser.parse_args(["--function", "recorder"]).function == "recorder"


def test_one_function_can_be_asked_about_on_its_own(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: dict[str, str],
) -> None:
    calls = answer_lambda_with(monkeypatch, module, agreeing(recorded))

    code, out, _ = run_main(module, capsys, ["--function", "recorder"])

    assert code == module.EXIT_OK
    assert len(calls) == 1
    assert RECORDER in out
    assert VALIDATOR not in out
