"""The command that cannot start, and the far larger set of commands that must keep starting.

Two runs died on 2026-08-10 because ``block-run.yml`` ran a 64-rank command as one process on
one card. The guard for that is easy to write and easy to write far too widely: the node has
eight cards, so "refuse anything that does not use eight" catches the defect and also catches
``nvidia-smi``, which is the single most common thing anybody types into this button.

So the tests below come in two halves and the second half is the one that matters. The first
pins the refusal and its message. The second is a list of ordinary work -- probes, one-liners,
a small single-card training run, a command somebody already fixed -- and every entry in it is
a dispatch that a slightly wider rule would refuse during a window nobody can extend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.block_launcher import (
    MULTI_RANK_FACTORIES,
    NO_LAUNCHER_CODE,
    launcher_refusals,
    multi_rank_evidence,
)
from edullm_platform.block_multinode import (
    Candidate,
    mesh_for,
    rendezvous_for,
    torchrun_command,
    with_mesh_flags,
)
from edullm_platform.launchers import LAUNCH_CHECK_WAIVER

#: The command that killed two runs, copied from ``.edullm/run.yaml`` on
#: ``edu-llm/OLMo-core@edullm/final-model``. Written out here rather than fetched, because a
#: test that reaches GitHub is a test that fails when GitHub is slow; the cost is that this
#: goes stale, and what it is pinning is the *shape* -- a training entrypoint, the MoE factory,
#: no launcher -- rather than the flag list.
COMMITTED = (
    "python .edullm/train_on_corpus.py "
    "--model-factory olmoe_7b_32x4 "
    "--dataset-id pretrain/reservoir-dolma2 "
    "--dataset-version v1 "
    "--dataset-tokenizer tokenizer/dolma2-bpe "
    "--require-val "
    "--sequence-length 4096 "
    "--global-batch-size 4194304 "
    "--rank-microbatch-size 16384 "
    "--steps 11921 "
    "--param-dtype bfloat16"
)

#: What people actually run through this button between training runs. Every one of these is a
#: single process on a machine with eight cards, on purpose, and every one of them is a
#: dispatch somebody makes while a colleague's run is going.
ORDINARY_WORK = (
    "nvidia-smi",
    "nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv",
    "python -c 'import torch; print(torch.cuda.device_count())'",
    "python -c 'import olmo_core; print(olmo_core.__file__)'",
    "bash -lc 'df -h /scratch && free -g && nvidia-smi -L'",
    "df -h /scratch",
    "python .edullm/train_on_corpus.py --model-factory olmo2_1B --steps 10",
    "bash -lc 'PYTHONPATH=/work/src python .edullm/probe_group_words.py --steps 4'",
)


def refusal(command: str) -> str:
    """The one refusal this command earns, failing the test if it earned none or several."""
    found = launcher_refusals(command)
    assert len(found) == 1, f"expected exactly one refusal for {command!r}, got {found}"
    return found[0]


# ---------------------------------------------------------------------------------------
# The defect.
# ---------------------------------------------------------------------------------------


def test_the_command_that_killed_two_runs_is_refused() -> None:
    """Mutation: run whatever arrives, which is what this path did until now.

    ``olmoe_7b_32x4`` is 7.12 billion parameters over 32 routed experts and its shard degree
    defaults to 32. Started as one process it holds one of the node's eight cards, and the
    error it eventually prints is about the parallelism mesh -- which sends the reader to the
    mesh flags, where there is nothing wrong.
    """
    found = refusal(COMMITTED)

    assert found.startswith(f"{NO_LAUNCHER_CODE}:")
    assert "olmoe_7b_32x4" in found


def test_the_refusal_quotes_the_word_it_decided_on() -> None:
    """Mutation: refuse with a verdict and no evidence.

    A person told their command needs more than one rank, and not told which word said so,
    cannot tell a correct refusal from a rule that fired on the wrong thing -- and the second
    reading is the one that gets a guard disabled at two in the morning.
    """
    evidence = multi_rank_evidence(COMMITTED)

    assert len(evidence) == 1
    assert evidence[0].startswith("--model-factory olmoe_7b_32x4")
    assert "7,123,109,888" in evidence[0]


@pytest.mark.parametrize(
    "launcher",
    [
        "python -m torch.distributed.run --nproc-per-node=8 --standalone",
        "python -m torch.distributed.launch --nproc-per-node=8",
        "torchrun --standalone --nproc-per-node 8 --no-python python",
    ],
)
def test_the_same_command_under_a_launcher_passes(launcher: str) -> None:
    """The other half of the rule, and the half that says it is about launchers and not models.

    Nothing here judges whether the rank count is right -- ``block-run.yml`` gives the whole
    node to the container and this step has not addressed a node, so there is no count to
    compare against. A launcher is the whole of what it asks for.
    """
    fixed = COMMITTED.replace("python .edullm/", f"{launcher} .edullm/", 1)

    assert launcher_refusals(fixed) == ()


def test_a_mesh_named_on_the_command_is_evidence_on_its_own() -> None:
    """The hand-typed override, which is where the mesh flags actually turn up.

    ``.edullm/run.yaml`` on the model branch forbids both flags -- ``with_mesh_flags`` computes
    the pair from the node count and refuses a command that disagrees -- so the committed
    command carries neither and this signal never fires on it. It fires on somebody pasting a
    line out of the distributed lane into the single-node form.
    """
    for spelling in (
        "python train.py --moe-shard-degree 8",
        "python train.py --moe-shard-degree=8",
        "python train.py --moe-num-replicas 4",
        "python train.py --moe-num-replicas=4",
    ):
        assert launcher_refusals(spelling), spelling


def test_a_command_read_through_its_shell_wrapper() -> None:
    """``bash -lc '...'`` is how half of this lane's commands are written, and both the
    evidence and the launcher live inside the quoted string rather than beside it. A reader
    that stopped at the wrapper would pass every wrapped dispatch."""
    wrapped = f"bash -lc '{COMMITTED} --steps 4'"
    fixed = wrapped.replace("python .edullm/", "torchrun --no-python python .edullm/", 1)

    assert launcher_refusals(wrapped)
    assert launcher_refusals(fixed) == ()


# ---------------------------------------------------------------------------------------
# The far larger set that has to keep working.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ORDINARY_WORK)
def test_single_process_work_this_button_exists_for_is_not_refused(command: str) -> None:
    """THE FAILURE THIS FILE IS MOSTLY ABOUT, AND IT IS AN OUTAGE RATHER THAN AN ANNOYANCE.

    ``launchers.require_a_process_for_every_device`` would refuse every line here: the node
    has eight cards and each of these starts one process. That rule is right on the submission
    path, where each command is a training run against a profile somebody chose, and it is
    wrong on the only door fifteen of thirty-five people have to a machine. A refusal on
    ``nvidia-smi`` during a live window is a control people route around rather than read.
    """
    assert launcher_refusals(command) == ()


def test_a_degree_of_one_is_a_single_rank_run_and_not_evidence() -> None:
    """The spelling of somebody who has already worked this out, refused by an over-eager
    reading of the same flag. One replica of one shard group is a world size of one, which is
    what it says and what it means."""
    assert launcher_refusals("python train.py --moe-shard-degree 1 --moe-num-replicas 1") == ()


def test_a_mesh_flag_this_cannot_read_is_left_alone() -> None:
    """A degree resolved inside the container is not a number to compare here, and the
    direction to be wrong in is the one that does not refuse a command nobody can prove
    wrong."""
    assert launcher_refusals('python train.py --moe-shard-degree "$DEGREE"') == ()


def test_a_factory_the_platform_has_established_nothing_about_is_not_refused() -> None:
    """THE LIMIT, KEPT AS A PASSING TEST RATHER THAN LEFT AS A SURPRISE.

    This is a backstop and not a proof. Nothing on the platform records a model's size, so an
    entry in ``MULTI_RANK_FACTORIES`` is a claim somebody established rather than something
    derived, and a factory that is absent reaches the node unrefused. Widening it by pattern --
    every name containing ``moe`` -- is the inference :mod:`edullm_platform.accelerators`
    argues at length against, and it would refuse a 190M test model with a sparse layer in it.
    """
    assert "olmoe_7b_32x4" in MULTI_RANK_FACTORIES
    assert launcher_refusals("python train.py --model-factory olmoe_1b_7b_next") == ()


def test_the_factory_has_to_be_selected_rather_than_merely_mentioned() -> None:
    """Mutation: look for the factory name anywhere in the text.

    It is the more robust-looking rule and it refuses a command that prints the name, greps a
    log for it, or carries it in a run id. The flag is what selects a factory.
    """
    assert launcher_refusals("python -c \"print('olmoe_7b_32x4')\"") == ()
    assert launcher_refusals("bash -lc 'grep olmoe_7b_32x4 /scratch/a-run/log/train.log'") == ()


def test_an_unparseable_command_is_somebody_else_s_refusal() -> None:
    """An unbalanced quote is a shell error and it belongs to ``bash -lc`` in the container.
    Reading half a command and refusing on what was legible would be a refusal about the wrong
    thing, arriving under a code that names launchers."""
    assert launcher_refusals(f"bash -lc '{COMMITTED}") == ()


def test_the_waiver_is_the_way_past_it_and_is_spelled_the_way_it_is_everywhere_else() -> None:
    """A guard with no way out is one people get around by editing the thing it reads, which
    here means committing a launcher into ``.edullm/run.yaml`` and breaking the eight-node
    lane. The token is ``launchers.LAUNCH_CHECK_WAIVER`` rather than a spelling of this
    module's own, because a researcher should not have to learn which guard they are getting
    past to know how to say so."""
    assert launcher_refusals(f"{LAUNCH_CHECK_WAIVER} {COMMITTED}") == ()


# ---------------------------------------------------------------------------------------
# The message, which is as much the deliverable as the check.
# ---------------------------------------------------------------------------------------


def test_the_refusal_ends_with_a_line_that_can_be_pasted() -> None:
    """Mutation: name the launcher and let the reader assemble it.

    The corrected line is last because everything here arrives as one wrapped paragraph, and a
    command with prose after it has no visible end -- a reader copying it takes the next six
    words with it. It is spliced in after the interpreter rather than wrapped around the whole
    line, which is what keeps every other character of the command as the researcher wrote it.
    """
    found = refusal(COMMITTED)
    remedy = found.split("Run it under a launcher: ", 1)[1]

    assert found.rstrip().endswith(remedy)
    assert remedy.startswith("python -m torch.distributed.run --nproc-per-node=gpu --standalone")
    assert remedy.endswith("--param-dtype bfloat16")
    assert "--model-factory olmoe_7b_32x4" in remedy


def test_the_remedy_names_the_count_the_caller_could_actually_see() -> None:
    """Two callers, two honest answers, and neither of them a number from memory.

    ``block-run.yml`` runs before any machine has been addressed, so it takes the default and
    writes torchrun's own ``gpu`` -- one process per visible device, resolved in the container.
    A caller that has read ``nvidia-smi`` passes the figure, and then the sentence explaining
    ``gpu`` is not printed, because it would be explaining a word that is not there.
    """
    unseen = refusal(COMMITTED)
    counted = launcher_refusals(COMMITTED, processes="8")[0]

    assert "--nproc-per-node=gpu" in unseen
    assert "torchrun's own value for one process per visible device" in unseen
    assert "--nproc-per-node=8" in counted
    assert "one process per visible device" not in counted


def test_the_refusal_says_not_to_repair_the_committed_file() -> None:
    """THE PARAGRAPH THAT STOPS THIS FIX BECOMING A WORSE DEFECT.

    The obvious response to "your command has no launcher" is to put one where the command
    came from. ``.edullm/run.yaml`` is read by two paths: this one runs it as written, and
    ``block-run-distributed.yml`` prepends the rendezvous form. A launcher committed there
    fixes the button and gives the eight-node dispatch sixty-four workers over eight cards --
    later, more expensively, in the window the fleet was bought for.
    """
    found = refusal(COMMITTED)

    assert ".edullm/run.yaml" in found
    assert "block-run-distributed.yml" in found
    assert "sixty-four workers over eight cards" in found
    assert "leave that file alone" in found


def test_the_refusal_warns_off_the_spelling_that_looks_like_the_fix() -> None:
    """``torchrun`` written in front of ``python script.py`` runs a file called ``python`` on
    every rank, because torchrun's positional is a script path and it supplies the interpreter
    itself. It is the first thing somebody told "put torchrun in front of it" will type."""
    found = refusal(COMMITTED)

    assert "drop the leading `python`" in found
    assert "runs a file called `python`" in found


def test_the_refusal_names_the_waiver_and_the_incident() -> None:
    found = refusal(COMMITTED)

    assert LAUNCH_CHECK_WAIVER in found
    assert "2026-08-10" in found


def test_this_check_would_refuse_the_distributed_lane_and_must_never_be_asked_there() -> None:
    """**THE MOST DANGEROUS PROPERTY OF THIS MODULE, PINNED SO THAT IT CANNOT BE STUMBLED INTO.**

    The command ``block-run-distributed.yml`` is *supposed* to be given carries no launcher --
    the form says so in as many words, because the workflow prepends the rendezvous form itself
    and a command bringing its own would be wrapped in a second. It also carries
    ``--model-factory olmoe_7b_32x4``. That is precisely the shape this module refuses, so
    :func:`launcher_refusals` asked on the distributed path would refuse the eight-node run for
    being correct.

    Nothing asks it, and the assertion below is that nothing *can*: the only importer in the
    tree is the single-node workflow. It is one plausible edit away from being otherwise --
    "the launcher check should run on both lanes" is a reasonable-sounding sentence, and acting
    on it turns the 64-rank dispatch into a refusal in a window that cannot be extended. This
    test is here to be the thing that says no.

    The narrowness is the whole reason the module is safe on one lane and lethal on the other,
    and it is why the check is a call at one site rather than a rule in a shared library.
    """
    lane_input = (
        "python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4 "
        "--dataset-id=pretrain/reservoir-dolma2 --steps=11921"
    )

    assert launcher_refusals(lane_input), (
        "the distributed lane's own documented command is no longer refused by this module, so "
        "the hazard this test guards has moved rather than gone. Re-derive it before deleting."
    )

    root = Path(__file__).resolve().parents[1]
    importers = {
        path.relative_to(root).as_posix()
        for path in [*(root / "src").rglob("*.py"), *(root / "tools").rglob("*.py")]
        if "block_launcher" in path.read_text(encoding="utf-8")
        and "import" in path.read_text(encoding="utf-8").split("block_launcher")[0].splitlines()[-1]
    }
    workflows = {
        path.name
        for path in (root / ".github" / "workflows").glob("*.yml")
        if "block_launcher" in path.read_text(encoding="utf-8")
    }

    assert importers == set(), f"a library module now imports block_launcher: {importers}"
    assert workflows == {"block-run.yml"}, (
        f"block_launcher is reachable from {sorted(workflows)}. Only the single-node button may "
        "ask it: block-run-distributed.yml's correct command has no launcher by design, and "
        "this module refuses exactly that shape."
    )


def test_the_line_the_distributed_lane_composes_is_not_refused_even_if_it_were_asked() -> None:
    """The second, independent reason the eight-node run is safe, which is worth having because
    the first is a fact about imports and imports change.

    What reaches a node on that path is the rendezvous form with the entrypoint spliced in
    after ``--no-python``, so it opens with ``torchrun`` and :func:`read_launch_plan` finds a
    launcher in command position. Even a future edit that did wire this module into the
    distributed lane at the wrong point would have to do it *before* the composition to break
    anything.
    """
    mesh = mesh_for(nodes=8, gpus_per_node=8)
    spliced, refusals = with_mesh_flags(
        "python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4 --steps=11921", mesh=mesh
    )
    assert not refusals
    nodes = [
        Candidate(node=number, instance_id=f"i-{number:017d}", private_ip=f"10.0.0.{number}")
        for number in range(1, 9)
    ]
    line = torchrun_command(
        mesh=mesh, rendezvous=rendezvous_for(nodes, run="final-model-64"), command=spliced
    )

    assert line.startswith("torchrun ")
    assert launcher_refusals(line) == ()
