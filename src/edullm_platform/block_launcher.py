"""Whether a command dispatched at one capacity block node can start at all.

**TWO RUNS DIED THIS WAY ON 2026-08-10 AND NEITHER FAILURE NAMED THE CAUSE.**
``.github/workflows/block-run.yml`` and ``edullm-node run`` execute the command they are given
verbatim and prepend nothing. The command committed at ``.edullm/run.yaml`` on
``edu-llm/OLMo-core@edullm/final-model`` is a 64-rank command -- ``olmoe_7b_32x4`` is 7.12
billion parameters over 32 routed experts -- so dispatching the single-node button with it
starts one process on one of the node's eight cards and leaves seven idle at the block's rate.
What comes back some seconds later is an error about the parallelism mesh, which sends the
reader to the mesh flags. The mesh is fine. There is no launcher.

**THE ENTRYPOINT'S OWN GUARD IS SWITCHED OFF IN EXACTLY THIS CASE, WHICH IS WHY THE FAILURE
ARRIVES WEARING SOMEBODY ELSE'S NAME.** ``validate_olmoe_parallelism`` on that branch reads
``--moe-shard-degree`` (32 by default) against ``--moe-num-replicas`` (2), multiplies them out
to a world size of 64, and compares that against ``os.environ["WORLD_SIZE"]`` -- returning
early, with no complaint, when the variable is unset. Nothing but a launcher sets it. So the
one check written to say "this recipe wants 64 ranks" is precisely the check that does not run
when there are none, and the run continues until a device mesh is built out of a world of one.

**THIS REFUSES AND DELIBERATELY DOES NOT PREPEND A LAUNCHER.** Prepending is the tempting fix
and it is the failure ``block_multinode`` spends its header on: a command that already carries
one gets wrapped in a second, which is eight agents per node each starting eight workers that
each start eight more. A shared control cannot both rewrite somebody's command and be trusted
with the next one, and the person who typed the command is the only one who knows whether the
single process was the point.

**IT ASKS THE OPPOSITE QUESTION TO ``launchers.require_a_process_for_every_device`` AND MUST
NOT BE CONFUSED WITH IT.** That rule reads "the devices billed are the devices used" and is
right for a submission, where every command is a training run against a profile somebody chose.
Asked here it would refuse ``nvidia-smi``, ``python -c 'import torch; print(...)'`` and every
one-line shell probe anybody runs through this button, all of which are ordinary work on a
machine that happens to have eight cards. A false refusal on the only door fifteen of
thirty-five people have, during a window that cannot be extended, is its own outage. So the
question here is narrower and its burden of proof is on the refusal: not "does this command use
every card" but "does this command *say* it needs more than one rank while starting one
process".

**WHICH MEANS THE EVIDENCE HAS TO BE A WORD IN THE COMMAND, AND THE BAR IS
:mod:`edullm_platform.precision`'S.** That module refuses only where it can quote the exact
token asking for the thing the hardware cannot do, and :mod:`edullm_platform.accelerators`
records at length why a guard that infers a model's size from a command line is not worth
having. :func:`multi_rank_evidence` returns the words it found and the refusal quotes them, so
a person reading it can see what this decided on. A command carrying none of them is not
refused -- this is a backstop and not a proof, and the honest statement of that is here rather
than in a reader's assumption.

**THERE IS A SECOND, NARROWER COPY OF THIS ON THE NODE, AND IT IS NARROWER FOR A REASON THAT
HAS NOTHING TO DO WITH THE RULE.** ``edullm-node run`` in ``infra/block-node-bootstrap.sh``
refuses the same factory, because the workflow is not the only door -- somebody with a role
types that verb on a shell. What it cannot do is import this: there is no platform library on
the machine, and the file it lives in is user-data, which EC2 refuses above 16,384 bytes
compressed and which was already at 14,903 of that. So the node checks the factory as a
substring and stops there, and this module is where the reading that misses nothing lives.
Neither copy reaches a node that is already running: nothing re-runs user-data.

**THE LAUNCHER IS READ BY :mod:`edullm_platform.launchers` RATHER THAN BY THE SUBSTRING TEST IN
``block_multinode``.** ``_LAUNCHERS`` there is ``"torchrun" in command``, which is correct for
what it does -- it is looking for a launcher to refuse, so a mention in a comment costing a
false refusal on the distributed path is a cheap error in a safe direction. Here the same test
runs the other way round: a stray ``torchrun`` in a note would read as a launcher that is not
there and let the broken command through. ``read_launch_plan`` opens ``bash -lc`` wrappers,
recognises a launcher in command position only, skips comments and knows the six programs that
start ranks, which is the reading this needs.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Final

from .block_multinode import MESH_FLAGS
from .launchers import LAUNCH_CHECK_WAIVER, carries_the_token, corrected_command, read_launch_plan
from .launchers import simple_commands as _simple_commands

__all__ = [
    "MULTI_RANK_FACTORIES",
    "NO_LAUNCHER_CODE",
    "RANKS_READ_ON_THE_MACHINE",
    "launcher_refusals",
    "multi_rank_evidence",
]

#: The machine-readable half of the refusal. Matched on by anything reading this lane's output;
#: the prose after the colon is written for a person and will be reworded.
NO_LAUNCHER_CODE: Final = "command_needs_a_launcher"

#: What goes in ``--nproc-per-node`` when the caller cannot see the machine.
#:
#: torchrun's own spelling for "one process per visible device", resolved inside the container
#: at start-up. The credential-free step of ``block-run.yml`` runs on a GitHub runner before any
#: node has been addressed, so it does not know whether this fleet is eight H100s or something
#: else, and ``config/capacity.yaml`` is not the answer either -- the block is bought outside
#: the platform's provisioning. A number written here would be a number quoted from memory,
#: which is the one thing this repository asks nobody to do; ``gpu`` defers the count to the
#: only party that can take it. ``edullm-node run`` has ``nvidia-smi`` in front of it and names
#: the real figure instead.
RANKS_READ_ON_THE_MACHINE: Final = "gpu"

#: Model factories that cannot run as one process, and the fact that makes each one true.
#:
#: A FACTORY IS ON THIS LIST BECAUSE SOMEBODY ESTABLISHED SOMETHING ABOUT IT, AND THE LIST IS
#: SHORT FOR THAT REASON RATHER THAN THROUGH NEGLECT. Nothing on this platform records a model's
#: size -- ``schemas/submission-inputs.schema.json`` carries fifteen properties and no parameter
#: count, and :mod:`edullm_platform.accelerators` sets out why inferring one from a command line
#: is a guess dressed as a check. So an entry here is a claim with a source behind it, and a
#: factory that is absent is not refused. Widening this by pattern -- every name containing
#: ``moe``, say -- would be exactly the inference that module argues against.
MULTI_RANK_FACTORIES: Final[Mapping[str, str]] = {
    "olmoe_7b_32x4": (
        "7,123,109,888 parameters across 32 routed experts, whose --moe-shard-degree "
        "defaults to 32 and --moe-num-replicas to 2 on edullm/final-model, so the recipe "
        "describes 64 ranks before anybody passes a flag"
    ),
}

#: The flag that names a factory, in both spellings argparse takes. Read as a flag rather than
#: hunting the factory name loose in the text, so that a factory named inside a note, a run id
#: or a ``python -c`` string is not mistaken for one being selected.
_FACTORY_FLAG: Final = "--model-factory"


def multi_rank_evidence(command: str) -> tuple[str, ...]:
    """Every word in this command that says it needs more than one rank, quoted as written.

    Two kinds of word qualify and they are found for different reasons.

    ``--moe-shard-degree`` and ``--moe-num-replicas`` are a mesh stated outright, and a value
    above one is a world size above one whatever else the command says. They are the weaker
    signal in practice: ``.edullm/run.yaml`` on the model branch forbids both -- naming either
    there turns a distributed dispatch into a refusal, because ``with_mesh_flags`` computes the
    pair from the node count -- so the command that actually killed two runs carries neither.
    A hand-typed override on the form is where these turn up.

    The factory is the signal that catches the committed command, and it is read as the value
    of ``--model-factory``. A degree of exactly one is not evidence of anything: it is the
    spelling of a single-rank run, and refusing it would refuse the person who had already
    worked this out.

    KNOWN LIMIT, WRITTEN DOWN RATHER THAN LEFT TO BE DISCOVERED. argparse accepts unambiguous
    prefixes, so ``--model-fact olmoe_7b_32x4`` selects the same factory and is not read here.
    Nothing in this repository or on the model branch writes a flag that way and a reader
    should not start; the alternative -- matching the factory name wherever it appears -- would
    refuse a command that merely mentions it, which is the false refusal this module exists not
    to produce.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        # An unbalanced quote is a shell error rather than a launcher question, and it belongs
        # to `bash -lc` inside the container. Reading half a command and refusing on what was
        # legible would be a refusal about the wrong thing.
        return ()

    found: list[str] = []
    for segment in _simple_commands(tuple(words)):
        for position, word in enumerate(segment):
            after = segment[position + 1] if position + 1 < len(segment) else None
            factory = _value_of(_FACTORY_FLAG, word=word, after=after)
            if factory is not None and factory in MULTI_RANK_FACTORIES:
                found.append(f"--model-factory {factory} is {MULTI_RANK_FACTORIES[factory]}")
                continue
            for flag in MESH_FLAGS:
                value = _value_of(flag, word=word, after=after)
                if value is not None and _above_one(value):
                    found.append(f"{flag} {value} is a mesh of more than one rank")
    return tuple(found)


