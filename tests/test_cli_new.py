"""``edullm new``, and ``check`` absorbing it.

What a scaffold is worth is entirely in whether the file it writes is one the checks then
clear, so most of what is asserted here is that: write it, check it, and expect no refusal
about the fields the scaffold chose. A scaffold that produced a plausible file the platform
refuses would be worse than no scaffold, because it puts the mistake in version control
under somebody else's name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.cli.spec import load_spec
from tests.cli_support import CONFIG_DIR, FakeRunner, failed, git_answers, invoke


def test_a_first_spec_names_a_workload_this_repository_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: write a workload profile from anywhere but the catalog.

    A profile belongs to one repository and brings that repository's bounds with it, so a
    scaffold naming somebody else's is a file whose first check refuses it -- and whose
    author has no reason to think the tool got it wrong rather than themselves.
    """
    runner = FakeRunner(git_answers(tmp_path))

    code, out, err = invoke(["new"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
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
    ``check`` clears what ``new`` wrote.
    """
    (tmp_path / ".edullm").mkdir()
    (tmp_path / ".edullm" / "train_on_corpus.py").write_text("print(1)\n", encoding="utf-8")
    runner = FakeRunner(git_answers(tmp_path))

    invoke(["new"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=FakeRunner(git_answers(tmp_path)),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "no refusals. edullm submit will dispatch this." in out
    assert err == ""


def test_a_spec_that_is_already_there_is_not_replaced_without_being_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: overwrite.

    The file is edited by hand after it is written -- that is what it is for -- so the
    second ``new`` in a repository is somebody who has forgotten they ran the first, and
    what it would cost them is a command they tuned over a week.
    """
    runner = FakeRunner(git_answers(tmp_path))
    invoke(["new"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    written = (tmp_path / ".edullm" / "run.yaml").read_text(encoding="utf-8")
    (tmp_path / ".edullm" / "run.yaml").write_text(
        written.replace("schema_version: 1", "schema_version: 1\n# mine"), encoding="utf-8"
    )

    code, _, err = invoke(["new"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "--force" in err
    assert "# mine" in (tmp_path / ".edullm" / "run.yaml").read_text(encoding="utf-8")


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

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

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

    code, _, err = invoke(["new"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "refused  not_a_repository" in err
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
        ["new", "--compute", "gpu-4xa10g"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    spec = load_spec(tmp_path / ".edullm" / "run.yaml")
    assert "--nproc-per-node=4" in spec.command
    assert '"$EDULLM_CHECKPOINT_DIR"' in spec.command
    assert spec.argv[0] == "bash"


def test_the_header_names_the_configuration_the_choices_were_read_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the comments.

    The file outlives the invocation and the person editing it in a fortnight is the one who
    needs to know which alternatives existed. Printing that to a terminal once reaches
    whoever ran the command and nobody else.
    """
    runner = FakeRunner(git_answers(tmp_path))

    invoke(["new"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    written = (tmp_path / ".edullm" / "run.yaml").read_text(encoding="utf-8")
    assert str(CONFIG_DIR) in written
    assert "olmo-core-check, olmo-core-train" in written
