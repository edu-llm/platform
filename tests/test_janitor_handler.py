"""The handler between describe-instances and stop-instances, with a fake in place of EC2.

boto3 is not a project dependency -- it is in the Lambda runtime -- so the client is described
by a Protocol and injected. That is lifecycle_handler.py's arrangement and the reason for it is
the same: importing boto3 to test would put the whole SDK into pyproject.toml and therefore into
the admission validator's zip.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from edullm_platform.janitor_handler import SweepIncomplete, handler


class Refused(Exception):
    """The shape botocore's ClientError presents to a caller that cannot import it.

    A message that reads like a sentence and a ``response`` mapping carrying the code, which
    is exactly what the handler reads off it. Built by hand rather than imported because
    botocore is in the Lambda runtime and not in pyproject.toml -- the same reason the client
    itself is a Protocol -- so a test that imported ClientError to raise it would be testing a
    dependency the deployed function's own package does not declare.
    """

    def __init__(self, instance_id: str, operation: str, code: str) -> None:
        super().__init__(
            f"An error occurred ({code}) when calling the {operation} operation: "
            f"The instance '{instance_id}' may not be stopped. Modify its 'disableApiStop' "
            "instance attribute and try again."
        )
        self.response = {"Error": {"Code": code, "Message": "may not be stopped"}}


class FakeEc2:
    def __init__(
        self,
        reservations: list[dict[str, Any]],
        *,
        refuses: dict[str, str] | None = None,
    ) -> None:
        self._reservations = reservations
        #: instance id -> AWS error code the account answers any write on it with.
        self._refuses = refuses or {}
        self.stopped: list[str] = []
        self.tagged: list[tuple[str, str]] = []
        self.filters: list[Any] = []
        #: Every mutating request, as the list of ids it carried. What distinguishes one call
        #: per machine from one call for all of them, which is the whole of the repair.
        self.write_calls: list[tuple[str, tuple[str, ...]]] = []

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.filters.append(kwargs.get("Filters"))
        return {"Reservations": self._reservations}

    def stop_instances(self, **kwargs: Any) -> dict[str, Any]:
        identifiers = tuple(kwargs["InstanceIds"])
        self.write_calls.append(("stop_instances", identifiers))
        self._refuse_any_of(identifiers, "StopInstances")
        self.stopped.extend(identifiers)
        return {}

    def create_tags(self, **kwargs: Any) -> dict[str, Any]:
        identifiers = tuple(kwargs["Resources"])
        self.write_calls.append(("create_tags", identifiers))
        self._refuse_any_of(identifiers, "CreateTags")
        for instance_id in identifiers:
            for tag in kwargs["Tags"]:
                self.tagged.append((instance_id, tag["Key"]))
        return {}

    def _refuse_any_of(self, identifiers: tuple[str, ...], operation: str) -> None:
        """Refuse the whole request if any id in it is refused, and act on none of it.

        THIS IS THE BEHAVIOUR UNDER TEST AND IT IS THE REAL API'S. StopInstances and
        CreateTags both validate every instance in a request before acting on any of them, so
        a batch containing one machine the account will not touch fails entirely -- observed
        live on 2026-08-06, where a stop-protected machine kept an ordinary expired one
        running for two sweeps. A fake that refused only the bad id would let a batching
        handler pass every test here.
        """
        for instance_id in identifiers:
            code = self._refuses.get(instance_id)
            if code is not None:
                raise Refused(instance_id, operation, code)


def reservation(instance_id: str, tags: dict[str, str], state: str = "running") -> dict[str, Any]:
    return {
        "Instances": [
            {
                "InstanceId": instance_id,
                "State": {"Name": state},
                "Tags": [{"Key": key, "Value": value} for key, value in tags.items()],
            }
        ]
    }


@pytest.fixture(autouse=True)
def lane_settings_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The three numbers infra/expiry-janitor.yaml sets on the deployed function.

    The handler reads them from the environment and never from disk, because the zip carries no
    configuration -- see tools/build_janitor_lambda.py's docstring for why. Set here so the
    tests exercise the same path production does; a test that patched load_lane_settings would
    be exercising a path the deployed function never takes.
    """
    monkeypatch.setenv("EDULLM_DEFAULT_LIFETIME_HOURS", "8")
    monkeypatch.setenv("EDULLM_WARNING_LEAD_MINUTES", "30")
    monkeypatch.setenv("EDULLM_SWEEP_MINUTES", "5")


