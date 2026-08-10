"""Giving a capacity block node back, for somebody who cannot open one.

**THE REFUSAL THIS EXISTS TO ANSWER NAMED A CURE HALF THE TEAM CANNOT OBTAIN.** Nothing on a
node removes a claim when a container exits, so a run that finished hours ago leaves the
machine reading as held. ``block_fleet.NodeReading.claim_is_stale`` learned to say so, which was
a large improvement over ``node_is_busy`` naming a colleague who had gone home -- and the remedy
it printed was ``edullm-node release`` *on the machine*. Roughly fifteen of the thirty-five
people here hold no AWS role, cannot assume one, and cannot open a Systems Manager session, so
for them that sentence names the disease and prescribes a medicine they cannot get. With eight
nodes carrying claims from an afternoon of agents, the first thing most of them meet is a
machine that says it is busy while sitting idle, and no way forward at all.

``.github/workflows/block-release.yml`` holds the credential and this is what it runs.

**THE SAFETY PROPERTY IS ON THE NODE AND NOT HERE, WHICH IS WHY THIS IS SAFE BY CONSTRUCTION.**
``do_release`` in ``infra/block-node-bootstrap.sh`` refuses while a container of the claimed run
is up, and makes ``--force`` the sentence somebody has to type when they mean to abandon a claim
over live work. Nothing in this module passes it and nothing ever should: a release that could
kill a running job would be a button that hands a busy machine to a second person, which is the
collision the claim exists to prevent, reintroduced by the tool meant to repair it.

That leaves one narrow window, named here because it is the only way this can lie. The
distributed launcher takes its claim through ``edullm-node claim`` and starts its container tens
of seconds later, after a clone -- so between those two moments a claim is real and no container
answers to it. A release landing in that window ends a lock over a job that is about to start.
It is not defended against, because defending against it means guessing at intent from a
timestamp; it is reported instead. :func:`release_rows` prints how long a claim has been held,
and a claim seconds old is one to leave alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from edullm_platform.block_fleet import elapsed_as

__all__ = [
    "RELEASE_SCRIPT",
    "ReleaseReading",
    "nodes_wanted",
    "not_given_back",
    "parse_release_reading",
    "release_markdown",
    "release_rows",
]

#: What one node is asked, and it is read, released and read again in that order.
#:
#: **THE FIRST LINE IS A SHEBANG AND WITHOUT IT NONE OF THE REST RUNS.** Systems Manager writes
#: an ``AWS-RunShellScript`` payload to a file and executes it, honouring a ``#!`` on line one
#: and falling back to ``/bin/sh`` -- which is ``dash`` on this AMI family -- when there is not
#: one. ``dash`` refuses ``set -o pipefail`` on the third line, so the whole payload would fail
#: identically on every node with ``Illegal option -o pipefail`` and no other output.
#: ``tools/block_run_distributed.py`` records the same finding against the same mistake.
#:
#: **THE CLAIM IS READ BEFORE AND AFTER, AND THE SECOND READ IS THE VERDICT.** ``edullm-node
#: release`` exits zero and says ``released``, which is a claim about what it did rather than
#: about what is now true, and the two come apart on a machine where something rewrote the file
#: in between. The claim file being gone is the fact a person is actually asking about, so it is
#: the fact that is measured. The read *before* is what lets the report say whose lock was
#: ended: after a successful release there is nothing left on the machine to name them by.
#:
#: **IT IS COMPOSED HERE AND NOT INSTALLED ON THE NODE, AND THAT IS THE WHOLE REASON IT WORKS
#: TODAY.** A helper added to ``infra/block-node-bootstrap.sh`` reaches a machine at launch and
#: never afterwards, so it would be worth nothing to a fleet that is already up -- which is
#: every fleet this was written for. The bootstrap is also user-data against a 16,384-byte
#: gzipped ceiling with a few hundred bytes left, measured by ``tests/test_block_workflows.py``,
#: and exceeding it does not turn a test red. It makes the next fleet unlaunchable.
#:
#: ``nvidia-smi`` is asked even though nothing here branches on the answer. A node handed back
#: with cards still in use is somebody's work that this lane did not start, and it is the single
#: most useful thing the report can carry -- ``block-run.yml`` refuses on exactly that state by
#: name, so a reader who sees it here knows what they are about to meet.
#:
#: **THE STATE DIRECTORY IS THE SETTING THE REST OF THE LANE ALREADY USES, AND IT DEFAULTS TO
#: THE PATH THAT IS ALWAYS RIGHT ON A NODE.** Systems Manager runs this with a bare environment,
#: so on a real machine the default is what applies -- the same literal
#: ``block_fleet.REMOTE_READING_SCRIPT`` reads, for its reason: it must not depend on anything
#: the bootstrap installs. Honouring ``EDULLM_BLOCK_STATE`` when it is set is what lets
#: ``tests/test_block_release.py`` run this payload against the *real* ``edullm-node`` extracted
#: from the bootstrap, rather than making assertions about the text of a script that has never
#: been executed. This payload's whole claim is about what another program decides, so testing
#: it any other way would be testing the claim against itself.
RELEASE_SCRIPT: Final = r"""#!/bin/bash
set -euo pipefail

