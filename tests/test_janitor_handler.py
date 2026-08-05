"""The handler between describe-instances and stop-instances, with a fake in place of EC2.

boto3 is not a project dependency -- it is in the Lambda runtime -- so the client is described
by a Protocol and injected. That is lifecycle_handler.py's arrangement and the reason for it is
the same: importing boto3 to test would put the whole SDK into pyproject.toml and therefore into
the admission validator's zip.
"""

from __future__ import annotations

from typing import Any

import pytest

from edullm_platform.janitor_handler import handler


class FakeEc2:
    def __init__(self, reservations: list[dict[str, Any]]) -> None:
        self._reservations = reservations
        self.stopped: list[str] = []
        self.tagged: list[tuple[str, str]] = []
        self.filters: list[Any] = []

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.filters.append(kwargs.get("Filters"))
        return {"Reservations": self._reservations}

    def stop_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.stopped.extend(kwargs["InstanceIds"])
        return {}

    def create_tags(self, **kwargs: Any) -> dict[str, Any]:
        for instance_id in kwargs["Resources"]:
            for tag in kwargs["Tags"]:
                self.tagged.append((instance_id, tag["Key"]))
        return {}


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
