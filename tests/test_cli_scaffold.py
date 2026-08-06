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

from edullm_platform.cli.intake import SELF_SERVICE_KINDS
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE
from edullm_platform.cli.spec import load_spec
from edullm_platform.cli.workspace import CommandResult
from tests.cli_support import (
    CONFIG_DIR,
    FakeRunner,
    failed,
    git_answers,
    invoke,
    ok,
    write_spec,
)

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


def answers_from_a_clone(root: Path, *, committed: frozenset[str]) -> Answers:
    """``git status`` where ``.edullm/`` is tracked, so a new file in it is named on its own.

    This is the shape of the clone the guide sends a researcher to. ``OLMo-core`` carries a
    committed ``.edullm/`` holding a ``Dockerfile`` and an entry point, so the directory is
    not new and git names ``.edullm/run.yaml`` on its own rather than collapsing to the
    directory above it. Anything under the tree that ``committed`` does not name is reported
    untracked, which is what makes the file the scaffold writes visible here.
    """
    def status(_: tuple[str, ...]) -> CommandResult:
        found = sorted(path for path in root.rglob("*") if path.is_file())
        return ok(
            "".join(
                f"?? {relative}\n"
                for path in found
                if (relative := path.relative_to(root).as_posix()) not in committed
                and not relative.startswith(("_tools/", "_no-"))
            )
        )

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
    that does the work. This is the moment somebody decides whether this platform is for
    them, so it opens with the command that registers the repository, names the repository
    it would register, and lists the ones already registered.

    **AND THE COMMAND IT NAMES IS ``edullm add repository``, WHICH IT DID NOT USED TO BE.**
    This refusal sent people to open an issue and said ``edullm add`` was "not built yet".
    It is built, ``repository`` is the one kind in ``SELF_SERVICE_KINDS``, and
    ``register-repository.yml`` edits the five platform files and opens the pull request. So
    the sentence was sending the one reader who could have self-served into a queue.
    """
    runner = FakeRunner(git_answers(tmp_path, repository="platform"))

    code, out, err = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    said = " ".join(out.split())

    assert code == EXIT_REFUSED
    assert "refused  unregistered_repository" in out
    assert said.count("edullm add repository") == 1
    assert "to register 'platform'" in said
    assert "config/repositories.yaml carries no entry for it" in said
    assert "Registered today: OLMo-core" in said
    # The verb it names is real and self-service, which is what the old wording denied.
    assert "repository" in SELF_SERVICE_KINDS
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

    It did, and the two runs then disagreed about one repository thirty seconds apart. A
    verdict that changes under somebody with nothing else changing is the one kind of wrong
    answer a checking tool cannot afford: they have no way to know which reading to trust.

    So the facts are read again after the write, and the two runs are asserted to end
    identically rather than merely to agree about one refusal. The ``wrote`` line is the
    whole of the difference between them.

    **WHICH REFUSAL THEY AGREE ON MOVED, AND THAT IS THE OTHER HALF OF THE PROPERTY.** It
    was ``uncommitted_changes`` naming the file the first run had just written, which was
    consistent and was a loop with no way out of it. It is now the scaffold's own default
    command failing the checkpoint rule, which is a thing a person can fix. Asserting the
    two runs are identical is what holds both fixes together: excluding the spec on the
    invocation that wrote it and nowhere else would pass every other case here and fail
    this one.
    """
    runner = FakeRunner(answers_reading_the_tree(tmp_path))

    first_code, first, _ = invoke(
        FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    second_code, second, _ = invoke(
        FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert first_code == second_code == EXIT_REFUSED
    assert "refused  checkpoint_path_not_in_command" in first
    assert "uncommitted_changes" not in first + second
    assert first.endswith(second)
    assert "wrote " in first and "wrote " not in second


def test_the_spec_check_wrote_is_not_a_change_the_researcher_left_uncommitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE LOOP, WHICH IS WHAT THE GUIDE'S OWN FIRST COMMAND DID IN A FRESH CLONE.

    ``guides/the-platform.md`` sends a researcher to a checkout of ``OLMo-core`` and tells
    them to type one command. That clone has a committed ``.edullm/Dockerfile`` and no
    ``run.yaml``, so ``check`` wrote one and then refused ``uncommitted_changes`` naming the
    file it had just written. Both remedies the refusal offers fail: ``git stash -u`` deletes
    the file, the next ``check`` writes it back, and the identical refusal prints again.
    Measured against the real binary in ``/tmp/olmo-core-rt`` on 2026-08-06, byte for byte.

    Mutation: count an untracked spec towards ``uncommitted_changes`` again. That is the
    shipped behaviour and this is the case that catches it. The stash arm is asserted as
    well as the first run, because a fix that only quietened the invocation that wrote the
    file would leave the loop turning one command further along.

    The three verdicts are compared to each other rather than only to ``EXIT_OK``, which is
    what stops the opposite mutation: an exclusion that keys off "this invocation wrote it"
    makes ``check`` and ``check`` again disagree about one unchanged tree, and a verdict that
    flips under a researcher is the failure the whole re-read above this exists to prevent.
    """
    (tmp_path / ".edullm").mkdir()
    (tmp_path / ".edullm" / "train_on_corpus.py").write_text("print(1)\n", encoding="utf-8")
    runner = FakeRunner(
        answers_from_a_clone(tmp_path, committed=frozenset({".edullm/train_on_corpus.py"}))
    )
    spec_path = tmp_path / ".edullm" / "run.yaml"

    wrote_it, first, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    again, second, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    spec_path.unlink()
    after_a_stash, third, _ = invoke(
        FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert wrote_it == again == after_a_stash == EXIT_OK, first + second + third
    assert "uncommitted_changes" not in first + second + third
    assert first.startswith("wrote ") and third.startswith("wrote ")
    # The one that wrote nothing says the same thing about the same tree as the two that
    # did, minus the line naming the file. Compared whole, because a fix that agreed on the
    # exit code and disagreed on the cost block would be the same defect one layer down.
    assert first.endswith(second) and third.endswith(second)


def test_a_spec_that_is_in_the_repository_and_has_been_edited_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary the exclusion above must not cross.

    A spec that is committed is the repository's recorded recipe, and an edit to it that is
    in no commit is a real uncommitted input: the next person to check out this commit gets
    the recipe without the edit. Git already tells the two apart -- ``??`` is a file nobody
    has ever committed, `` M`` is a change to one that is in the repository -- so the
    exclusion is keyed on that rather than on the path.

    Mutation: exclude ``.edullm/run.yaml`` by name whatever git says about it. The loop test
    above passes either way, which is exactly why this one is written: the cheap fix and the
    correct one are indistinguishable from the fresh-clone case alone.
    """
    write_spec(tmp_path)
    runner = FakeRunner(git_answers(tmp_path, dirty=[".edullm/run.yaml"]))

    code, out, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "refused  uncommitted_changes" in out
    assert ".edullm/run.yaml" in out


def test_an_untracked_directory_holding_more_than_the_spec_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collapsed entry, which is the one place the exclusion could hide somebody's work.

    ``git status`` reports a wholly untracked directory as one line naming the directory, so
    a repository whose ``.edullm/`` is new is reported as ``.edullm/`` however many files are
    under it. Dropping that entry because the spec is inside it would take an uncommitted
    ``Dockerfile`` with it -- and the image is built from the commit, so a Dockerfile that is
    in no commit is the difference between a build and a refusal from the registry.

    Mutation: drop any dirty entry that is a parent of the spec. It passes the loop test,
    because there the only thing under ``.edullm/`` is the spec, and it silently stops
    reporting the file that decides whether an image can be built at all.
    """
    (tmp_path / ".edullm").mkdir()
    (tmp_path / ".edullm" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    runner = FakeRunner(answers_reading_the_tree(tmp_path))

    code, out, _ = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED, out
    assert "refused  uncommitted_changes" in out
    assert ".edullm/" in out


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


def test_a_spec_that_is_not_one_is_named_field_by_field_and_not_by_pydantic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file this scaffold writes is a file people edit, so this is a normal Tuesday.

    Mutation: interpolate the ``ValidationError``. Pydantic's own rendering of two bad
    fields is eleven lines carrying the model name twice, each value echoed as a Python
    repr, and two links to ``errors.pydantic.dev`` -- and the reader who meets it mistyped
    something in a YAML file. A link to a library's error index answers a question nobody
    standing in a repository is asking.

    Both fields, and not only the first: they are about to open the file, and the second
    problem is worth knowing before they close it.
    """
    (tmp_path / ".edullm").mkdir()
    (tmp_path / ".edullm" / "run.yaml").write_text(
        "schema_version: 1\nworkload_profile: []\n", encoding="utf-8"
    )
    runner = FakeRunner(git_answers(tmp_path))

    code, _, err = invoke(FIRST_CHECK, runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert "is not a run spec this platform can read" in err
    assert "workload_profile: Input should be a valid string" in err
    assert "command: Field required" in err
    assert "errors.pydantic.dev" not in err
    assert "RunSpec" not in err
