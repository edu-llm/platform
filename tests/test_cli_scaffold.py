"""``check`` scaffolding, which is the only way a spec gets written.

What a scaffold is worth is entirely in whether the file it writes is one the checks then
clear, so most of what is asserted here is that: write it, check it, and expect no refusal
about the fields the scaffold chose. A scaffold that produced a plausible file the platform
refuses would be worse than no scaffold, because it puts the mistake in version control
under somebody else's name.

There is no ``new``. ``decisions.md`` folds it into ``check`` and the fold is the point
rather than a convenience: the first command a newcomer types is also the one that gets them
a file, and it prices the file in the same breath, so nobody learns their corpus is
unregistered on a second command they had to be told to run. ``check`` never replaces a spec
it did not just write, which is what makes writing safe enough to do without asking.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.cli.spec import load_spec
from edullm_platform.cli.workspace import CommandResult
from tests.cli_support import CONFIG_DIR, FakeRunner, failed, git_answers, invoke, ok

FIRST_CHECK = ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"]

Answers = dict[tuple[str, ...], CommandResult | Callable[[tuple[str, ...]], CommandResult]]


def answers_reading_the_tree(root: Path) -> Answers:
    """``git status`` answered from the filesystem, which is what makes a scaffold visible.

    Every other test here can supply a fixed answer because nothing it does changes the
    tree. The scaffold does, so an answer recorded before the file was written is exactly
    the bug two of the tests below are about, and a fake carrying one could not fail them.

    The untracked entry is the directory rather than the file, which is what real git
    answers when the whole of ``.edullm/`` is new -- and it is the case a reader is least
    able to connect to the path the ``wrote`` line names.
    """
    def status(_: tuple[str, ...]) -> CommandResult:
        return ok("?? .edullm/\n" if (root / ".edullm").exists() else "")

    return {**git_answers(root), ("git", "status", "--porcelain"): status}


def test_a_first_spec_names_a_workload_this_repository_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: write a workload profile from anywhere but the catalog.

    A profile belongs to one repository and brings that repository's bounds with it, so a
    scaffold naming somebody else's is a file whose first check refuses it -- and whose
    author has no reason to think the tool got it wrong rather than themselves.
    """
    runner = FakeRunner(git_answers(tmp_path))

    invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    spec = load_spec(tmp_path / ".edullm" / "run.yaml")
    assert spec.workload_profile in {"olmo-core-check", "olmo-core-train"}
    assert spec.suggested_compute is not None


def test_the_scaffolded_command_satisfies_the_rules_the_check_then_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property worth having, asserted end to end rather than field by field.

    Mutation: scaffold a command that names no ``$EDULLM_CHECKPOINT_DIR`` under a workload
    that checkpoints, or one that starts a single process on a multi-device suggestion.
    Both are refused by rules this repository already owns, so the assertion is simply that
    the check clears the file the same invocation just wrote.
    """
    (tmp_path / ".edullm").mkdir()
    (tmp_path / ".edullm" / "train_on_corpus.py").write_text("print(1)\n", encoding="utf-8")
    runner = FakeRunner(git_answers(tmp_path))

    code, out, err = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert out.startswith("wrote ")
    assert "no refusals. edullm submit will dispatch this." in out
    assert err == ""


def test_a_spec_that_is_already_there_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: scaffold whenever the fields would differ, or offer a ``--force``.

    The file is edited by hand after it is written -- that is what it is for -- so every
    ``check`` after the first is somebody whose command is theirs now. There is no flag to
    replace it because there is no request behind one: a spec that should go is deleted, and
    the next ``check`` writes a fresh one, which is one concept rather than two.
    """
    runner = FakeRunner(git_answers(tmp_path))
    invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    spec_path = tmp_path / ".edullm" / "run.yaml"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace(
            "schema_version: 1", "schema_version: 1\n# mine"
        ),
        encoding="utf-8",
    )

    _, out, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert "wrote " not in out
    assert "# mine" in spec_path.read_text(encoding="utf-8")