def launcher_refusals(
    command: str, *, processes: str = RANKS_READ_ON_THE_MACHINE
) -> tuple[str, ...]:
    """Refuse a command that asks for several ranks and starts one, with the line to type.

    ``processes`` is what the remedy puts in ``--nproc-per-node``, because the two callers know
    different things: this runs on a GitHub runner that has not addressed a node, and it also
    runs where ``nvidia-smi`` has already answered. See :data:`RANKS_READ_ON_THE_MACHINE`.

    A tuple rather than a raised error, matching
    :func:`~edullm_platform.block_multinode.command_refusals` beside it, so a caller can print
    everything wrong with a dispatch in one pass instead of one refusal per attempt.

    **THE MESSAGE IS THE DELIVERABLE AS MUCH AS THE CHECK, AND ONE PARAGRAPH OF IT IS ABOUT A
    FILE THE READER IS ABOUT TO BREAK.** The obvious response to being told a command has no
    launcher is to put one in ``.edullm/run.yaml``, which is where the command came from. That
    file is read by two paths: this one, which runs it as written, and
    ``block-run-distributed.yml``, which prepends the rendezvous form. A launcher committed
    there fixes this button and gives the eight-node dispatch sixty-four workers over eight
    cards -- the more expensive of the two failures, arriving later, in the window this fleet
    was bought for. So the refusal says where the launcher goes instead.

    **IT IS NOT ASKED AT ALL WHEN THE FORM IS ABOUT TO SUPPLY A LAUNCHER**, and that gate lives
    at the call site rather than here. ``block-run.yml``'s ``processes`` input composes one
    against the card count once the node has been read, which is after this runs;
    :func:`~edullm_platform.block_multinode.composes_a_launcher` is what the workflow asks
    first, so that ``processes=all`` -- the answer the message below recommends -- is not
    refused by the check recommending it.
    """
    evidence = multi_rank_evidence(command)
    if not evidence:
        return ()
    if carries_the_token(_words(command), LAUNCH_CHECK_WAIVER):
        return ()
    if read_launch_plan(_words(command)).launcher is not None:
        return ()

    return (
        (
            f"{NO_LAUNCHER_CODE}:this command says it needs more than one rank and starts one "
            f"process. {'; '.join(evidence)}. Nothing on this path prepends a launcher -- "
            "block-run.yml hands the command to `edullm-node run`, which hands it to `bash "
            "-lc` exactly as written -- so the container is given every card on the node and "
            "the command takes one of them. The rest are billed and idle, and the run stops "
            "on a message about the parallelism mesh, which is a mesh that could not be built "
            "out of one rank rather than a mesh anybody got wrong. Two runs died that way on "
            "2026-08-10. `.edullm/run.yaml` on the branch carries no launcher and that is "
            "correct rather than an oversight: block-run-distributed.yml prepends the "
            "rendezvous form itself, and a launcher committed in that file would be wrapped "
            "in a second one -- sixty-four workers over eight cards, which is the more "
            "expensive of the two mistakes. The short way out is this form's `processes` "
            "input: set it to `all` and the launcher is composed for you against the card "
            "count read off the node, which is a number this check cannot see. Otherwise put "
            "the launcher in the `command` input and leave that file alone. If one process is "
            "deliberate, write "
            f"{LAUNCH_CHECK_WAIVER} into the command, which records the decision on the run "
            "rather than leaving it to be guessed at from a log. "
            f"{_remedy(command, processes=processes)}"
        ),
    )