def test_a_handler_with_no_settings_in_the_environment_refuses_rather_than_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: default the warning lead when the variable is unset.

    A default here is a second copy of a number that lives in config/reports/researcher-lane.yaml,
    and the copy that wins is the one nobody reviewed. Refusing means a function deployed without
    its environment fails on its first sweep with a message naming the variable, rather than
    sweeping on numbers somebody guessed.
    """
    monkeypatch.delenv("EDULLM_WARNING_LEAD_MINUTES")

    with pytest.raises(KeyError, match="EDULLM_WARNING_LEAD_MINUTES"):
        handler({}, None, client=FakeEc2([]))


def test_a_machine_past_its_expiry_and_already_warned_is_stopped() -> None:
    """Mutation: call stop_instances for every decision rather than for the STOP ones."""
    client = FakeEc2(
        [
            reservation(
                "i-0000000000000aaaa",
                {
                    "Project": "mixlaw",
                    "ExpiresAt": "2020-01-01T00:00:00Z",
                    "edullm:expiry-warned-at": "2019-12-31T23:00:00Z",
                },
            )
        ]
    )

    summary = handler({}, None, client=client)

    assert client.stopped == ["i-0000000000000aaaa"]
    assert client.tagged == []
    assert summary["stopped"] == 1


def test_a_machine_near_its_expiry_is_tagged_and_not_stopped() -> None:
    """Mutation: write the warning tag and stop in the same sweep.

    The whole of "warns before it stops anything" is that these are two sweeps. A handler that
    did both would satisfy every unit test in tests/test_expiry_janitor.py and destroy work.
    """
    client = FakeEc2(
        [
            reservation(
                "i-0000000000000bbbb", {"Project": "mixlaw", "ExpiresAt": "2020-01-01T00:00:00Z"}
            )
        ]
    )

    summary = handler({}, None, client=client)

    assert client.stopped == []
    assert client.tagged == [("i-0000000000000bbbb", "edullm:expiry-warned-at")]
    assert summary["warned"] == 1


def test_a_machine_that_is_not_ours_is_neither_tagged_nor_stopped() -> None:
    """Mutation: drop the tag filter and act on everything running.

    This is a shared account. The reservation below is the shape of somebody else's machine.
    """
    client = FakeEc2([reservation("i-0000000000000cccc", {"Name": "mcat-dev-worker"})])

    summary = handler({}, None, client=client)

    assert client.stopped == []
    assert client.tagged == []
    assert summary["skipped"] == 1


def test_the_summary_counts_every_instance_examined() -> None:
    """Mutation: report only what was acted on.

    A denominator is what distinguishes a quiet morning from a filter that matches nothing,
    which is the same argument the instruments slice makes about the mismatch list.
    """
    client = FakeEc2(
        [
            reservation(
                "i-0000000000000aaaa", {"Project": "mixlaw", "ExpiresAt": "2099-01-01T00:00:00Z"}
            ),
            reservation("i-0000000000000cccc", {"Name": "mcat-dev-worker"}),
        ]
    )

    summary = handler({}, None, client=client)

    assert summary["examined"] == 2
    assert summary["warned"] + summary["stopped"] + summary["left"] + summary["skipped"] == 2


def test_the_sweep_reads_only_what_could_still_be_costing_anything() -> None:
    """Mutation: drop the Filters argument and page the whole account.

    A shared account keeps terminated instances visible for an hour and stopped ones for ever,
    so an unfiltered describe pages through machines whose only possible decision is LEAVE. The
    filter is also what keeps the denominator meaningful: "examined" should be the number of
    machines that could have been stopped, not the number that have ever existed here.
    """
    client = FakeEc2([])

    handler({}, None, client=client)

    assert client.filters == [[{"Name": "instance-state-name", "Values": ["running", "stopping"]}]]


EXPIRED_AND_WARNED = {
    "Project": "mixlaw",
    "ExpiresAt": "2020-01-01T00:00:00Z",
    "edullm:expiry-warned-at": "2019-12-31T23:00:00Z",
}
#: The unstoppable machine of the 2026-08-06 drill: ours, expired, warned, and carrying
#: DisableApiStop. `i-0f1d30b389c714760` was the real one.
PROTECTED = "i-0000000000000dead"
#: The ordinary machine that shared its sweep and kept running because of it. `i-07ce0a937ab612e66`.
ORDINARY = "i-0000000000000a2a2"


def a_sweep_with_a_protected_machine_and_an_ordinary_one() -> FakeEc2:
    """The exact case that found the defect, in the order the account returned it.

    The protected machine first, so a handler that stops them one at a time in the order it
    decided them meets the refusal before it reaches the machine it can stop. A test that put
    the good one first would pass on a handler that gave up at the first error.
    """
    return FakeEc2(
        [
            reservation(PROTECTED, EXPIRED_AND_WARNED),
            reservation(ORDINARY, EXPIRED_AND_WARNED),
        ],
        refuses={PROTECTED: "OperationNotPermitted"},
    )


def test_a_machine_that_cannot_be_stopped_does_not_keep_an_expired_one_running() -> None:
    """Mutation: stop them in one batched call, which is what this handler used to do.

    Measured on 2026-08-06 rather than imagined. `StopInstances` validates every id in a
    request before acting on any of them, so a machine carrying DisableApiStop refused the
    stop of a perfectly ordinary expired machine that shared its sweep, and went on refusing
    it for as long as it existed. One machine must not be able to stop the service reclaiming
    the others, so the blast radius of a refusal is that one machine.
    """
    client = a_sweep_with_a_protected_machine_and_an_ordinary_one()

    with pytest.raises(SweepIncomplete) as refusal:
        handler({}, None, client=client)

    assert client.stopped == [ORDINARY]
    assert client.write_calls == [
        ("stop_instances", (PROTECTED,)),
        ("stop_instances", (ORDINARY,)),
    ]
    assert refusal.value.summary["stops_completed"] == 1


def test_the_whole_sweep_is_reported_even_though_part_of_it_was_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: print the summary after the stops rather than before the raise.

    That was the original order and it is the more important half of this defect. Two sweeps
    on 2026-08-06 printed nothing at all -- not what they examined, not what they warned, not
    the machine they did stop -- because the summary sat behind a call that raised. A reclaim
    service whose failure mode is producing no output is indistinguishable from a quiet night,
    which is the failure this component exists to prevent.
    """
    client = FakeEc2(
        [
            reservation(PROTECTED, EXPIRED_AND_WARNED),
            reservation(ORDINARY, EXPIRED_AND_WARNED),
            reservation(
                "i-0000000000000bbbb", {"Project": "mixlaw", "ExpiresAt": "2020-01-01T00:00:00Z"}
            ),
            reservation("i-0000000000000cccc", {"Name": "mcat-dev-worker"}),
        ],
        refuses={PROTECTED: "OperationNotPermitted"},
    )

    with pytest.raises(SweepIncomplete):
        handler({}, None, client=client)

    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["examined"] == 4
    assert printed["warned"] == 1
    assert printed["warnings_written"] == 1
    assert printed["stopped"] == 2
    assert printed["stops_completed"] == 1
    assert printed["skipped"] == 1
    assert [one["instance_id"] for one in printed["refusals"]] == [PROTECTED]
    assert "swept_at" in printed


