"""The scope check, held to the two things that would let it pass while the defect is back.

``tools/verify_lifecycle_event_scope.py`` asks EventBridge whether the deployed lifecycle rule
can still deliver an event the recorder refuses. It cannot be unit-tested against the account,
and the parts of it worth testing are not the AWS calls anyway. They are the two ways it could
answer confidently and wrongly.

**It could be judging the pattern against an event it does not fill.** ``TestEventPattern``
answers "no match" for a clause on an absent field, so a pattern that grew a ``status``
requirement would read as a rule that delivers nothing -- and "delivers nothing" is the one
outcome a scope check is supposed to be relieved by. The tool guards this at run time by
comparing the pattern's field paths against what it assembles, and the guard only fires in the
audit; the test below fires at commit time, against the committed template.

**It could be asking a different question from the recorder.** The tool decides what the
recorder would refuse. If it did that with its own copy of the run id pattern, the two would
drift and the check would pass over exactly the events that dead-letter.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT_ROOT / "tools"
EVENTS_TEMPLATE = PROJECT_ROOT / "infra" / "batch-events.yaml"


def _load(name: str) -> ModuleType:
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    specification = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load("verify_lifecycle_event_scope")


def committed_pattern() -> dict[str, Any]:
    """The rule pattern as the template declares it, with intrinsics reduced to their shape.

    ``Fn::Sub`` is loaded as a plain mapping by the CloudFormation loader the rest of the
    suite uses, and the field-path walk in the tool descends into mappings. Each queue entry
    is therefore flattened to the string it renders to, so this reads the pattern's *fields*
    the way EventBridge would see them rather than the way YAML holds them.
    """
    loaded = yaml.load(
        EVENTS_TEMPLATE.read_text(encoding="utf-8"), Loader=_IntrinsicLoader
    )
    pattern = loaded["Resources"]["LifecycleRule"]["Properties"]["EventPattern"]
    detail = pattern["detail"]
    detail["jobQueue"] = [str(entry) for entry in detail["jobQueue"]]
    return dict(pattern)


class _IntrinsicLoader(yaml.SafeLoader):
    """Reads ``!Sub`` and friends as strings, so a pattern is a pattern and not a graph."""


def _intrinsic(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


for _tag in ("!Sub", "!Ref", "!GetAtt", "!ImportValue", "!Join", "!Select", "!Split"):
    _IntrinsicLoader.add_constructor(_tag, _intrinsic)


def test_the_tool_can_supply_every_field_the_committed_pattern_reads(tool: ModuleType) -> None:
    """Mutation: add ``status: [RUNNING]`` to the pattern's ``detail`` and change nothing else.

    That mutation deploys, matches, and turns this tool into a check that cannot fail. The
    assembled event carries no ``status``, EventBridge answers "no match" for every job, and
    the tool reports zero delivered -- which its own text calls a rule that delivers nothing
    and returns non-zero for, so the failure is loud rather than silent. It is still a check
    answering a question it can no longer answer, and the run-time guard for it lives in a
    daily job. This is the same comparison a day earlier.
    """
    unsupported = tool.fields_read_by(committed_pattern()) - tool.ASSEMBLED_FIELDS

    assert unsupported == frozenset(), (
        "the rule pattern tests a field verify_lifecycle_event_scope.py does not put in the "
        "event it hands TestEventPattern, so the check would answer about an absent value: "
        + ", ".join(sorted(".".join(path) for path in unsupported))
    )


def test_the_tool_asks_the_recorder_question_with_the_recorder_pattern(tool: ModuleType) -> None:
    """Mutation: give ``unreadable_by_the_recorder`` its own ``re.compile(r"run_.*")``.

    A second copy of what a run id looks like drifts from the first, and drifts in the
    direction that matters: a looser copy calls a name readable that the recorder refuses, so
    the check goes green over the events that dead-letter. The tool imports the compiled
    pattern rather than the string, so this compares the object.
    """
    from edullm_platform import lifecycle_projection
    from edullm_platform.contracts.identity import RUN_ID_REGEX

    assert tool.RUN_ID_REGEX is RUN_ID_REGEX
    assert lifecycle_projection.RUN_ID_REGEX is RUN_ID_REGEX


def test_the_tool_agrees_with_the_recorder_about_the_names_the_rule_admits(
    tool: ModuleType,
) -> None:
    """Mutation: return ``not job_name.startswith("run_")`` from ``unreadable_by_the_recorder``.

    The rule matches a wildcard and the recorder matches a shape, so the interesting names are
    the ones between them. EventBridge cannot say hexadecimal, so a name of the right shape in
    the wrong alphabet is admitted by the rule and refused by the recorder, and a prefix test
    would call it readable and report a clean scope over an event that dead-letters five
    times.
    """
    from edullm_platform.lifecycle_projection import UnreadableBatchEventError

    admitted_and_unreadable = "run_zzzzzzzz-zzzz-7zzz-zzzz-zzzzzzzzzzzz"
    a_real_run = "run_019fd520-999e-70d8-9003-1833aaa15247"

    assert tool.unreadable_by_the_recorder(admitted_and_unreadable) is True
    assert tool.unreadable_by_the_recorder(a_real_run) is False
    assert tool.unreadable_by_the_recorder("probe-mem") is True
    # And the recorder agrees, on the same two names, through the code that actually refuses.
    with pytest.raises(UnreadableBatchEventError, match="not a run id"):
        _project(admitted_and_unreadable)
    assert _project(a_real_run).event.run_id == a_real_run


def test_the_assembled_event_is_the_shape_test_event_pattern_requires(tool: ModuleType) -> None:
    """Mutation: drop ``resources`` from ``assembled_event``.

    ``TestEventPattern`` refuses an event missing any of ``id``, ``account``, ``region``,
    ``time``, ``source``, ``detail-type`` or ``resources`` with a ``ValidationException``, and
    the tool reads that as unusable and exits 2 -- an audit step that fails for a reason that
    has nothing to do with the scope. The first version of the tool had exactly this bug.
    """
    event = tool.assembled_event(
        job_name="run_019fd520-999e-70d8-9003-1833aaa15247",
        job_queue_arn="arn:aws:batch:us-east-1:123456789012:job-queue/sbsandbox-intern-edullm-cpu",
        job_arn="arn:aws:batch:us-east-1:123456789012:job/0000aaaa-0000-0000-0000-0000aaaa0000",
        account="123456789012",
        region="us-east-1",
    )

    assert set(event) == {
        "id",
        "account",
        "region",
        "time",
        "resources",
        "source",
        "detail-type",
        "detail",
    }
    assert event["source"] == tool.BATCH_SOURCE
    assert event["detail-type"] == tool.BATCH_DETAIL_TYPE


def test_a_content_filter_is_a_qualifier_and_not_another_field(tool: ModuleType) -> None:
    """Mutation: descend into the list under a field when walking the pattern.

    ``{"prefix": "run_"}`` sits inside the list a field's clause is, and reading it as a field
    named ``detail.jobName.prefix`` would make the guard above refuse a pattern that is
    entirely supported -- so the check would exit 2 every day and be turned off.
    """
    read = tool.fields_read_by(
        {
            "source": ["aws.batch"],
            "detail": {"jobName": [{"prefix": "run_"}], "jobQueue": ["arn:one"]},
        }
    )

    assert read == frozenset({("source",), ("detail", "jobName"), ("detail", "jobQueue")})


def _canned_account(
    *, rule_pattern: dict[str, Any], job_names: list[str], matches: set[str]
) -> Any:
    """One account, answering the four calls the tool makes, in the CLI's own shapes.

    Substituted for ``_aws``, which is the single place every AWS call goes through, so this
    drives the whole of :func:`main` rather than a branch that only exists for tests. Every
    test above this line reads a function; the three below run the tool.
    """

    def answer(arguments: list[str], *, profile: str | None, region: str) -> str:
        del profile, region
        service, action = arguments[0], arguments[1]
        if (service, action) == ("events", "describe-rule"):
            return json.dumps(rule_pattern)
        if (service, action) == ("sts", "get-caller-identity"):
            return "123456789012\n"
        if (service, action) == ("batch", "list-jobs"):
            if arguments[arguments.index("--job-status") + 1] != "RUNNING":
                return "[]"
            return json.dumps([[one, f"arn:aws:batch:::job/{one}"] for one in job_names])
        if (service, action) == ("events", "test-event-pattern"):
            event = json.loads(arguments[arguments.index("--event") + 1])
            return "True\n" if event["detail"]["jobName"] in matches else "False\n"
        raise AssertionError(f"the tool made an unexpected call: {service} {action}")

    return answer


A_RUN = "run_019fd520-999e-70d8-9003-1833aaa15247"
A_QUEUE = "arn:aws:batch:us-east-1:123456789012:job-queue/edullm-cpu"


def _pattern(job_name_clause: list[dict[str, str]] | None, **detail: Any) -> dict[str, Any]:
    inner: dict[str, Any] = {"jobQueue": [A_QUEUE], **detail}
    if job_name_clause is not None:
        inner["jobName"] = job_name_clause
    return {
        "source": ["aws.batch"],
        "detail-type": ["Batch Job State Change"],
        "detail": inner,
    }


def test_json_puts_one_document_on_stdout_and_nothing_else(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: print the success sentence to stdout under ``--json``.

    That is how this shipped. Every refusal already went to stderr, so ``--json | jq`` worked
    on every outcome except the good one, where the document was followed by a sentence and
    the parse failed. A flag that works until the answer is good is worse than no flag,
    because the failure arrives on the day nothing is wrong.

    This runs ``main`` rather than reading the source, over an account substituted at the one
    seam every AWS call goes through, so the success path is executed and its output parsed.
    """
    monkeypatch.setattr(
        tool,
        "_aws",
        _canned_account(
            rule_pattern=_pattern([{"wildcard": "run_*-*-7*-*-*"}]),
            job_names=[A_RUN, "probe-mem"],
            matches={A_RUN},
        ),
    )

    code = tool.main(["--json"])
    captured = capsys.readouterr()

    assert code == tool.EXIT_OK
    document = json.loads(captured.out)
    assert document["names_the_recorder_would_refuse"] == 0
    assert document["names_delivered_to_the_recorder"] == 1
    assert document["distinct_job_names"] == 2
    assert "delivers" in captured.err


