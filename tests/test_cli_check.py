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

from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE
from edullm_platform.cli.preflight import Preflight
from tests.cli_support import (
    SUBMITTER_ON_TWO_TEAMS,
    SUBMITTER_TEAM,
    FakeRunner,
    git_answers,
    invoke,
    write_spec,
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
    assert "$1.006/hour x 1 node x 24h x 2 attempts x 1 cell = $48.29" in out
    assert "routine -> run-approval-lead" in out
    assert f"team              {SUBMITTER_TEAM}" in out


def test_a_short_cheap_single_cell_run_is_told_nobody_releases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The automatic class, and the bounds printed as the policy file states them.

    Mutation: hard-code the four hours ``grant-matherne-scarce-shape-v2.md`` prints.
    ``decisions.md`` records the four-hour bound as *not ruled* and one hour as what the
    configuration says, so a CLI printing four would be telling a researcher a submission
    at ninety minutes releases itself when it goes to a lead.
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
    assert "automatic -- under $5 and under 1h. Nobody releases this." in out


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
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
        login="somebody-who-does-not-work-here",
    )

    assert code == EXIT_REFUSED
    assert out.startswith("1 refusal. Nothing was dispatched.")
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


def test_the_refusal_says_what_check_would_do_in_this_repository_rather_than_in_general(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print one fixed sentence about ``check`` in both states.

    "Type check instead" is a redirection. "Here, check would write a first spec and then
    price it" is the answer to what the person was trying to find out, and the difference
    costs one directory walk. The two states have to read differently or the sentence is
    decoration -- a repository with a spec is told the path that will be priced, and one
    without is told a file is about to appear.
    """
    empty = tmp_path / "fresh"
    empty.mkdir()
    runner = FakeRunner({})

    _, _, raw_without = invoke(["new"], runner=runner, cwd=empty, monkeypatch=monkeypatch)
    root, spec_runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")
    _, _, raw_with = invoke(["new"], runner=spec_runner, cwd=root, monkeypatch=monkeypatch)
    # Unwrapped, because where the paragraph breaks is a width and not a claim.
    without, with_spec = " ".join(raw_without.split()), " ".join(raw_with.split())

    assert "write a first .edullm/run.yaml -- there is none at or above here" in without
    assert f"price {root / '.edullm' / 'run.yaml'} and list every refusal" in with_spec
    assert "write a first" not in with_spec


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
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment", "--hours", "1e400"],
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