def test_a_machine_it_could_not_stop_fails_the_sweep_rather_than_rolling_over() -> None:
    """Mutation: return the summary when there are refusals instead of raising after it.

    The question this answers is what should happen to a machine that cannot be stopped, and
    "skip it forever" is the wrong answer. A machine that is ours, expired, warned and
    unstoppable is somebody's problem to fix; if the sweep returns cleanly, the only trace is
    a log line in a service nobody reads because it is working, and the machine bills for a
    week. Failing the invocation after the summary is printed puts it on the one signal a
    scheduled function has that anybody can alarm on, and it clears itself the moment the
    machine does.
    """
    client = a_sweep_with_a_protected_machine_and_an_ordinary_one()

    with pytest.raises(SweepIncomplete) as refusal:
        handler({}, None, client=client)

    assert PROTECTED in str(refusal.value)
    assert "OperationNotPermitted" in str(refusal.value)


def test_a_refusal_names_the_instance_and_the_code_rather_than_counting() -> None:
    """Mutation: record refusals as a number and drop the list.

    A count says a sweep is degraded and not which machine to go and look at, and the two
    kinds of refusal want different people: OperationNotPermitted is a machine somebody
    configured, UnauthorizedOperation is the grant having stopped matching the filter. The
    code is groupable and the message is a sentence, so both are recorded and the code is what
    an alarm counts.
    """
    client = a_sweep_with_a_protected_machine_and_an_ordinary_one()

    with pytest.raises(SweepIncomplete) as refusal:
        handler({}, None, client=client)

    assert refusal.value.summary["refusals"] == [
        {
            "instance_id": PROTECTED,
            "action": "stop",
            "code": "OperationNotPermitted",
            "detail": (
                "An error occurred (OperationNotPermitted) when calling the StopInstances "
                f"operation: The instance '{PROTECTED}' may not be stopped. Modify its "
                "'disableApiStop' instance attribute and try again."
            ),
        }
    ]