def test_check_writes_a_first_spec_where_there_is_none_and_then_checks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``check`` absorbing ``new``, which is what ``decisions.md`` settles it does.

    Mutation: refuse with "there is no spec". Every transcript runs the two commands back to
    back, and the second of them is the one that says anything useful; making a researcher
    type both to learn that their corpus is unusable is the discoverability problem this
    verb exists to close.
    """
    runner = FakeRunner(git_answers(tmp_path))

    code, out, err = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert (tmp_path / ".edullm" / "run.yaml").is_file()
    assert out.startswith("wrote ")
    assert "no_run_spec" not in out
    # It went on to check the file it wrote rather than stopping, which is the whole of what
    # "absorbs" means here: no entry point is discoverable in an empty tree, so the command
    # is the form's own default and the checkpoint rule refuses it by name.
    assert code == EXIT_REFUSED
    assert "refused  checkpoint_path_not_in_command" in out
    assert err == ""


def test_a_directory_that_is_not_a_checkout_is_told_that_rather_than_scaffolded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: write the file anyway.

    A run is a commit in a registered repository. Scaffolding into a directory that is not
    one produces a file whose repository cannot be determined, and the refusal that follows
    is about a field the scaffold invented rather than about where the person is standing.
    """
    runner = FakeRunner({("git", "rev-parse", "--show-toplevel"): failed("not a git repo")})

    code, out, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "refused  not_a_repository" in out
    assert not (tmp_path / ".edullm").exists()


def test_the_written_file_reads_back_as_the_spec_it_was_rendered_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folded command survives a round trip, which is the one risky part of rendering.

    Mutation: fold at a fixed column rather than at a space. A block scalar rejoins its
    lines with single spaces, so a break inside a quoted word comes back as a different
    string -- and the way that surfaces is a container exec'ing a path with a space in it.
    """
    (tmp_path / ".edullm").mkdir()
    (tmp_path / ".edullm" / "train_a_very_long_experiment_name_on_a_corpus.py").write_text(
        "print(1)\n", encoding="utf-8"
    )
    runner = FakeRunner(git_answers(tmp_path))

    invoke(
        [*FIRST_CHECK, "--compute", "gpu-4xa10g"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    spec = load_spec(tmp_path / ".edullm" / "run.yaml")
    assert "--nproc-per-node=4" in spec.command
    assert '"$EDULLM_CHECKPOINT_DIR"' in spec.command
    assert spec.argv[0] == "bash"


def test_a_repository_nothing_registers_is_refused_rather_than_raising_out_of_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first command a newcomer runs where they happen to be standing.

    Mutation: scaffold anyway, and let ``RunSpec`` be the thing that says no. It did: the
    catalog names no workload for an unregistered repository, so ``_pick_workload``
    answered ``None``, the spec was built with an empty ``workload_profile``, and pydantic's
    ``min_length=1`` came out of ``main`` as a traceback ending in "String should have at
    least 1 character". The documented path never met it because researchers work in
    registered repositories; the exploratory path -- running it in the platform checkout, or
    in whatever is open -- met it every time.

    A refusal instead, in the vocabulary the other sixteen use, and it has to be the one
    that does the work: this is the moment somebody decides whether this platform is for
    them, so it names the repository, says what a registration is, and points at the ask
    rather than at a pull request nobody outside the platform can reasonably open.
    """
    runner = FakeRunner(git_answers(tmp_path, repository="platform"))

    code, out, err = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    said = " ".join(out.split())

    assert code == EXIT_REFUSED
    assert "refused  unregistered_repository" in out
    assert "'platform' is not a repository config/repositories.yaml carries" in said
    assert "It is not a change to make yourself" in said
    assert "opening an issue on edu-llm/platform" in said
    assert "Registered today: OLMo-core" in said
    # Nothing was written, and the generic refusal is not printed beside the specific one:
    # both answer "why is there no spec", and two spellings of one problem send a reader
    # looking for a second problem.
    assert not (tmp_path / ".edullm").exists()
    assert "no_run_spec" not in out
    assert err == ""


