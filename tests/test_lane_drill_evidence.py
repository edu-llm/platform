"""The drill's record, and the five things it has to say for the exploration route to be done.

A TEST OVER A COMMITTED FILE RATHER THAN OVER AWS. The drill is run by hand against a live
account and writes what it saw. This reads what it wrote, so the claim "a machine was reclaimed
without a human" survives in the tree rather than in somebody's memory of a terminal.

Mutation for the whole module: run the drill, watch it work, and commit nothing. That is the
state this repository has been in before, and it is what made an admission role's missing grants
invisible -- no capture of those roles was ever committed, so nothing could disagree with the
belief that they were fine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.lane import SCRATCH_BUCKET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECORD = PROJECT_ROOT / "fixtures" / "evidence" / "lane" / "drill.json"


def drill() -> dict[str, object]:
    if not RECORD.exists():
        pytest.skip(
            "the lane drill has not been run. tools/capture_lane_evidence.py writes this "
            "record and Task 10 of the exploration route plan is where it is run."
        )
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_a_machine_was_started_through_the_verb_and_not_by_hand() -> None:
    """Mutation: record an instance id somebody launched from the console.

    The claim is that a researcher gets a machine with one command. An instance that exists
    proves a machine; an instance whose launch carries the lane tag proves the command, because
    nothing but the verb writes that key.
    """
    record = drill()

    assert str(record["instance_id"]).startswith("i-")
    assert record["launched_by"] == "edullm run"
    assert record["lane_tag"]


def test_a_session_reached_it() -> None:
    """Mutation: record that the machine started and never connect to it.

    A machine nobody can reach is the failure this whole mechanism choice was made to avoid, and
    it is invisible from the launch: the instance runs, bills and answers nothing.
    """
    record = drill()

    assert record["agent_ping"] == "Online"
    assert record["session_id"]


def test_the_command_ran_on_the_machine_rather_than_the_session_merely_opening() -> None:
    """**THE DISTINCTION THE FIRST FOUR ATTEMPTS AT THIS DRILL DID NOT MAKE.**
    Mutation: assert the session id and stop, which is what the plan originally asked for.

    A session that opens and runs nothing looks identical to a session that works, from
    everywhere except the machine. It is what edullm run did for the whole of its life before
    2026-08-06: three separate defects each ended the session before the command, and every one
    of them reported only that the session had ended without saying what the command did. The
    sentinel is the verb's own evidence that the remote shell reached the end of the script.
    """
    record = drill()

    assert record["remote_command_ran"] is True
    assert record["remote_exit_status"] == 0


def test_a_file_written_on_the_machine_outlived_it() -> None:
    """**THE ANSWER TO "WHAT HAPPENS TO MY WORK".**
    Mutation: check that the object exists while the machine is still running.

    The working tier's whole purpose is that it survives the machine. Read after the stop, or
    the check proves only that a sync worked.
    """
    record = drill()

    assert str(record["work_object"]).startswith(f"s3://{SCRATCH_BUCKET}/")
    assert record["work_object_read_after_stop"] is True


def test_the_janitor_stopped_it_and_no_person_did() -> None:
    """**THE CONDITION THE WHOLE RECLAIM STORY RESTS ON.**
    Mutation: stop it by hand and record the stop.

    An expiry nobody enforces is worse than no expiry, because people plan around it. The
    CloudTrail principal on the StopInstances event is what tells the two apart, and it has to be
    the janitor's function role rather than a person's session.
    """
    record = drill()

    assert "janitor" in str(record["stopped_by"])
    assert record["final_state"] == "stopped"
    assert record["warned_before_stop"] is True


def test_the_record_carries_no_account_id() -> None:
    """Mutation: write the raw ARNs out.

    write_record in edullm_platform.capture_tooling refuses a record carrying an account id, and
    this asserts that the file which reached the tree honours that -- rather than the writer
    having been the only thing that ever checked.
    """
    from edullm_platform.evidence import ACCOUNT_ID_IN_FREE_TEXT

    if not RECORD.exists():
        pytest.skip("the lane drill has not been run")

    assert not ACCOUNT_ID_IN_FREE_TEXT.search(RECORD.read_text(encoding="utf-8"))