claim="${EDULLM_BLOCK_STATE:-/var/lib/edullm}/claim.json"
held=""
if [ -f "${claim}" ]; then
  held=$(sed -n 's/.*"run":"\([^"]*\)".*/\1/p' "${claim}")
  printf 'held\t%s\n' "${held}"
  sed -n 's/.*"who":"\([^"]*\)".*/who\t\1/p' "${claim}"
  sed -n 's/.*"started_at":"\([^"]*\)".*/started_at\t\1/p' "${claim}"
fi
busy=$(nvidia-smi --query-compute-apps=gpu_uuid --format=csv,noheader 2>/dev/null |
  sort -u | grep -c . || true)
printf 'gpus_busy\t%s\n' "${busy:-0}"

if [ -z "${held}" ]; then
  printf 'verdict\tunclaimed\n'
  exit 0
fi

# In an `if` condition so that `set -e` does not end the payload on the refusal this command
# exists to make. Both streams are captured because the refusal is written to stderr, and it is
# the sentence a person most needs: it names the container that is still up.
if said=$(edullm-node release 2>&1); then
  printf 'verdict\treleased\n'
else
  printf 'verdict\trefused\n'
fi
if [ -f "${claim}" ]; then
  printf 'cleared\tno\n'
else
  printf 'cleared\tyes\n'
