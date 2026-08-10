"""The button that hands a capacity block node back, and the one property it rests on.

**IT CANNOT KILL LIVE WORK, AND NOTHING IN THE WORKFLOW OR THE TOOL IS WHAT MAKES THAT TRUE.**
``do_release`` in ``infra/block-node-bootstrap.sh`` refuses while a container of the claimed run
is up, and ``--force`` is the sentence somebody has to type when they mean to abandon a claim
over a running job. Everything above the node is a caller that does not pass it. That is a good
arrangement -- one place decides -- and it has the failure mode every arrangement like it has:
the guard and the thing depending on the guard are in different files, and removing the guard
turns a safe button into one that hands a machine somebody is training on to a second person.

So this module does not assert that the payload contains the word ``release``. It extracts the
helper the bootstrap actually installs, points it at a temporary state directory and a PATH of
stubs, and runs the payload against it -- with a container up, with one gone, and with no claim
at all. What is being tested is a claim about what another program decides, and the only honest
way to test that is to let the other program decide.

The rest holds the seams a single file cannot see: that the tool has no route to ``--force``,
that a node which did not answer is never reported as one that came free, and that the refusal
in ``block-run.yml`` names a remedy its reader can actually reach.
"""

from __future__ import annotations

import ast
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from workflow_support import WORKFLOWS_ROOT, load_workflow, only_job, step

from edullm_platform.block_fleet import FleetNode
from edullm_platform.block_release import (
    RELEASE_SCRIPT,
    ReleaseReading,
    nodes_wanted,
    not_given_back,
    parse_release_reading,
    release_markdown,
    release_rows,
)
from tools.block_release import chosen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"
RELEASE_PATH = WORKFLOWS_ROOT / "block-release.yml"
RUN_PATH = WORKFLOWS_ROOT / "block-run.yml"
RELEASE_TOOL = PROJECT_ROOT / "tools" / "block_release.py"
LIBRARY = PROJECT_ROOT / "src" / "edullm_platform" / "block_release.py"

RELEASE_STEP = "Hand the nodes back and say what came free"
REFUSAL_STEP = "Refuse a node list or a reservation id that is not one"

#: The helper, out of the ``cat > /usr/local/bin/edullm-node <<'HELPER'`` the bootstrap writes it
#: with. The same expression ``tests/test_block_node_claim.py`` uses and anchored the same way,
#: so a rename of the installed command fails here rather than silently testing nothing.
HELPER_BODY = re.compile(
    r"^cat > /usr/local/bin/edullm-node <<'(?P<delimiter>[A-Z]+)'\n(?P<body>.*?)\n(?P=delimiter)\n",
    re.MULTILINE | re.DOTALL,
)

#: The one absolute path the helper reads that a test cannot write.
SETTINGS_LINE = ". /etc/edullm-block.env"

#: A ``docker`` whose ``ps`` answers out of a marker file, so "is a container up" is a fixture
#: the test sets rather than something the stub decides. ``container_of`` in the helper filters
#: on ``name=^edullm-<run>$`` and reads stdout, so the name is recovered from the filter and the
#: answer is per container rather than per invocation -- a node holding a claim for one run
#: while a container of another name is up is a real state and must answer differently.
DOCKER_STUB = """
if [ "${1:-}" = ps ]; then
  wanted=none
  for argument in "$@"; do
    case "${argument}" in
      name=^edullm-*$)
        wanted="${argument#name=^edullm-}"
        wanted="${wanted%$}"
        ;;
    esac
  done
  if [ -f "${DOCKER_MARKER}-${wanted}" ]; then
    echo c0ffee1234
  fi
  exit 0
fi
exit 0
"""