def test_a_rule_that_matches_nothing_is_a_finding_and_not_a_pass(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: return ``EXIT_OK`` when nothing is delivered, since nothing is refused.

    This is the reading that makes the whole check useless, and it is the tempting one. No
    delivered event is refused, so the scope must be fine. A rule scoped to a renamed queue,
    and a ``jobName`` clause no run id satisfies, both look exactly like this, and both mean
    every run completes in Batch and writes nothing to lineage with nothing red anywhere.
    """
    monkeypatch.setattr(
        tool,
        "_aws",
        _canned_account(
            rule_pattern=_pattern([{"wildcard": "nothing_*"}]),
            job_names=[A_RUN],
            matches=set(),
        ),
    )

    code = tool.main([])

    assert code == tool.EXIT_DISAGREES
    assert "the_rule_delivers_nothing" in capsys.readouterr().err


def test_a_pattern_reading_a_field_the_event_lacks_stops_the_run(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: delete the ``ASSEMBLED_FIELDS`` guard from ``main`` and judge anyway.

    Without it the tool asks ``TestEventPattern`` about an event missing the field the pattern
    tests, gets "no match" for every job, and reports the rule as delivering nothing. That is
    a wrong answer arrived at confidently, and it would be read as the finding above rather
    than as a tool that can no longer answer.
    """
    monkeypatch.setattr(
        tool,
        "_aws",
        _canned_account(
            rule_pattern=_pattern(None, status=["RUNNING"]), job_names=[], matches=set()
        ),
    )

    code = tool.main([])
    captured = capsys.readouterr()

    assert code == tool.EXIT_UNUSABLE
    assert "pattern_reads_a_field_this_cannot_supply" in captured.err
    assert "detail.status" in captured.err


def _project(job_name: str) -> Any:
    from edullm_platform.lifecycle_projection import project_batch_event

    return project_batch_event(
        {
            "source": "aws.batch",
            "detail-type": "Batch Job State Change",
            "id": "11111111-2222-3333-4444-5555aaaa5555",
            "time": "2026-08-06T07:00:00Z",
            "detail": {
                "jobName": job_name,
                "jobId": "0000aaaa-0000-0000-0000-0000aaaa0000",
                "status": "RUNNING",
                "jobQueue": (
                    "arn:aws:batch:us-east-1:123456789012:job-queue/sbsandbox-intern-edullm-cpu"
                ),
            },
        }
    )