def test_a_warning_that_cannot_be_written_costs_neither_the_other_warnings_nor_the_stops() -> None:
    """Mutation: write the warning tags in one batched create_tags call.

    `CreateTags` batches the same way `StopInstances` does, and the machine that breaks it is
    ordinary rather than perverse: one terminated between the describe and the tag answers
    InvalidInstanceID.NotFound for the whole request. Batched, that costs every other
    machine's warning *and* every stop in the sweep, because the tag call runs first. The
    repair has to be the same repair in both places or the silence just moves.
    """
    vanished = "i-0000000000000f00d"
    client = FakeEc2(
        [
            reservation(vanished, {"Project": "mixlaw", "ExpiresAt": "2020-01-01T00:00:00Z"}),
            reservation(
                "i-0000000000000bbbb", {"Project": "mixlaw", "ExpiresAt": "2020-01-01T00:00:00Z"}
            ),
            reservation(ORDINARY, EXPIRED_AND_WARNED),
        ],
        refuses={vanished: "InvalidInstanceID.NotFound"},
    )

    with pytest.raises(SweepIncomplete) as refusal:
        handler({}, None, client=client)

    assert client.tagged == [("i-0000000000000bbbb", "edullm:expiry-warned-at")]
    assert client.stopped == [ORDINARY]
    assert refusal.value.summary["warnings_written"] == 1
    assert refusal.value.summary["refusals"][0]["code"] == "InvalidInstanceID.NotFound"


def test_a_decision_line_says_what_came_of_it_and_not_only_what_was_decided() -> None:
    """Mutation: report the decision without the outcome beside it.

    `action: stop` is what the sweep judged and reads, to anybody scanning a log, as a machine
    that was stopped. For two sweeps on 2026-08-06 it would have been a machine that was still
    running. The two fields disagree exactly when somebody needs to know they do.
    """
    client = a_sweep_with_a_protected_machine_and_an_ordinary_one()

    with pytest.raises(SweepIncomplete) as refusal:
        handler({}, None, client=client)

    lines = {
        one["instance_id"]: one
        for one in refusal.value.summary["decisions"]  # type: ignore[attr-defined]
    }
    assert lines[PROTECTED]["action"] == "stop"
    assert lines[PROTECTED]["outcome"] == "refused"
    assert lines[ORDINARY]["outcome"] == "done"


def test_a_sweep_with_nothing_to_do_makes_no_write_call_and_still_returns() -> None:
    """Mutation: call stop_instances or create_tags with an empty id list anyway.

    One call per machine acted on, and no machine means no call. This is the cost claim: a
    sweep examining eighteen instances with none at their expiry is the same single describe
    it always was, and the per-machine calls are bought only where there is something to buy.
    """
    client = FakeEc2(
        [
            reservation(
                "i-0000000000000aaaa", {"Project": "mixlaw", "ExpiresAt": "2099-01-01T00:00:00Z"}
            ),
            reservation("i-0000000000000cccc", {"Name": "mcat-dev-worker"}),
        ]
    )

    summary = handler({}, None, client=client)

    assert client.write_calls == []
    assert summary["refused"] == 0
    assert summary["stops_completed"] == 0


def test_a_sweep_that_found_nothing_still_reports_a_denominator() -> None:
    """Mutation: return early when describe-instances comes back empty.

    Zero examined and no output at all are the two states an operator most needs told apart: a
    quiet account against a janitor whose filter, credentials or schedule have stopped working.
    The second is silent by construction, so the empty sweep has to say so.
    """
    summary = handler({}, None, client=FakeEc2([]))

    assert summary["examined"] == 0
    assert summary["decisions"] == []
    assert "swept_at" in summary