#: An ``nvidia-smi`` answering the one query the payload makes. ``BUSY_CARDS`` lines out means
#: that many cards in use, and none means an idle machine.
NVIDIA_STUB = """
if [ "${BUSY_CARDS:-0}" -gt 0 ]; then
  index=0
  while [ "${index}" -lt "${BUSY_CARDS}" ]; do
    echo "GPU-0000000${index}"
    index=$((index + 1))
  done
fi
exit 0
"""


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def node(tmp_path: Path) -> dict[str, Path]:
    """One capacity block node carrying the real helper, as far as the payload can tell.

    The stub directory is prepended to PATH rather than replacing it: the payload and the
    helper both reach for ``sed``, ``sort``, ``grep``, ``rm``, ``tr`` and ``date``, and a PATH
    of stubs alone would fail every test here for a reason that has nothing to do with a claim.
    """
    match = HELPER_BODY.search(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    assert match is not None, "the bootstrap no longer installs /usr/local/bin/edullm-node"

    state = tmp_path / "state"
    scratch = tmp_path / "scratch"
    binaries = tmp_path / "bin"
    for directory in (state, scratch, binaries):
        directory.mkdir()

    settings = tmp_path / "edullm-block.env"
    settings.write_text(
        "\n".join(
            [
                "EDULLM_BLOCK_RESERVATION=cr-0000000000000000a",
                "EDULLM_BLOCK_NODE=3",
                "EDULLM_BLOCK_OUTPUTS_BUCKET=edullm-block-outputs-us-east-2",
                "EDULLM_BLOCK_DATA_BUCKET=edullm-data-us-east-2",
                "EDULLM_BLOCK_IMAGE=registry/olmo-core:abc123",
                "EDULLM_BLOCK_IMAGE_REGION=us-east-1",
                "EDULLM_BLOCK_REGION=us-east-2",
                "EDULLM_BLOCK_WANDB_SECRET_ID=a-secret",
                "EDULLM_BLOCK_LOG_SYNC_SECONDS=60",
                "EDULLM_BLOCK_S3_PREFIX=block/cr-0000000000000000a/node-3",
                f"EDULLM_BLOCK_SCRATCH={scratch}",
                f"EDULLM_BLOCK_STATE={state}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    body = match.group("body")
    assert SETTINGS_LINE in body, "the helper no longer reads /etc/edullm-block.env"
    helper = binaries / "edullm-node"
    helper.write_text(body.replace(SETTINGS_LINE, f'. "{settings}"'), encoding="utf-8")
    helper.chmod(0o755)

    _write_stub(binaries, "docker", DOCKER_STUB)
    _write_stub(binaries, "nvidia-smi", NVIDIA_STUB)

    return {"state": state, "binaries": binaries, "marker": tmp_path / "container-is-up"}


def _claim(node: dict[str, Path], *, run: str, who: str, started: str) -> None:
    (node["state"] / "claim.json").write_text(
        f'{{"run":"{run}","who":"{who}","repository":"edu-llm/OLMo-core",'
        f'"branch":"main","commit":"abc123","started_at":"{started}"}}\n',
        encoding="utf-8",
    )


def _send(
    node: dict[str, Path], tmp_path: Path, *, busy_cards: int = 0
) -> subprocess.CompletedProcess[str]:
    """Run the payload the tool sends, the way Systems Manager runs it."""
    payload = tmp_path / "payload.sh"
    payload.write_text(RELEASE_SCRIPT, encoding="utf-8")
    payload.chmod(0o755)
    return subprocess.run(
        ["bash", str(payload)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": f"{node['binaries']}:/usr/bin:/bin:/usr/sbin:/sbin",
            "EDULLM_BLOCK_STATE": str(node["state"]),
            "DOCKER_MARKER": str(node["marker"]),
            "BUSY_CARDS": str(busy_cards),
        },
    )


def _reading(output: str, *, status: str = "Success") -> ReleaseReading:
    return parse_release_reading(node=3, instance_id="i-0abc", status=status, output=output)


# --------------------------------------------------------------------------------------
# THE SAFETY PROPERTY, RUN RATHER THAN READ.
# --------------------------------------------------------------------------------------


def test_a_claim_whose_container_is_still_up_is_refused_by_the_node(
    node: dict[str, Path], tmp_path: Path
) -> None:
    """THE ONE THAT DECIDES WHETHER THIS BUTTON IS SAFE TO GIVE TO FIFTEEN PEOPLE.

    A live 64-rank job holds a claim on every node it runs on, and a release that ended those
    locks would leave eight containers training on machines that read as free -- which is the
    collision the claim exists to prevent, reintroduced by the tool meant to repair it. The
    helper refuses, the payload reports the refusal in the node's own words, and the claim file
    is still there afterwards.
    """
    _claim(node, run="sixtyfour-rank-efa", who="philote-dev", started="2026-08-10T20:32:27Z")
    (node["marker"].parent / f"{node['marker'].name}-sixtyfour-rank-efa").write_text("")

    result = _send(node, tmp_path, busy_cards=8)
    reading = _reading(result.stdout)

    assert result.returncode == 0, result.stderr
    assert reading.verdict == "refused"
    assert reading.cleared is False
    assert reading.given_back is False
    assert "still running here" in reading.said
    assert (node["state"] / "claim.json").is_file(), "the claim was removed over a live container"


def test_a_claim_whose_container_is_gone_comes_back(
    node: dict[str, Path], tmp_path: Path
) -> None:
    """The case the whole workflow exists for, and the fields it has to carry out of it.

    ``held`` and ``who`` are read *before* the release because after a successful one there is
    nothing left on the machine to name the person whose lock was ended, and "whose machine was
    that" is the question somebody clearing a claim actually has.
    """
    _claim(node, run="regmix-probe", who="a-colleague", started="2026-08-10T17:04:00Z")

    result = _send(node, tmp_path)
    reading = _reading(result.stdout)

    assert result.returncode == 0, result.stderr
    assert reading.verdict == "released"
    assert reading.cleared is True
    assert reading.given_back is True
    assert reading.held == "regmix-probe"
    assert reading.who == "a-colleague"
    assert reading.started_at == datetime(2026, 8, 10, 17, 4, tzinfo=UTC)
    assert not (node["state"] / "claim.json").exists()


def test_a_node_nobody_was_holding_says_so_rather_than_reporting_a_release(
    node: dict[str, Path], tmp_path: Path
) -> None:
    """An unclaimed node is as available as one this just cleared, and it is not the same event.

    Reporting it as a release would put a line in the summary saying a lock was ended when none
    was, which is the sort of thing that gets read back later as evidence that somebody took a
    machine off somebody else.
    """
    result = _send(node, tmp_path)
    reading = _reading(result.stdout)

    assert result.returncode == 0, result.stderr
    assert reading.verdict == "unclaimed"
    assert reading.held is None
    assert reading.given_back is True


def test_cards_in_use_are_reported_on_a_node_that_came_free(
    node: dict[str, Path], tmp_path: Path
) -> None:
    """Free of a claim is not free, and the gap between the two is what somebody walks into.

    A container started outside this lane keeps its cards and answers to no claim, so a node
    can be handed back honestly and still be busy. ``block-run.yml`` refuses on that state by
    name, so the reader is better off meeting it here than after a dispatch.
    """
    _claim(node, run="regmix-probe", who="a-colleague", started="2026-08-10T17:04:00Z")

    reading = _reading(_send(node, tmp_path, busy_cards=3).stdout)

    assert reading.given_back is True
    assert reading.gpus_busy == 3
    assert "3 cards in use" in release_markdown(
        [reading], now=datetime.now(tz=UTC), who="someone"
    )


def test_the_payload_carries_a_shebang_so_systems_manager_does_not_hand_it_to_dash() -> None:
    """THE TRAP THIS LANE HAS ALREADY FALLEN INTO ONCE, HELD ON THE LINE THAT AVOIDS IT.

    Systems Manager writes an ``AWS-RunShellScript`` payload to a file and executes it,
    honouring a ``#!`` on line one and falling back to ``/bin/sh`` -- ``dash`` on this AMI
    family -- when there is not one. ``dash`` refuses ``set -o pipefail``, so the whole payload
    would fail identically on every node with ``Illegal option -o pipefail`` and no other
    output. ``tools/block_run_distributed.py`` records the same finding against the same
    mistake, which is why this is a test rather than a comment.
    """
    checked = subprocess.run(
        ["bash", "-n", "-"], input=RELEASE_SCRIPT, capture_output=True, text=True, check=False
    )

    assert RELEASE_SCRIPT.splitlines()[0] == "#!/bin/bash"
    assert "set -euo pipefail" in RELEASE_SCRIPT
    assert checked.returncode == 0, checked.stderr


def test_nothing_in_this_lane_has_a_route_to_force() -> None:
    """Mutation: add ``--force`` behind an input, the way ``block-run.yml`` has one.

    That input exists there because taking a node off somebody is sometimes the right call once
    they have been asked, and the run it starts fights the other one for memory rather than
    ending it. Here the equivalent flag does something categorically worse: it abandons a claim
    while the container keeps its cards, so the machine reads as free to the whole fleet while
    somebody is training on it, and the next person's run collides with work nothing can see.

    The refusal is the feature. Anybody who genuinely needs to break a lock over live work
    holds a role and can type the sentence on the machine, where they will have read what it
    says first.

    Read out of the syntax rather than off the file, for the reason the drain test gives
    against the same shape: three of the paragraphs here name the flag they exist to forbid, so
    a check against the raw text would be satisfied by the explanation instead of by the code.
    Every string these two modules build -- the payload included, which is a plain constant --
    is examined; only the prose is not.
    """
    for path in (RELEASE_TOOL, LIBRARY):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prose = {
            id(first.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
            for first in [next(iter(node.body), None)]
            if isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        }
        reachable = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
        ]

        assert not [text for text in reachable if "--force" in text], (
            f"{path.name} builds a string carrying --force, which is the flag that abandons a "
            "claim while its container keeps the cards"
        )

    # The workflow is YAML, where a comment is a line rather than a node. Both an input that
    # offered the flag and a run body that passed it would survive this stripping and fail.
    workflow = "\n".join(
        line
        for line in RELEASE_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "--force" not in workflow, "the form or its run body can reach --force"


def test_the_node_helper_still_refuses_a_release_over_a_running_container() -> None:
    """The guard this whole button leans on, held in the file that owns it.

    Every other test here runs the helper, so a removed guard already fails several of them.
    This one exists so that the failure *names the thing that changed*: without it, deleting two
    lines from ``do_release`` produces four confusing failures in a module about a workflow, and
    the person reading them has to work out that the fault is in a bootstrap they did not touch.
    """
    match = HELPER_BODY.search(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    assert match is not None
    body = match.group("body")
    release = body[body.index("do_release()") : body.index("do_run()")]

    assert 'if [ -n "$(container_of "${run}")" ] && [ "${force}" != "--force" ]; then' in release
    assert "die " in release
    assert release.index("container_of") < release.index('rm -f "${CLAIM}"')


# --------------------------------------------------------------------------------------
# WHAT THE READING IS ALLOWED TO CONCLUDE.
# --------------------------------------------------------------------------------------


def test_a_node_that_did_not_answer_is_never_reported_as_one_that_came_free() -> None:
    """Mutation: read the output before the invocation status, which parses perfectly.

    An invocation that never ran produces an empty output, and an empty output looks exactly
    like a node that was not claimed -- so a reader built that way tells somebody a machine is
    theirs when nothing was ever asked of it. ``parse_reading`` and ``parse_drain_reading`` both
    read the status first for this reason and this is the third of them.
    """
    reading = _reading("", status="Failed")

    assert reading.reachable is False
    assert reading.verdict == "unknown"
    assert reading.given_back is False
    assert not_given_back([reading]) == ("i-0abc",)
    assert "UNREACHABLE" in "\n".join(release_rows([reading], now=datetime.now(tz=UTC)))


def test_an_answer_that_stopped_before_the_verdict_is_not_read_as_unclaimed() -> None:
    """The other direction of the same mistake, and the one a successful invocation can produce.

    Systems Manager truncates invocation output, and a payload cut off after the claim lines
    has said nothing about whether the release happened. ``unclaimed`` is the one verdict that
    must never be inferred from silence, because it is the verdict that means "take this
    machine".
    """
    reading = _reading("held\tregmix-probe\nwho\ta-colleague\ngpus_busy\t0\n")

    assert reading.verdict == "unknown"
    assert reading.given_back is False
    assert "UNCLEAR" in "\n".join(release_rows([reading], now=datetime.now(tz=UTC)))


def test_a_release_reported_over_a_claim_file_that_is_still_there_is_neither() -> None:
    """``edullm-node release`` says what it did; the claim file says what is true.

    They come apart when something rewrites the claim between the release and the read, which
    is the distributed launcher taking a node in the same second. Reporting the exit status
    would hand that machine to somebody, and reporting a refusal would be wrong about what
    happened, so it is reported as the thing to go and look at.
    """
    reading = _reading("held\tr\nwho\tw\ngpus_busy\t0\nverdict\treleased\ncleared\tno\nsaid\tok\n")

    assert reading.given_back is False
    assert "UNCLEAR" in "\n".join(release_rows([reading], now=datetime.now(tz=UTC)))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("all", None),
        ("ALL", None),
        ("3", (3,)),
        ("3,5,7", (3, 5, 7)),
        (" 7 , 3 , 7 ", (3, 7)),
    ],
)
def test_which_nodes_a_dispatch_named(value: str, expected: tuple[int, ...] | None) -> None:
    assert nodes_wanted(value) == expected


@pytest.mark.parametrize("value", ["", "   ", ",", "five", "3,five", "0", "-1", "3.5"])
def test_a_node_list_that_is_not_one_is_refused_rather_than_partly_read(value: str) -> None:
    """THE EMPTY STRING IS IN THIS LIST ON PURPOSE AND IT IS THE IMPORTANT ROW.

    ``all`` is a word somebody has to type. A form left blank must not mean every machine on a
    button whose job is to end other people's locks, and the difference between "sweep the
    fleet" and "I did not finish filling this in" is exactly one that a permissive parser
    erases.

    ``3,five`` is the other shape worth naming: read permissively it becomes ``(3,)``, which
    reports on one node and says nothing at all about the other, to somebody who asked about
    two.
    """
    with pytest.raises(ValueError):
        nodes_wanted(value)


def test_a_node_number_that_is_not_in_the_fleet_is_said_rather_than_dropped() -> None:
    """Somebody typing ``9`` at an eight-node fleet, or a node number from the previous block.

    Filtering it out silently produces a report about the machines that do exist with no
    mention of the one being asked about, which reads as that machine being fine.
    """
    fleet = [
        FleetNode(node=1, instance_id="i-0001", state="running", private_ip=None,
                  capacity_reservation_id="cr-1"),
        FleetNode(node=2, instance_id="i-0002", state="running", private_ip=None,
                  capacity_reservation_id="cr-1"),
    ]

    asking, missing = chosen(fleet, wanted=(2, 9))

    assert [found.instance_id for found in asking] == ["i-0002"]
    assert missing == (9,)


def test_the_summary_names_who_ended_the_lock() -> None:
    """A claim is a lock on a shared machine in a window nobody can extend.

    Ending somebody else's should leave a name where the next person to read the node finds it.
    The durable half is the Systems Manager comment, which carries the same name into
    CloudTrail; this is the half a researcher can read.
    """
    released = _reading(
        "held\tregmix-probe\nwho\ta-colleague\ngpus_busy\t0\n"
        "verdict\treleased\ncleared\tyes\nsaid\tnode 3 released\n"
    )
    refused = parse_release_reading(
        node=4,
        instance_id="i-0def",
        status="Success",
        output=(
            "held\tsixtyfour-rank-efa\nwho\tphilote-dev\ngpus_busy\t8\n"
            "verdict\trefused\ncleared\tno\n"
            "said\tedullm-node: sixtyfour-rank-efa is still running here; stop it or pass --force\n"
        ),
    )

    page = release_markdown([released, refused], now=datetime.now(tz=UTC), who="a-researcher")

    assert "released by `a-researcher`" in page
    assert "| claims released | 1 |" in page
    assert "| refused, still running | 1 |" in page
    assert "was held by `a-colleague` for `regmix-probe`" in page
    assert "still running here" in page
    assert "safety property working" in page


def test_a_node_number_that_is_not_in_the_fleet_reaches_the_page_and_not_only_the_log() -> None:
    """THE SUMMARY IS THE WHOLE OF WHAT THE READER THIS BUTTON EXISTS FOR CAN SEE.

    :func:`chosen` separates the numbers that are not in the fleet, the tool prints them to
    stderr, and until this test they went no further. A dispatch naming node 9 against an
    eight-node fleet therefore rendered as a page saying there was nothing to ask -- which reads
    as *the block is gone* to somebody who typed one character wrong, and is the same misreading
    the workflow refuses a malformed reservation id one step earlier to avoid.
    """
    page = release_markdown([], now=datetime.now(tz=UTC), who="a-researcher", missing=[9])

    assert "- node 9" in page
    assert "no running instance in this fleet" in page
    assert "nothing to ask" not in page, (
        "an empty fleet and a node number that is not in it are different answers, and this one "
        "tells a researcher the block is gone"
    )


def test_an_empty_fleet_still_says_so_when_nobody_named_a_node_that_is_missing() -> None:
    """Mutation: drop the empty-fleet sentence now that the branch above it is conditional.

    A sweep that found no tagged instance at all is a real reading and a different one -- the
    fleet has been reclaimed, or the reservation filter matched nothing -- and a page that went
    silent on it would answer that with a heading and no words under it.
    """
    page = release_markdown([], now=datetime.now(tz=UTC), who="a-researcher")

    assert "No running instance carries a node tag here" in page


def test_the_tool_hands_the_missing_nodes_to_the_page_rather_than_only_to_stderr() -> None:
    """The seam between the two tests above and the file that has to join them.

    ``release_markdown`` growing the parameter proves nothing on its own: the whole defect was a
    caller that had the list and wrote it somewhere a browser does not go. Read out of the syntax
    because the tool prints the same numbers to stderr as well, on purpose, for the maintainer
    running it from a laptop -- so the name appears in this file either way and only the call
    argument settles it.
    """
    tree = ast.parse(RELEASE_TOOL.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "release_markdown"
    ]

    assert len(calls) == 1, "the tool writes the job summary in one place and this found another"
    assert "missing" in {keyword.arg for keyword in calls[0].keywords}, (
        "the job summary is built without the node numbers that are not in the fleet, so a "
        "mistyped number reaches the log and never the page the dispatcher reads"
    )


# --------------------------------------------------------------------------------------
# THE WORKFLOW.
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    return load_workflow(RELEASE_PATH)


def test_giving_a_node_back_is_deliberately_not_limited_to_admins(
    release: dict[str, Any],
) -> None:
    """The property somebody tidying this up would break first, and it is the same one
    ``block-run.yml``, ``block-status.yml``, ``block-logs.yml`` and ``block-drain.yml`` all
    carry. This file exists precisely because roughly fifteen of the thirty-five people here
    hold no AWS role; an admin guard on it hands the door back to the twenty who never needed
    one, and leaves the fifteen exactly where the refusal found them."""
    job = only_job(release)
    names = [item.get("name", "") for item in job["steps"]]

    assert not any("may not make one" in name for name in names)
    assert "if" not in job


def test_the_node_list_has_no_default(release: dict[str, Any]) -> None:
    """Mutation: default it to ``all``, which every other input in this lane has an analogue of.

    The other defaults name a region or a bucket. This one would mean that a dispatch nobody
    finished filling in ends every claim on the fleet, and the input this file is most likely
    to be dispatched next to is ``block-drain.yml``'s, which does default.
    """
    field = release["on"]["workflow_dispatch"]["inputs"]["nodes"]

    assert field["required"] is True
    assert "default" not in field
    assert "is refused" in field["description"], "the form does not say it cannot end a live run"


def test_the_node_list_is_refused_before_any_credential_is_assumed(
    release: dict[str, Any],
) -> None:
    """A typo answerable on a runner with no AWS access should cost no role assumption, which is
    the ordering ``block-status.yml`` and ``block-run.yml`` both keep. The list is parsed by the
    library the tool parses it with, so the form cannot accept something the tool then rejects
    after the credentials step."""
    job = only_job(release)
    names = [item.get("name", "") for item in job["steps"]]
    body = step(job, REFUSAL_STEP)["run"]

    assert names.index(REFUSAL_STEP) < names.index("Configure AWS credentials")
    assert "from edullm_platform.block_release import nodes_wanted" in body
    assert "reservation_is_not_a_capacity_reservation_id" in body


def test_the_workflow_runs_the_tool_a_maintainer_can_run_from_a_laptop(
    release: dict[str, Any],
) -> None:
    """The same argument the drain, the log reader and the status reading each make. A
    maintainer runs exactly this with ``--profile sbsandbox`` in place of ``--no-profile``,
    which is the fallback for the morning GitHub is the thing that is broken."""
    body = step(only_job(release), RELEASE_STEP)["run"]

    assert "tools/block_release.py" in body
    assert "--no-profile" in body
    assert '--summary "${GITHUB_STEP_SUMMARY}"' in body
    assert RELEASE_TOOL.is_file()


def test_the_dispatching_actor_is_carried_into_the_record(release: dict[str, Any]) -> None:
    """Mutation: drop ``--who`` and let the workflow run's own metadata be the record.

    It is a record, and it is one nobody reading the node will ever see. The Systems Manager
    comment is a CloudTrail entry that outlives this repository's retention and lives outside
    the system that produced it, which is the property worth having for an action that ends
    somebody else's lock on a shared machine.
    """
    job = only_job(release)
    body = step(job, RELEASE_STEP)["run"]
    tool = RELEASE_TOOL.read_text(encoding="utf-8")

    assert job["env"]["RELEASED_BY"] == "${{ github.actor }}"
    assert '--who "${RELEASED_BY}"' in body
    assert 'f"edullm block release by {who}"' in tool


def test_the_stale_claim_refusal_names_a_remedy_its_reader_can_reach() -> None:
    """THE SENTENCE THAT IS THE ONLY THING MOST PEOPLE WILL READ, HELD TO WHAT IT SENDS THEM TO.

    The refusal used to say to clear the claim with ``edullm-node release`` on the machine. That
    is correct and roughly fifteen of the thirty-five people here cannot do it, because they
    hold no AWS role and cannot open a node by any route -- so what the sentence left them was
    ``take_the_node_anyway``, whose own description promises a fight for memory with a run that
    does not exist. A refusal that names a cure its reader cannot obtain teaches them to reach
    for the dangerous lever instead.
    """
    body = RUN_PATH.read_text(encoding="utf-8")
    script = step(only_job(load_workflow(RUN_PATH)), "Find the node and read what it is doing")[
        "run"
    ]

    assert "node_claim_is_stale:" in script
    assert "block-release.yml" in script
    assert "needs no AWS credential" in script
    assert RELEASE_PATH.is_file()
    assert body.count("edullm-node release") <= 1, (
        "a second place still sends somebody to the machine as the first thing to try"
    )
