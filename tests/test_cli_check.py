"""``edullm check``: what it clears, and what it refuses before anything is queued.

Every case here is either the happy path or a mistake somebody actually makes, because a
test that a constant equals itself is worse than none. The mistakes are taken from the
transcripts in ``docs-frank/working/terminal-mockups/``, which were written by watching
people use a design rather than by reading it -- a mistyped dataset release, four cards
picked out of habit with a command that starts one process, a corpus that is registered and
is not a corpus, a team the roster cannot resolve.

The whole of the reviewed configuration is the real ``config/``, so an assertion here is an
assertion about what the platform would do rather than about a fixture.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from pathlib import Path
from re import findall

import pytest

from edullm_platform.cli.configuration import (
    PACKAGED_CONFIG_DIRECTORY,
    load_reviewed_configuration,
)
from edullm_platform.cli.lane import placement_said, placement_verdict
from edullm_platform.cli.main import (
    BUILT_TODAY,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNUSABLE,
    NOT_BUILT_YET,
    RETIRED,
    build_parser_and_verbs,
    main,
)
from edullm_platform.cli.preflight import Preflight
from edullm_platform.cli.presentation import config_source_said
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.placement import CAPACITY_FILENAME, PLACES_UNRELIABLY
from tests.cli_support import (
    CONFIG_DIR,
    SUBMITTER_ON_TWO_TEAMS,
    SUBMITTER_TEAM,
    FakeRunner,
    git_answers,
    invoke,
    write_spec,
)

#: A command that starts one process per device on an eight-card shape, so a case about
#: something else is not also a case about ``process_per_device``.
EIGHT_PROCESS_COMMAND = (
    "bash -lc 'python -m torch.distributed.run --nproc-per-node=8 --standalone "
    '.edullm/train_on_corpus.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR"\''
)


def checkout(tmp_path: Path, **spec: object) -> tuple[Path, FakeRunner]:
    write_spec(tmp_path, **spec)  # type: ignore[arg-type]
    return tmp_path, FakeRunner(git_answers(tmp_path))


def test_check_reaches_no_network_at_all_however_stale_this_install_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE PROPERTY THE VERSION PROBE WAS DELIBERATELY KEPT OUT OF THIS VERB TO PROTECT.**

    Mutation: probe for a newer release here too. ``check`` answers in a fraction of a
    second and asks nothing, which is what makes it the verb somebody runs half a dozen
    times while editing a spec and the verb that works on a cluster login node with no
    egress. One API call is a tenth of a second on a good connection and a hang on a bad
    one, spent on a question that only matters at the moment a submission costs somebody
    else's approval -- which is where the probe lives instead.

    Asserted as the absence of a call rather than as a duration, because a timing
    assertion is a flake on a loaded machine and this is the fact underneath it: nothing
    was asked, so there was nothing to wait for.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("gh") == []
    assert [argv[0] for argv in runner.calls] == ["git"] * len(runner.calls)


@pytest.mark.parametrize("refused", [False, True])
def test_check_names_the_configuration_that_answered_whichever_way_it_went(
    refused: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Two runs against two configurations printed the same bytes, and one was stale.**

    ``configuration.directory`` appeared exactly once in the whole ``cli/`` package, in a
    comment inside the spec file ``scaffold.py`` writes, so nothing a terminal showed said
    which ``config/*.yaml`` had answered. The packaged copy beats a checkout's ``config/``
    in the precedence order, which makes the maintainer standing in the platform tree the
    person most likely to be validating against the wheel's frozen copy and the person the
    output hid that from.

    Mutation: print it only when the check passes. A stale validator's damage is a refusal
    that is wrong, so the reader who most needs to know which files decided is the one
    reading a refusal -- and that reader is also the common case.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")
    elsewhere = tmp_path / "another-config"
    shutil.copytree(CONFIG_DIR, elsewhere)

    code, out, err = invoke(
        [
            "check",
            "--dataset",
            "regmix-10b-v1" if not refused else "a-corpus-nothing-registers",
            "--experiment",
            "an-experiment",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
        config_dir=elsewhere,
    )

    assert code == (EXIT_REFUSED if refused else EXIT_OK), out + err
    assert out.startswith(f"checked against {elsewhere}\n\n")
    # No colour and no escape, here as everywhere: a piped check and a terminal check are
    # the same bytes, which is what makes a pasted transcript what the next person sees.
    assert "\x1b" not in out


def test_the_configuration_line_says_when_the_copy_is_the_install_s_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precedence trap named, rather than left to be read out of a path.

    Mutation: print the path and nothing else. A ``site-packages`` path is a strong hint and
    ``--config-dir`` can point anywhere, including at a copy that happens to live under an
    install; what decides the question is whether this is the copy the wheel carries, and
    that is a comparison rather than a spelling.
    """
    assert config_source_said(PACKAGED_CONFIG_DIRECTORY).endswith("the copy this install carries")
    assert config_source_said(CONFIG_DIR) == f"checked against {CONFIG_DIR}"


