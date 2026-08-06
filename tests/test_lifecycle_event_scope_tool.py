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
