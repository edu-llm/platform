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

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
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


def test_dry_run_is_the_same_verb_under_the_spelling_every_guide_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the alias.

    ``decisions.md`` settles ``check`` as the name and every transcript and guide written
    before that date types ``dry-run``. A researcher typing what they were taught should get
    the command rather than a usage error listing the verbs that happen to exist.
    """
    root, runner = checkout(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")
    arguments = ["--dataset", "none", "--experiment", "an-experiment"]

    checked = invoke(["check", *arguments], runner=runner, cwd=root, monkeypatch=monkeypatch)
    dry_run = invoke(["dry-run", *arguments], runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert dry_run == checked


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