def test_a_clean_submission_is_cleared_and_priced_from_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path, and the four things the transcripts say it has to print.

    Mutation: drop the cost block, or price it from anything but the catalog's rate. A
    submitter reading a figure the approver page does not carry has no way to know which of
    the two is the one that routes their run.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "no refusals. edullm submit will dispatch this." in out
    # gpu-1xa10g at $1.006 for olmo-core-train's 24h ceiling and two attempts, which is
    # the arithmetic config/workload-catalog.yaml and config/policy.yaml between them fix.
    # THE TOTAL IS ON THE HEADING LINE AND THE ARITHMETIC IS UNDER IT. A reader scanning
    # for the money finds it in the first line of the block rather than at the end of a
    # five-factor product.
    assert "worst case  $48.29" in out
    assert "$1.006/hour x 1 node x 24h x 2 attempts x 1 cell" in out
    # AUTOMATIC, AND IT PRINTED "routine -> run-approval-lead" UNTIL POLICY v5. Forty-eight
    # dollars in one cell is a tenth of the one bound, so a full day of training on one card
    # is now released by nobody. That is the change a submitter notices first.
    assert "automatic. One cell, under $500, so nobody releases this." in out
    assert f"team              {SUBMITTER_TEAM}" in out


def test_a_shape_that_has_never_placed_is_said_so_before_the_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE FREE VERB WAS SILENT ABOUT THE ONE REASON A SUBMISSION WILL NOT WORK.

    ``config/capacity.yaml`` records 4,060 ``InsufficientInstanceCapacity`` refusals for
    ``gpu-8xl40s`` over two days and not one instance obtained. ``check`` priced it at
    $1,446.30, routed it to a lead, and said nothing at all. ``edullm run`` and
    ``edullm shell`` -- which are ungated, uncited and spend money without an approval --
    both call ``placement_warning`` and both do say. So the command that exists to tell a
    researcher whether a submission will work was the one that would not.

    Mutation: warn on the lane verbs only, which is the shipped behaviour and is what this
    case is written against. A submitter who reads this sentence picks another shape for
    free; one who does not spends a lead's approval and then waits for a machine that never
    arrives, with nothing written anywhere -- a job that cannot be placed sits in
    ``RUNNABLE``, which is indistinguishable from one that is merely queued.

    The sentence is asserted to be the lane's own rather than a second composition of the
    same facts. Two wordings for one verdict is how ``gpu-8xa100`` came to be told "may not
    place" by one caller while a queue was running jobs on it.
    """
    root, runner = checkout(tmp_path, compute="gpu-8xl40s", command=EIGHT_PROCESS_COMMAND)

    code, out, err = invoke(
        ["check", "--dataset", "none", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    said = placement_said(
        placement_verdict(load_reviewed_configuration(CONFIG_DIR), "gpu-8xl40s")
    )

    assert code == EXIT_OK, out + err
    assert said is not None
    assert said in " ".join(out.split())
    assert f"config/{CAPACITY_FILENAME}" in out
    # Above the price rather than below it, because the price is what a reader stops at.
    assert out.index("capacity.yaml") < out.index("worst case")
    # The clause the lane adds and this verb must not: check starts nothing, so there is
    # nothing here for a Ctrl-C to stop.
    assert "Ctrl-C" not in out
    assert err == ""


def test_a_shape_that_places_reliably_is_told_nothing_about_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print a line on every submission, empty or reassuring.

    Ten of the seventeen priced shapes warn and seven do not, and a warning that appears
    over the seven is one submitters learn to skip past by the fifth run -- which costs the
    warning its whole value on the ten where it is the only thing standing between somebody
    and a wait that never ends.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "capacity" not in out.lower()
    assert err == ""


def test_the_capacity_answer_is_in_the_document_an_agent_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the sentence and leave ``--json`` alone.

    ``AGENTS.md`` tells an agent to read ``check --json`` and to match on codes rather than
    on prose, so a warning that exists only in the paragraphs is invisible to every caller
    the machine-readable form was built for. The verdict is carried beside the sentence for
    the same reason ``history`` carries counts beside ``said``: the verdict is the structure
    to branch on and the sentence is prose that will be reworded.

    On stderr under ``--json``, because stdout there is one document and nothing else.
    """
    root, runner = checkout(tmp_path, compute="gpu-8xl40s", command=EIGHT_PROCESS_COMMAND)

    code, out, err = invoke(
        ["check", "--dataset", "none", "--experiment", "an-experiment", "--json"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )
    document = json.loads(out)

    assert code == EXIT_OK, out + err
    assert document["placement"]["profile"] == "gpu-8xl40s"
    assert document["placement"]["places"] == PLACES_UNRELIABLY
    assert document["placement"]["measured_by"]
    assert "gpu-8xl40s" in document["placement"]["said"]
    assert "gpu-8xl40s" in err


def test_the_document_says_nothing_rather_than_nothing_known_for_a_shape_that_places(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave the key out where there is no warning.

    A key that is absent on one path is a key every caller has to guard, which is the rule
    ``check_document`` already states about ``run_id`` and ``manifest_sha256``. ``None`` is
    the answer for a shape that places and for a check that never resolved one.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment", "--json"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert json.loads(out)["placement"] is None


def test_a_cheap_single_cell_run_is_told_nobody_releases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The automatic class, with the bound printed as the policy file states it.

    Mutation: write ``$500`` into ``presentation.py`` rather than interpolating
    ``automatic_below_cost_usd``. ``test_cli_no_hardcoded_bounds.py`` is what normally
    catches that; this catches the half of it that reads the wrong field, because the value
    printed here has to be the one ``classify_request`` compared against.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, out, err = invoke(
        [
            "check",
            "--dataset",
            "none",
            "--experiment",
            "an-experiment",
            "--hours",
            "0.5",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "automatic. One cell, under $500, so nobody releases this." in out


def test_a_mistyped_dataset_release_is_refused_with_the_list_it_should_have_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first thing that goes wrong for a first-time submitter, twice in the transcripts.

    Mutation: refuse without listing what is offered. ``math-frontload-100m`` against
    ``math-frontload-100m-v1`` is one suffix, and a refusal that does not print the
    neighbours makes a researcher go and find ``config/datasets.yaml`` to learn it.
    """
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        ["check", "--dataset", "math-frontload-100m", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  unregistered_dataset" in out
    assert "math-frontload-100m-v1" in out
    assert "Nothing was dispatched." in out
    # `check` refuses on stdout rather than stderr, because a refusal is the answer to the
    # question rather than a fault: this is the output somebody pipes into a file or reads
    # back to a colleague, and half of it going to the other stream splits it.
    assert err == ""


def test_a_corpus_that_is_registered_and_is_not_one_is_refused_as_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dataset_is_not_a_corpus`` and ``unregistered_dataset`` are different sentences.

    Mutation: fold the two together. The registry knows exactly what a vendor mirror is,
    and answering "nothing registers this" about an entry that is in the file the refusal
    points at sends somebody to add a line that is already there.
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        ["check", "--dataset", "openai-prm800k-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  dataset_is_not_a_corpus" in out
    assert "unregistered_dataset" not in out


@pytest.mark.parametrize("release", ["dolma-2026-07", "fineweb-edu-1b-v2"])
def test_a_retired_release_is_refused_on_the_laptop_rather_than_merely_unlisted(
    release: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE VERB THAT COST NOTHING AND WAS SAYING NOTHING. Mutation: leave `retired` to the
    dropdown.

    This printed "no refusals. edullm submit will dispatch this." for both retired names,
    measured on 2026-08-05, and it was telling the truth about a platform in which the only
    thing refusing them was a list of options on a GitHub form. A researcher who ran this
    before dispatching learned nothing, and the reason it is worth saying here rather than
    only in the compile job is that here it costs a fraction of a second and no queue wait.

    Asserted on stdout with the code, because that is what a skill matches on and what a
    researcher pastes to a colleague.
    """
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        ["check", "--dataset", release, "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  retired_dataset_release" in out
    assert "no refusals" not in out
    assert "config/datasets.yaml" in out
    assert err == ""


def test_the_unregistered_refusal_suggests_no_name_the_next_check_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#232'S CORRECTION, ASKED OF THE OTHER REFUSAL THAT WAS MAKING THE SAME MISTAKE.
    Mutation: list every registered name, since all of them are real entries.

    ``_resolve_workload`` records why the workload refusal stopped naming the whole catalog:
    a name offered to somebody who has just been refused is a second refusal waiting, and
    listing everything reproduces inside an error message the defect the dropdown tests
    exist to keep off a menu. This refusal listed all twenty-four registered names, five of
    which the very next check refuses by family and two of which are retired.

    Derived from the registry in both directions rather than pinned to a list, so
    registering a corpus does not move this test and building a refusal does. The tokenizers
    and the vendor mirror are the live instances; the two retired names are the ones this
    change added.
    """
    root, runner = checkout(tmp_path)
    registry = load_yaml(CONFIG_DIR / "datasets.yaml", DatasetRegistry)

    code, out, _ = invoke(
        ["check", "--dataset", "no-such-corpus-v9", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  unregistered_dataset" in out
    # The refusal wraps, so the names are read out of the words rather than out of a line.
    named = {word.strip(" ,.") for word in out.split()}
    suggested = named & (registry.release_ids | registry.reference_ids)

    assert suggested == set(registry.names_a_run_may_still_use()), (
        "the refusal and the registry disagree about which names a submission could take. "
        "A name suggested here that some check refuses is a second refusal waiting; a "
        "usable name left out sends a researcher to config/datasets.yaml to find it"
    )
    for refused in ("smollm2-bpe-v1", "openai-prm800k-v1", "dolma-2026-07"):
        assert refused not in suggested
    assert "regmix-10b-v1" in suggested, (
        "no usable corpus is suggested at all, so the comparison above is between two empty "
        "sets and the refusal could say anything"
    )


def test_four_cards_and_a_command_that_starts_one_process_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``nathan-zhao-curriculum-matrix.md``'s refusal, reached from the CLI.

    Mutation: check the command against the workload's own idea of a machine rather than
    against the resolved profile. ``--compute`` is what the run lands on, and a submission
    cleared against anything else trains on one device, bills for four, and exits zero.
    """
    root, runner = checkout(tmp_path, compute="gpu-4xa10g")

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  process_per_device" in out
    assert "torch.distributed.run" in out


def test_bfloat16_on_the_only_eight_card_shape_that_places_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal a submitter meets by taking the one multi-card shape this account can get.

    ``config/capacity.yaml`` records ``gpu-8xt4`` as the only shape above four cards that
    places, and its T4s are Turing and have no bfloat16. So the shape somebody is pushed
    toward by scarcity is the one their recipe cannot run on, and until this refusal existed
    the whole path -- classification, a lead's approval, admission, placement -- ran before
    anything said so.

    Mutation: refuse in ``compile_submission`` only. This verb exists so that a refusal is
    met before the queue rather than after the gate, and a rule the CLI does not ask is a
    rule a submitter meets in a GitHub Actions log with the approval already spent.
    """
    root, runner = checkout(
        tmp_path,
        compute="gpu-8xt4",
        command=(
            "bash -lc 'python -m torch.distributed.run --nproc-per-node=8 --standalone "
            '.edullm/train_on_corpus.py "$EDULLM_RUN_ID" '
            '--save-folder "$EDULLM_CHECKPOINT_DIR" '
            "train_module.dp_config.param_dtype=bfloat16'"
        ),
    )

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  bfloat16_not_in_the_hardware" in out
    assert "Turing" in out
    # The shape it may not place on is a separate sentence and a warning rather than a
    # refusal, and gpu-8xt4 places, so nothing here should be mentioning placement at all.
    assert "may not place" not in out


def test_a_fanout_is_told_that_no_size_of_one_releases_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule ``classify_request`` states in capitals, said where a submitter can read it.

    Mutation: let the cost decide alone. A sixty-four cell sweep at both count ceilings is
    genuinely under five dollars, and what the total does not carry is that it is sixty-four
    machines starting at once.
    """
    root, runner = checkout(
        tmp_path, workload="olmo-core-check", compute="gpu-1xt4", fanout=(9, "arm")
    )

    code, out, err = invoke(
        ["check", "--dataset", "none", "--experiment", "an-experiment", "--hours", "0.25"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "9 cells, index parameter 'arm'" in out
    assert "a fan-out is never released automatically, whatever it costs" in out


def test_a_dirty_tree_is_refused_before_anything_else_is_considered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One of the three things ``decisions.md`` says the recorded path needs.

    Mutation: warn instead of refusing. A dirty tree submits the last commit, so what runs
    is not what is on the laptop and nothing downstream can tell those two apart -- the
    failure arrives as a result that does not reproduce, weeks later.
    """
    write_spec(tmp_path)
    runner = FakeRunner(git_answers(tmp_path, dirty=("src/train.py",)))

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  uncommitted_changes" in out
    assert "src/train.py" in out


def test_an_unpushed_commit_is_refused_naming_the_push_rather_than_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal ``sophia-zhang-external-base-model.md`` records twenty minutes lost to.

    Mutation: leave this to the registry. ``no_published_image`` names a digest and an ECR
    repository, and the thing to do about it is a push -- which is what this says, along
    with the one reason this answer can be wrong.
    """
    write_spec(tmp_path)
    runner = FakeRunner(git_answers(tmp_path, pushed=False))

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  commit_not_pushed" in out
    assert "git fetch" in out


def test_a_submitter_on_two_declared_groups_is_asked_rather_than_guessed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pending decision, left pending.

    Mutation: pick the first, or the one they lead. ``decisions.md`` records the variant
    that resolves silently as the one nobody would notice going wrong, because it bills a
    lead's own group for work they did as a member of somebody else's -- and team is what
    cost attribution groups on.
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
        login=SUBMITTER_ON_TWO_TEAMS,
    )

    assert code == EXIT_REFUSED
    assert "refused  team_is_ambiguous" in out
    assert "--team" in out


def test_naming_a_team_the_roster_does_not_put_you_on_is_refused_before_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``submitter_not_in_claimed_team``, asked here rather than after an approval is spent.

    Mutation: accept whatever ``--team`` says. Authorization compares the claim against the
    roster on the far side of the gate, so a mis-claimed team is a lead's attention spent on
    a run admission then refuses.
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        [
            "check",
            "--dataset",
            "regmix-10b-v1",
            "--experiment",
            "an-experiment",
            "--team",
            "pre-training",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  submitter_not_in_claimed_team" in out


def test_somebody_the_roster_has_never_heard_of_is_told_that_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first refusal the compile job makes, made first here for the same reason.

    Mutation: report it after the workload or the dataset. A refusal naming a workload
    profile sends somebody who is not on the roster to correct a field that was never what
    stood in the way.

    The count is the second line rather than the first now, and the line above it is the one
    thing allowed there: which reviewed configuration produced the refusal. Everything
    ``render_refusals`` argues about the count coming before the reasons still holds -- a
    reader learns nothing was dispatched before they learn why -- and a false refusal from a
    stale packaged copy is the case where the reader has to know which files answered.
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
        login="somebody-who-does-not-work-here",
    )
    first, blank, rest = out.split("\n", 2)

    assert code == EXIT_REFUSED
    assert first.startswith("checked against ") and blank == ""
    assert rest.startswith("1 refusal. Nothing was dispatched.")
    assert "refused  submitter_not_in_roster" in out


def test_a_workload_written_for_another_repository_names_the_ones_written_for_this_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two fields that must agree, and the refusal saying which to change.

    Mutation: refuse without listing this repository's own profiles. A workload profile
    fixes the runtime bound, the attempt bound and the checkpoint contract for one codebase,
    so the remedy is always "pick one of these" and the list is never long.
    """
    root, runner = checkout(tmp_path, workload="olmo-eval-check", compute="cpu-32vcpu")

    code, out, _ = invoke(
        ["check", "--dataset", "none", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  workload_profile_repository_mismatch" in out
    assert "olmo-core-train" in out


def test_an_experiment_that_cannot_group_is_refused_with_the_shape_that_can(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: accept anything non-empty.

    An experiment registers nothing, so the only thing a rule can buy here is that two
    people naming the same experiment get one group rather than two -- which is exactly
    what a capital letter or a trailing hyphen costs.
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "An Experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  experiment_not_a_slug" in out


def test_the_two_checks_a_laptop_cannot_make_are_named_rather_than_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honesty this verb rests on.

    Mutation: print "no refusals" and stop. ``adarsh-rajesh-first-run.md`` is a transcript
    of a submitter who read a clean preflight as a guarantee and met
    ``image_scan_findings_unreviewed`` from inside the submission -- and read it as a
    security problem in his own image rather than as a scan that had not finished.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, out, err = invoke(
        ["check", "--dataset", "none", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "not checked here, because both need the container registry" in out
    assert "no_published_image" in out
    assert "image_scan_findings_unreviewed" in out


@pytest.mark.parametrize("retired", ["dry-run", "new"])
def test_a_retired_name_is_refused_and_the_refusal_names_check(
    retired: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: make them aliases again.

    An alias makes two names work and teaches nobody which one is the name, so the retired
    spelling survives into the next guide somebody writes and the rename never finishes.
    Refusing costs one retry and ends it. What makes the refusal worth the retry is that it
    names the replacement, so nobody has to go and look the new name up.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, out, err = invoke([retired], runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert f"{retired} is not a verb. check is" in err
    assert out == ""
    # Nothing was judged, so nothing was read: no git, no gh, no configuration.
    assert runner.calls == []


@pytest.mark.parametrize("verb", ["new", "dry-run"])
def test_a_path_that_read_nothing_promises_nothing_about_this_directory(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: tailor the sentence to the directory again.

    An earlier version of this walked up looking for a spec and said "here, check would
    write a first .edullm/run.yaml -- there is none at or above here". It read well and it
    was a promise, and the binary broke it twice: in an unregistered checkout ``check``
    writes nothing and refuses, and outside a checkout it writes nothing either. Both are
    where somebody typing a retired verb is standing.

    The property being defended is that these paths judge nothing and therefore read
    nothing -- no git, no ``gh``, no configuration, not even the working directory -- and
    a sentence about *this* directory cannot be honest on that information. So the
    sentence describes ``check`` instead, and reads the same in a fresh directory and in a
    checkout that already has a spec.
    """
    empty = tmp_path / "fresh"
    empty.mkdir()
    runner = FakeRunner({})

    _, _, raw_without = invoke([verb], runner=runner, cwd=empty, monkeypatch=monkeypatch)
    root, spec_runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")
    _, _, raw_with = invoke([verb], runner=spec_runner, cwd=root, monkeypatch=monkeypatch)
    # Unwrapped, because where the paragraph breaks is a width and not a claim.
    without, with_spec = " ".join(raw_without.split()), " ".join(raw_with.split())

    assert without == with_spec
    assert "here" not in without and "write a first" not in without
    assert str(root) not in with_spec
    assert runner.calls == [] and spec_runner.calls == []


def test_the_orientation_describes_check_without_predicting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: promise a first spec to whoever runs a bare ``edullm``.

    The bare invocation is the likeliest first contact of all, and the directory it happens
    in is the likeliest to be unregistered -- somebody types ``edullm`` where they are
    standing. Naming the condition is the whole difference between a description and a
    promise: ``check`` writes a spec *where a registered repository has none*, which is
    true everywhere, including here.
    """
    runner = FakeRunner({})
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "_no-gh-config"))
    out, err = io.StringIO(), io.StringIO()

    # Called without ``invoke``, which puts a verb in front of every argv it is given, and
    # no verb at all is the thing being tested.
    code = main([], runner=runner, out=out, err=err, cwd=tmp_path)
    said = " ".join(err.getvalue().split())

    assert code == EXIT_UNUSABLE and out.getvalue() == ""
    assert "writes a first .edullm/run.yaml where a registered repository has none" in said
    assert "here it would" not in said.lower()
    assert runner.calls == []


def test_a_word_that_is_nobody_s_verb_gets_the_list_and_the_nearest_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo is a different fact from a rename and gets a different answer.

    Mutation: route typos through the rename table, or renames through the typo path. The
    first invents a replacement that was never settled; the second answers "the guide told
    me to type dry-run" with a menu to search.
    """
    runner = FakeRunner({})

    code, _, err = invoke(["stauts"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert "stauts is not a verb." in err
    assert "Did you mean status?" in err
    assert "check" in err and "cancel" in err
    assert runner.calls == []


def test_a_mistyped_flag_is_answered_with_the_flag_and_not_with_the_verbs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one mistake in this binary that used to get a menu instead of a sentence.

    Mutation: let argparse answer. It prints the root usage line, which lists nine verbs
    and not one flag -- the flags are all on the subparsers, so the message could not
    contain the answer even in principle. ``--experiement`` is one transposition from
    ``--experiment``, the same distance ``stauts`` is from ``status``, and the verb path
    has named the nearest spelling since the day it was written.

    The value after the flag comes back unrecognised too, and naming it would be a second
    wrong answer to a person who typed nothing wrong there.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, out, err = invoke(
        ["check", "--experiement", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNUSABLE
    assert "--experiement is not a flag check takes." in err
    assert "Did you mean --experiment?" in err
    assert "an-experiment is not" not in err
    assert "usage: edullm" not in err
    assert out == ""
    # Nothing was judged, so nothing was read.
    assert runner.calls == []


def test_a_flag_with_no_near_spelling_still_says_which_verb_takes_none_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No suggestion is better than a bad one, and silence is worse than both.

    ``get_close_matches`` at the cutoff the verbs use answers nothing for ``--wibble``,
    which is right -- guessing at a word with no near neighbour sends people to read a flag
    they never wanted. What is still owed is the sentence and where the list of flags is.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, _, err = invoke(["check", "--wibble"], runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert "--wibble is not a flag check takes." in err
    assert "Did you mean" not in err
    assert "edullm check --help" in err


def test_a_stray_word_is_not_called_a_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``check`` takes no positional argument, so a word is a different mistake.

    Mutation: run everything through the flag sentence. "pilot is not a flag check takes"
    is true and misleading: the person did not type a flag, and the fix is not to spell one
    correctly.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    code, _, err = invoke(["check", "pilot"], runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert "check was given a word it does not take: pilot." in err
    assert "is not a flag" not in err


def test_an_unbuilt_verb_says_so_before_it_says_anything_about_a_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two true things, and the order between them is the whole of this test.

    Mutation: check the flags first. A verb that is settled and unbuilt would be answered with
    the flags it takes, which is a conversation about a verb that does not exist yet.

    **THE UNBUILT NAME IS SUPPLIED RATHER THAN PICKED OFF THE TABLE, BECAUSE THE TABLE IS NOW
    EMPTY.** Every verb ``decisions.md`` settled is built as of the exploration route, and a
    version of this case that drove whichever name happened to be unbuilt would have quietly
    stopped running on the day the last one landed -- passing, and asserting nothing, which is
    the shape this repository has now found seven times. The property is about anything in
    ``NOT_BUILT_YET`` and not about a particular word, so a word is put there.
    """
    runner = FakeRunner({})
    monkeypatch.setitem(NOT_BUILT_YET, "teleport", "put you on the machine without asking")

    code, _, err = invoke(
        ["teleport", "--wibble"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNUSABLE
    assert "teleport is not built yet." in err
    assert "--wibble" not in err


def test_a_team_that_is_not_a_name_this_platform_can_group_on_is_refused_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap between two contracts, met by somebody capitalising a team name.

    Mutation: build the classification facts outside a guard. ``RunManifest.team`` accepts
    any non-empty string and ``RequestFacts.claimed_team`` is a slug, so ``--team "Pre
    Training"`` produced a valid manifest and then a pydantic traceback one line later --
    on a mistake that costs a shift key. Both contracts are right; what was wrong was that
    being held to one of them printed a stack trace.

    The refusal that names the declared teams comes first and is the one to act on. The
    second says the classification could not be derived, which is the honest consequence
    rather than a second opinion about the team.
    """
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        [
            "check",
            "--dataset",
            "regmix-10b-v1",
            "--experiment",
            "an-experiment",
            "--team",
            "Pre Training",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  unregistered_team" in out
    assert "refused  submission_cannot_be_priced" in out
    assert "claimed_team" in out
    assert err == ""


def test_a_runtime_bound_no_arithmetic_can_carry_is_refused_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same guard, and the reason it is a guard rather than a branch.

    Mutation: refuse an upper bound on ``--hours`` here instead. There is no ceiling this
    package may write down -- ``test_cli_no_hardcoded_bounds.py`` is the rule -- and there
    does not need to be one: ``CostInputs`` already refuses a worst case it cannot
    represent, and the only thing missing was somewhere for that refusal to be read.
    """
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        [
            "check",
            "--dataset",
            "regmix-10b-v1",
            "--experiment",
            "an-experiment",
            "--hours",
            "1e400",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "refused  submission_cannot_be_priced" in out
    assert err == ""


def test_a_contract_this_binary_breaks_anyway_is_reported_as_a_defect_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The net under the five contract models on this path, tested as a net.

    Mutation: remove the handler and rely on having found every site. Three were found by
    looking, all three on the first two minutes of a first user's day, and the argument for
    a net is that the fourth is found by somebody standing at a terminal. A pydantic
    traceback teaches a reader that the tool is broken, which is a more expensive belief
    than whichever defect produced it.

    Exit 2 rather than 1, and the message says whose fault it is: this is a submission
    nobody could judge rather than one that was declined, and a message that read as a
    refusal would send somebody to edit a spec that was fine.
    """
    from pydantic import BaseModel, Field

    from edullm_platform.cli import main as cli

    class Contract(BaseModel):
        name: str = Field(min_length=1)

    def refuse_everything(*_: object, **__: object) -> Preflight:
        return Contract(name="").model_copy()  # type: ignore[return-value]

    monkeypatch.setattr(cli, "run_preflight", refuse_everything)
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNUSABLE
    assert "defect in edullm rather than in what you typed" in err
    assert "name: String should have at least 1 character" in err
    assert "edu-llm/platform" in err
    assert "Traceback" not in err
    assert out == ""


def test_nothing_this_verb_does_reaches_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promise the verb is for: an answer in under a second, having asked nobody.

    Mutation: resolve the login over the API, or ask GitHub which image a commit published.
    Either turns a two-second refusal into a round trip, and the refusal it would buy is one
    the submission workflow makes anyway.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")

    invoke(
        ["check", "--dataset", "none", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert runner.ran("gh") == []
    assert runner.ran("aws") == []
    assert {argv[0] for argv in runner.calls} == {"git"}


# ---------------------------------------------------------------------------------------
# --help, and the one thing that could put an ANSI escape in it
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted({*BUILT_TODAY, *NOT_BUILT_YET}))
def test_every_verb_says_what_it_is_for_before_it_lists_its_flags(verb: str) -> None:
    """**The most-read page in the tool, and it answered the one question nobody asks.**

    Mutation: drop the ``description`` from one subparser. ``edullm check --help`` printed a
    usage line and fifteen flags and never said what ``check`` does, because the summary in
    ``BUILT_TODAY`` shows only in the parent listing and a subparser with no description has
    nothing to put above its options. Somebody who has already chosen the verb is past that
    listing, so the page they open is the page with the answer missing from it.

    Read off the parser rather than compared to a table here, so a tenth verb is covered on
    the day it is added and a description cannot be asserted against its own copy.
    """
    parser = build_parser_and_verbs()[1][verb]

    assert parser.description
    assert parser.description.strip().endswith(".")
    # Whitespace-normalised, because argparse wraps the description to the help page's
    # width and asserting the unwrapped form would be asserting the wrap width.
    assert " ".join(parser.description.split()) in " ".join(parser.format_help().split())


@pytest.mark.parametrize("verb", sorted({*BUILT_TODAY, *NOT_BUILT_YET}))
def test_no_verb_describes_a_flag_its_own_parser_does_not_take(verb: str) -> None:
    """**A help page is one page, and its two halves may not contradict each other.**

    ``edullm shell --help`` described "open an editor over SSH on a machine, with --notebook
    for Jupyter" and then listed ``--config-dir`` and ``--platform-repository``. ``shell`` is
    unbuilt, so the sentence was a plan rather than a lie -- and it was still wrong, because
    the options list under it is argparse's own answer to what may be typed and a reader has
    no way to tell which half of one page to believe.

    Mutation: describe a flag the verb does not take, on any verb. This is a rule about all
    nine rather than a fixture for the one that broke it, because the pressure that produced
    it is ordinary: the four unbuilt verbs carry a plan, and a plan is where a flag nobody
    has written gets named first.

    Both halves are read off the parser. The usage line is argparse's rendering of every
    option the verb has, which is the same surface :func:`_nearest_flag` reads for the same
    reason -- a hand-kept list of what each verb takes would be a second copy of the parser.
    """
    parser = build_parser_and_verbs()[1][verb]
    named = set(findall(r"--[a-z][a-z0-9-]*", parser.description or ""))
    taken = set(findall(r"--[a-z][a-z0-9-]*", parser.format_usage()))

    assert named <= taken, (
        f"edullm {verb} --help describes {sorted(named - taken)}, which its own options list "
        "does not carry"
    )


@pytest.mark.parametrize("retired", sorted(RETIRED))
def test_every_retired_name_still_carries_the_sentence_that_replaced_it(retired: str) -> None:
    """The retired names have no subparser, and this is what stands in for one.

    Mutation: leave one of them with a headline and no explanation. A name that was in a
    guide last month is exactly the name somebody types, and "that is not a verb" without
    the paragraph saying what absorbed it is the answer that sends them back to the guide.
    """
    replacement, headline, explanation = RETIRED[retired]

    assert headline.strip().endswith(".")
    assert len(explanation.split()) > 20
    assert replacement is None or replacement in {*BUILT_TODAY, *NOT_BUILT_YET}


def test_no_help_page_this_binary_prints_carries_an_ansi_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The property the whole tool rests on, against the one release that took it away.**

    Nothing in this package emits colour, so a piped run and a terminal run are the same
    bytes and ``NO_COLOR`` has nothing to switch off. Python 3.14 changed that from
    underneath: ``ArgumentParser`` gained ``color``, it defaults to true, and argparse
    colourises both ``--help`` and its own error messages whenever the stream can take it.
    ``requires-python`` is ``>=3.12`` and ``uv tool install`` fetches whatever is newest
    where nothing suitable exists, so some researchers would paste a coloured help page and
    some would not, from installs that call themselves the same version.

    ``PYTHON_COLORS=1`` is what makes this hold under pytest, where stdout is captured and
    ``can_colorize`` would answer no on its own. It is the strongest form available: the
    assertion below is that colour stays off where a reader asked for it, so it cannot pass
    by nobody having asked. On 3.12 and 3.13 argparse ignores the variable and the case is
    vacuous, which is why ``ci.yml`` runs 3.14.
    """
    monkeypatch.setenv("PYTHON_COLORS", "1")
    parser, verbs = build_parser_and_verbs()
    pages = [parser.format_help(), parser.format_usage()]
    pages += [built.format_help() for built in verbs.values()]
    pages += [built.format_usage() for built in verbs.values()]

    assert not [page for page in pages if "\x1b[" in page]


def test_that_check_can_see_a_colour_it_was_not_meant_to_see() -> None:
    """The tripwire's own tripwire, on the version that has anything to trip over.

    A test that colour is absent passes on a parser that could never produce it, which is
    every parser on 3.13 and would be every parser on 3.14 if the environment variable above
    stopped meaning anything. This builds a parser the ordinary way and asserts it does
    colourise, so the case above is known to be measuring something.
    """
    if sys.version_info < (3, 14):
        pytest.skip("argparse colourises nothing before 3.14")
    plain = argparse.ArgumentParser(prog="edullm", description="Submit and follow runs.")
    plain.add_argument("--experiment")

    with pytest.MonkeyPatch.context() as patched:
        patched.setenv("PYTHON_COLORS", "1")

        assert "\x1b[" in plain.format_help()