fi
printf 'said\t%s\n' "$(printf '%s' "${said}" | tr '\n\t' '  ')"
"""


def nodes_wanted(value: str) -> tuple[int, ...] | None:
    """Which nodes a dispatch named, or ``None`` for every node in the fleet.

    ``all`` is a word rather than an empty string, and that is the difference between a sweep
    somebody asked for and a form somebody did not finish filling in. An empty ``node`` field on
    a shared button that clears other people's locks should not mean "clear all of them".

    Raises rather than dropping the parts it cannot read. A list of ``3, five, 7`` that quietly
    became ``(3, 7)`` would report on two nodes and say nothing about the third, and the person
    reading it asked about three machines.
    """
    text = value.strip()
    if text.lower() == "all":
        return None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        raise ValueError(
            "nodes_names_nothing:name a node, several separated by commas, or all"
        )
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit() or int(part) < 1:
            raise ValueError(
                f"node_is_not_a_number:{part!r} is not a node. They are numbered from 1, and "
                "the fleet this was written for has eight"
            )
        numbers.append(int(part))
    return tuple(sorted(set(numbers)))


@dataclass(frozen=True)
class ReleaseReading:
    """What one node said when it was asked to give its claim back.

    ``held`` and ``who`` are what the claim said *before* the release, which is the only moment
    they can be read: a release that works leaves nothing on the machine to name the person
    whose lock was just ended. They are what makes the report answer the question somebody
    actually has, which is not "did a command succeed" but "whose machine was that, and is it
    mine now".

    ``cleared`` is read off the disk afterwards rather than inferred from ``verdict``, so the
    two can disagree and be reported disagreeing. That is the state where something rewrote the
    claim between the release and the read, and it is a machine to look at rather than to take.
    """

    node: int | None
    instance_id: str
    reachable: bool
    detail: str
    held: str | None
    who: str | None
    started_at: datetime | None
    gpus_busy: int
    verdict: str
    cleared: bool
    said: str

    @property
    def given_back(self) -> bool:
        """Whether this machine is now free of a claim, whatever the route there was.

        A node that was never claimed is as available as one this just cleared, and both are
        the answer to the question the dispatch asked. A refusal is not, and neither is a node
        that answered ``released`` over a claim file that is still there.
        """
        if not self.reachable:
            return False
        if self.verdict == "unclaimed":
            return True
        return self.verdict == "released" and self.cleared


def _fields(output: str) -> list[list[str]]:
    return [line.split("\t") for line in output.splitlines() if line.strip()]


def parse_release_reading(
    *, node: int | None, instance_id: str, status: str, output: str
) -> ReleaseReading:
    """One node's answer, or the reason there is not one.

    Tab-separated records rather than JSON, which is the convention
    ``block_fleet.REMOTE_READING_SCRIPT`` set and is here for its reason: emitting JSON from
    shell means quoting by hand or depending on ``jq``, and ``jq`` is not on every image this
    AMI family has shipped.

    The invocation status is read before the output, for the reason ``parse_reading`` reads it
    first. An invocation that never ran produces an empty output, and an empty output parses
    perfectly into a node that was not claimed -- which is this tool reporting a machine as
    yours when nothing was ever asked of it.
    """
    if status != "Success":
        return ReleaseReading(
            node=node,
            instance_id=instance_id,
            reachable=False,
            detail=status or "no answer",
            held=None,
            who=None,
            started_at=None,
            gpus_busy=0,
            verdict="unknown",
            cleared=False,
            said="",
        )

    found: dict[str, str] = {}
    for record in _fields(output):
        key, values = record[0].strip(), [part.strip() for part in record[1:]]
        if values:
            found[key] = values[0]

    started: datetime | None = None
    if found.get("started_at"):
        try:
            started = datetime.fromisoformat(found["started_at"])
        except ValueError:
            # Tolerated for the reason ``block_drain._timestamp`` tolerates the same thing:
            # these records are written by ``printf`` on a machine rather than by a serializer,
            # and losing a whole reading over one unparsable field would report a node that
            # answered as a node that did not.
            started = None

    return ReleaseReading(
        node=node,
        instance_id=instance_id,
        reachable=True,
        detail="",
        held=found.get("held") or None,
        who=found.get("who") or None,
        started_at=started,
        gpus_busy=int(found.get("gpus_busy") or 0),
        # A payload that was cut off before it printed a verdict has told us nothing about the
        # claim, and ``unclaimed`` is the one answer that must never be inferred from silence.
        verdict=found.get("verdict") or "unknown",
        cleared=found.get("cleared") == "yes",
        said=found.get("said", ""),
    )


def _since(reading: ReleaseReading, *, now: datetime) -> str:
    if reading.started_at is None:
        return ""
    return f" held {elapsed_as(reading.started_at, now=now)}"


def release_rows(readings: Sequence[ReleaseReading], *, now: datetime) -> tuple[str, ...]:
    """One line per node, saying whether the machine is now the reader's to take.

    The verdict is the second column and is scannable down the page, for the reason
    ``block_fleet.status_rows`` puts ``IDLE`` there: somebody clearing a claim is asking one
    question about each machine and wants to answer it without reading across.

    A refusal prints what the node said rather than a phrasing of our own. That sentence names
    the container that is still running, and it is written by the helper that made the decision
    -- restating it here would be a second copy to keep in agreement with the first.
    """
    lines: list[str] = []
    for reading in readings:
        label = f"node {reading.node if reading.node is not None else '?'}"
        head = f"{label:<8}{reading.instance_id:<21}"
        if not reading.reachable:
            lines.append(f"{head}UNREACHABLE  {reading.detail}")
            continue
        if reading.verdict == "unclaimed":
            lines.append(f"{head}WAS NOT HELD")
            continue
        who = f"{reading.who or '-'} / {reading.held or '-'}"
        if reading.verdict == "released" and reading.cleared:
            lines.append(f"{head}RELEASED     {who}{_since(reading, now=now)}")
            continue
        if reading.verdict == "refused":
            lines.append(f"{head}REFUSED      {who}: {reading.said or 'no reason given'}")
            continue
        # Released against a claim file that is still there, or a payload that never reached a
        # verdict. Both are a machine to look at rather than one to take, and neither has a
        # phrasing that would be honest about which.
        lines.append(
            f"{head}UNCLEAR      {who}: answered {reading.verdict!r}, "
            f"claim file is {'gone' if reading.cleared else 'still there'}"
        )
        if reading.said:
            lines.append(f"{'':<29}{reading.said}")
    return tuple(lines)


def not_given_back(readings: Iterable[ReleaseReading]) -> tuple[str, ...]:
    """Every instance that still carries a claim, for an exit code to read."""
    return tuple(reading.instance_id for reading in readings if not reading.given_back)


def release_markdown(
    readings: Sequence[ReleaseReading],
    *,
    now: datetime,
    who: str,
    missing: Sequence[int] = (),
) -> str:
    """The same reading as a job summary, for the people who have no other way to see it.

    ``who`` is printed rather than left to the run's own metadata, and that is the point of the
    line rather than decoration. A claim is a lock on a shared machine in a window nobody can
    extend, so ending somebody else's should leave a trace where the next person to read the
    node will find it. The durable half of that trace is the Systems Manager comment, which
    carries the same name into CloudTrail; this half is the one a researcher can read.

    **THE NODE NUMBERS THAT ARE NOT IN THE FLEET ARE A PARAMETER HERE RATHER THAN SOMETHING THE
    CALLER PRINTS ELSEWHERE, AND LEAVING THEM OUT PRODUCED THE ONE LIE THIS PAGE COULD TELL.**
    :func:`~tools.block_release.chosen` separates them out precisely so a dispatch naming node 9
    against an eight-node fleet does not read as a report about the machines that do exist. Sent
    only to stderr, they reached the job log and not this page -- and this page is the whole of
    what a researcher with no AWS role ever sees, so the render was the summary below saying
    "there was nothing to ask": *the block is gone*, to somebody who typed one character wrong.
    ``.github/workflows/block-release.yml`` refuses a malformed reservation id one step earlier
    to avoid exactly that reading, which is how far the intent got before the page undid it.
    """
    ended = [reading for reading in readings if reading.verdict == "released" and reading.cleared]
    kept = [reading for reading in readings if reading.reachable and reading.verdict == "refused"]

    lines = [
        f"### Capacity block claims — released by `{who}`",
        "",
        f"| nodes asked | {len(readings)} |",
        "| --- | --- |",
        f"| claims released | {len(ended)} |",
        f"| refused, still running | {len(kept)} |",
        "",
    ]

    if missing:
        lines += [
            (
                "**These node numbers carry no running instance in this fleet, so nothing was "
                "asked of them and no claim of theirs was touched.** Check the number against "
                "`Block: which node is free`, which counts the machines that are actually up. "
                "If two blocks have a fleet going at once, node numbers repeat across them and "
                "the reservation id is what tells them apart."
            ),
            "",
            *(f"- node {number}" for number in missing),
            "",
        ]

    if not readings:
        # Silent when something *was* asked for and was simply not here. The sentence below is
        # about an empty fleet, and printing it under the list above would answer a mistyped
        # node number with "the block is gone".
        if not missing:
            lines.append(
                "No running instance carries a node tag here, so there was nothing to ask."
            )
        return "\n".join(lines)

    lines += ["```", *release_rows(readings, now=now), "```", ""]

    if ended:
        lines += [
            (
                "**These machines are free now.** Nothing was running on any of them: "
                "`edullm-node release` refuses while a claimed container is up, so a claim that "
                "came back was a leftover rather than somebody's work."
            ),
            "",
        ]
        for reading in ended:
            lines.append(
                f"- node {reading.node} (`{reading.instance_id}`) was held by "
                f"`{reading.who or 'unknown'}` for `{reading.held or 'unknown'}`"
            )
        lines.append("")

    if kept:
        lines += [
            (
                "**These were refused and that is the safety property working.** The claimed "
                "container is still up, so the claim is a live lock rather than a leftover. "
                "Take another node, or talk to the person named before anybody forces anything."
            ),
            "",
        ]
        for reading in kept:
            lines.append(
                f"- node {reading.node} (`{reading.instance_id}`): {reading.said or 'refused'}"
            )
        lines.append("")

    busy = [reading for reading in readings if reading.given_back and reading.gpus_busy]
    if busy:
        lines += [
            (
                "**Free of a claim and not free.** Cards are in use on these with nothing "
                "claiming them, which is work this lane did not start. `block-run.yml` refuses "
                "on that state by name -- find whoever started it before taking the machine."
            ),
            "",
        ]
        for reading in busy:
            lines.append(
                f"- node {reading.node} (`{reading.instance_id}`): "
                f"{reading.gpus_busy} cards in use"
            )
        lines.append("")

    return "\n".join(lines)