def test_a_workload_without_a_registration_still_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dolma`` is the shape this rules out, and it is in the catalog today.

    Mutation: ask the catalog before the registry, or ask only the catalog. ``dolma`` has a
    workload profile and no registration -- ``test_submission_form_options.py`` asserts that
    pairing deliberately -- so a scaffold that looked only for a workload would write a spec
    into a checkout that can never publish an image, and the refusal that followed would be
    about a file the tool had just created.

    The registry is asked first for the same reason ``run_preflight`` asks it first: a
    refusal naming a workload profile when the real problem is the registration points at a
    field that was never what stood in the way.
    """
    runner = FakeRunner(git_answers(tmp_path, repository="dolma"))

    code, out, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "refused  unregistered_repository" in out
    assert "dolma-tokenize" not in out
    assert not (tmp_path / ".edullm").exists()


@pytest.mark.parametrize("flag", ["--workload", "--compute"])
def test_an_empty_override_reads_as_absent_rather_than_as_a_profile_named_nothing(
    flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: pass the empty string through to the catalog lookup.

    ``--workload ""`` found no profile named "", the scaffold wrote that empty name into
    the spec, and ``RunSpec`` raised out of ``main`` -- the same traceback an unregistered
    repository produced, by a different route. Everywhere else in the CLI an empty override
    already reads as absent, ``arguments.workload or spec.workload_profile`` among them, so
    this is the reading that was already the rule.
    """
    runner = FakeRunner(git_answers(tmp_path))

    code, out, err = invoke(
        [*FIRST_CHECK, flag, ""], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_REFUSED, out + err
    spec = load_spec(tmp_path / ".edullm" / "run.yaml")
    assert spec.workload_profile
    assert spec.suggested_compute


def test_the_check_that_writes_a_spec_and_the_one_after_it_tell_the_same_story(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: read the working tree before the scaffold rather than after it.

    It did, and the two runs then disagreed about one repository thirty seconds apart: the
    first said "no refusals" having just created an uncommitted ``.edullm/run.yaml``, and
    the second refused with ``uncommitted_changes`` naming that file. The second was right.
    The first was reporting a tree that had stopped existing halfway through its own
    invocation, which is the one kind of wrong answer a checking tool cannot afford --
    somebody who sees a verdict change under them has no way to know which reading to trust.

    So the facts are read again after the write, and the two runs are asserted to end
    identically rather than merely to agree about one refusal. The ``wrote`` line carries
    the difference between them, and it is the sentence that connects the file to the
    refusal underneath it.
    """
    runner = FakeRunner(answers_reading_the_tree(tmp_path))

    first_code, first, _ = invoke(
        FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    second_code, second, _ = invoke(
        FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert first_code == second_code == EXIT_REFUSED
    assert "refused  uncommitted_changes" in first
    assert first.endswith(second)
    assert "wrote " in first and "wrote " not in second
    assert "uncommitted_changes refusal below is naming" in " ".join(first.split())


def test_the_scaffold_line_says_nothing_about_a_refusal_that_is_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the sentence unconditionally.

    A repository that ignores ``.edullm/`` -- or any tree git does not report the new file
    in -- gets no ``uncommitted_changes`` refusal, and a line promising one below it would
    send a reader looking for something that is not printed. The sentence is read from the
    same facts the refusal is, so the two cannot come apart.
    """
    runner = FakeRunner(git_answers(tmp_path))

    _, out, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert out.startswith("wrote ")
    assert "uncommitted_changes" not in out


def test_the_header_names_the_configuration_the_choices_were_read_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the comments.

    The file outlives the invocation and the person editing it in a fortnight is the one who
    needs to know which alternatives existed. Printing that to a terminal once reaches
    whoever ran the command and nobody else.
    """
    runner = FakeRunner(git_answers(tmp_path))

    invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    written = (tmp_path / ".edullm" / "run.yaml").read_text(encoding="utf-8")
    assert str(CONFIG_DIR) in written
    assert "olmo-core-check, olmo-core-train" in written