def _remedy(command: str, *, processes: str) -> str:
    """Its caveats first and the line to paste last, which is a layout decision.

    THE CORRECTED COMMAND GOES AT THE END BECAUSE EVERYTHING HERE ARRIVES AS ONE PARAGRAPH.
    :func:`~edullm_platform.launchers._no_launcher_refusal` records the same finding against
    the same problem: wrapped into a block of prose, a command with anything written after it
    has no visible end, and a reader copying it takes the next six words with it. So the notes
    that qualify it come before it and nothing follows it.

    ``corrected_command`` splices the launcher in after the interpreter rather than wrapping
    the whole line, which is what keeps ``"$EDULLM_RUN_ID"`` a variable instead of fourteen
    literal characters. It answers ``None`` for a command that does not start with a Python
    interpreter, and there is nothing to print in that case but where the launcher goes.

    THE SENTENCE ABOUT ``torchrun`` IS NOT PADDING. Told to "put torchrun in front of it", a
    reader writes ``torchrun ... python .edullm/train_on_corpus.py``, and torchrun's positional
    is a *script path* it supplies an interpreter for -- so what runs is ``python -u python
    .edullm/train_on_corpus.py``, on every rank, which is the same wasted dispatch with a
    different message on it. ``torchrun_command`` passes ``--no-python`` for this exact reason
    and half the distributed lane's refusals are about it.
    """
    runtime = (
        "`gpu` is torchrun's own value for one process per visible device, resolved on the "
        "node, and it is written rather than a number because this check runs before any "
        "machine has been addressed; put the card count there instead if you would rather pin "
        "it. "
        if processes == RANKS_READ_ON_THE_MACHINE
        else ""
    )
    spelling = (
        "`torchrun` is the same launcher under another name, and if you write it instead then "
        "drop the leading `python`: torchrun's positional is a script path and it supplies the "
        "interpreter itself, so `torchrun ... python script.py` runs a file called `python`. "
    )
    corrected = corrected_command(_words(command), devices=processes)
    if corrected is None:
        return (
            f"{runtime}{spelling}Put the launcher immediately in front of the program that "
            f"trains: python -m torch.distributed.run --nproc-per-node={processes} --standalone"
        )
    return f"{runtime}{spelling}Run it under a launcher: {corrected}"


def _words(command: str) -> tuple[str, ...]:
    """The command as a shell would split it, or as one word when it will not split.

    The single word is not a fallback that pretends to have parsed. Both readers this feeds
    ask whether a particular thing is in command position, and one unsplittable blob answers
    no to both -- which is the answer that refuses nothing.
    """
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return (command,)


def _value_of(flag: str, *, word: str, after: str | None) -> str | None:
    """What ``--flag value`` or ``--flag=value`` was given, for one position in a segment.

    Both spellings reach argparse identically and both are written by hand in this project, so
    a reader that understood one of them would report the other as absent -- which here is the
    branch that lets a command through.
    """
    if word == flag:
        return after
    if word.startswith(f"{flag}="):
        return word[len(flag) + 1 :]
    return None


def _above_one(value: str) -> bool:
    """Whether a mesh flag names a number greater than one.

    Anything that is not a number answers no. A degree written as a shell variable resolves
    inside the container and cannot be compared here, and the direction to fail in is the one
    that does not refuse a command nobody can prove wrong.
    """
    try:
        return int(value) > 1
    except ValueError:
        return False
