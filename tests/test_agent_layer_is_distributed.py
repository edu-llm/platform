"""The half of the agent layer that is copies, and the thing that keeps the copies one text.

**WHY THERE ARE COPIES AT ALL, SINCE COPIES ARE THE PROBLEM.** No host reads a skill out of a
repository the user does not have open. The two skills and the always-on rule lived only in
this repository until 2026-08-06, which meant they loaded for somebody working on the platform
and for nobody working in OLMo-core, and the failure that produces is an agent writing
``boto3`` against a cluster it cannot reach. There is no arrangement where a researcher's
agent reads a file that is not in the researcher's repository. So: copies, and then something
that holds them equal, because a copy nothing compares is how a document comes to say two
things.

**WHAT THIS FILE DOES AND WHAT IT DELIBERATELY DOES NOT.** Everything here is hermetic. It
holds the distributor's mechanics: that a write is idempotent, that a second run over an
edited copy restores it, that a repository's own prose survives, that drift is detected rather
than passed over. It reaches no network and names no research repository.

The other half cannot be hermetic, because the question "does OLMo-core's copy still match" is
a question about OLMo-core. ``.github/workflows/agent-layer-is-distributed.yml`` asks it, of
every repository in ``config/repositories.yaml``, on a schedule and on every push to ``main``.
Splitting it this way is the point rather than a compromise: the mechanics go red in the pull
request that breaks them, and the copies go red within the day, and neither waits on the
other. A single networked test would have made the fast half as flaky as the slow half.

**THE CASE THAT MATTERS MOST HERE IS THE LAST ONE.** A distributor whose ``--check`` cannot
fail is worse than no distributor, because the workflow built on it reports green forever.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.distribute_agent_layer import (
    BEGIN_MARKER,
    CLAUDE_IMPORT,
    END_MARKER,
    SKILL_NAMES,
    divergences,
    expected_skill_text,
    registered_repositories,
    write_into,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """An empty git checkout, since the distributor refuses anything else."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_a_written_checkout_has_nothing_left_diverging(checkout: Path) -> None:
    """The two halves agree, which is what lets the workflow trust ``--check``.

    Mutation: write ``.agents/skills`` and check ``.agent/skills``. Both halves would be
    individually reasonable and the pair would report drift on every repository forever,
    which gets a workflow disabled rather than a path fixed.
    """
    write_into(checkout)

    assert divergences(checkout) == []


def test_writing_twice_changes_nothing_the_second_time(checkout: Path) -> None:
    """Mutation: rewrite the file unconditionally.

    This runs over every registered repository whenever the source moves, and a distributor
    that rewrites unchanged bytes produces a pull request touching every repository with no
    diff in it. Those get merged without being read, which is the habit that lets a real one
    through later.
    """
    write_into(checkout)

    assert write_into(checkout) == []


def test_a_repositorys_own_prose_survives_the_rule_being_written(checkout: Path) -> None:
    """**Mutation: write ``AGENTS.md`` whole rather than splicing a marked region.**

    THIS IS THE ONE THAT WOULD DO REAL DAMAGE. ``AGENTS.md`` in a research repository is
    mostly that repository's own: how to run its tests, which of its entry points is the real
    one, what its layout means. OLMo-core's says its trainer sets bfloat16 in code where the
    platform's guard cannot see it, which is knowledge no platform file has. A distributor
    that overwrote the file would delete that on its first run, in six repositories, in a
    pull request whose title says it is adding documentation.
    """
    checkout.joinpath("AGENTS.md").write_text("# Ours\n\nRun the tests with `pytest -q`.\n")

    write_into(checkout)
    text = checkout.joinpath("AGENTS.md").read_text()

    assert "Run the tests with `pytest -q`." in text
    assert BEGIN_MARKER in text and END_MARKER in text


def test_an_edited_copy_is_reported_and_then_restored(checkout: Path) -> None:
    """Mutation: make ``--check`` compare file existence rather than file content.

    A copy that exists and says something else is the state this whole file is against, and
    it is invisible to any check that asks whether a path is there. It is also the likely
    one: somebody fixes a typo in the repository they are working in, which is the reasonable
    thing to do and the thing that forks the text.
    """
    write_into(checkout)
    skill = checkout / ".agents" / "skills" / SKILL_NAMES[0] / "SKILL.md"
    skill.write_text(skill.read_text().replace("Never", "Rarely"))

    found = divergences(checkout)
    assert [entry.path for entry in found] == [str(skill.relative_to(checkout))]
    assert "Rarely" in found[0].detail

    write_into(checkout)
    assert divergences(checkout) == []
    assert skill.read_text() == expected_skill_text(SKILL_NAMES[0])


def test_a_missing_claude_symlink_is_drift_even_when_the_skill_is_there(
    checkout: Path,
) -> None:
    """Mutation: distribute ``.agents/skills`` and call the job done.

    Codex and Cursor would both be fine and Claude Code would have nothing, in a repository
    whose agent layer looks present in every listing. That is a third of the organization,
    and the symptom is indistinguishable from an agent that read the skill and ignored it.
    """
    write_into(checkout)
    link = checkout / ".claude" / "skills" / SKILL_NAMES[0]
    link.unlink()

    assert [entry.path for entry in divergences(checkout)] == [
        str(link.relative_to(checkout))
    ]


def test_the_claude_path_is_a_link_and_not_a_second_copy(checkout: Path) -> None:
    """Mutation: copy the file into both directories.

    Two real files are two texts, and the argument that they will be written together is the
    argument every forked document was born under. The symlink makes them one file, so there
    is no state in which they disagree and nothing has to be diligent.
    """
    write_into(checkout)

    for name in SKILL_NAMES:
        link = checkout / ".claude" / "skills" / name
        assert link.is_symlink()
        assert (link / "SKILL.md").resolve() == (
            checkout / ".agents" / "skills" / name / "SKILL.md"
        )


def test_a_repository_that_already_had_a_claude_file_gets_the_rule_imported_into_it(
    checkout: Path,
) -> None:
    """**Mutation: check that ``CLAUDE.md`` exists and go no further.**

    FOUR OF THE SIX REGISTERED REPOSITORIES ALREADY HAD ONE, written before any of this and
    saying nothing about the platform. Under the weaker check the rule would go into
    ``AGENTS.md``, Claude Code would go on reading a file that never mentions ``edullm``, and
    every report would say the layer was installed. That is the layer's characteristic
    failure -- an agent with no rule is indistinguishable from an agent that read one -- and
    it would have landed in the repositories most likely to be worked in.
    """
    theirs = "# Ours\n\nThe trainer sets bfloat16 in code, which the guard cannot see.\n"
    checkout.joinpath("CLAUDE.md").write_text(theirs)

    write_into(checkout)
    text = checkout.joinpath("CLAUDE.md").read_text()

    assert CLAUDE_IMPORT in text
    assert theirs in text, "the repository's own guidance was overwritten rather than added to"
    assert divergences(checkout) == []


def test_a_claude_file_that_never_reaches_the_rule_is_drift(checkout: Path) -> None:
    """The reporting half of the case above, which the workflow is what reads.

    Mutation: have somebody rewrite their ``CLAUDE.md`` and drop the import while doing it.
    Nothing else in the layer changes, every file is still present, and one host quietly
    stops seeing any of it.
    """
    write_into(checkout)
    claude = checkout / "CLAUDE.md"
    claude.write_text(claude.read_text().replace(CLAUDE_IMPORT, "Read AGENTS.md yourself"))

    found = divergences(checkout)

    assert [entry.path for entry in found] == ["CLAUDE.md"]
    assert "never reaches the rule" in found[0].detail


def test_an_untouched_checkout_diverges_on_everything(checkout: Path) -> None:
    """Guards every case above, all of which write before they assert.

    Mutation: have ``divergences`` return ``[]`` unconditionally. Each case above would pass,
    the workflow would report every repository current, and the layer could rot out of all
    six without a single red run. A check that cannot fail is the failure this file is here
    to make impossible.
    """
    found = divergences(checkout)
    reported = {entry.path for entry in found}

    assert "AGENTS.md" in reported
    assert "CLAUDE.md" in reported
    for name in SKILL_NAMES:
        assert f".agents/skills/{name}/SKILL.md" in reported
        assert f".claude/skills/{name}" in reported


def test_the_repositories_written_to_are_read_off_the_reviewed_configuration() -> None:
    """Mutation: hard-code the six that were registered the day this was written.

    A seventh is registered by merging a change to ``config/repositories.yaml``, and nothing
    about that merge would touch a list kept here. The repository would be registered,
    buildable and submittable with no agent layer and nothing red, which is precisely the
    state all six were in before this.
    """
    from tools.distribute_agent_layer import REPOSITORIES_YAML

    registered = registered_repositories()

    assert registered, "no repository was read out of the reviewed configuration"
    assert set(registered) <= set(REPOSITORIES_YAML.read_text().split())
    assert "distribute_agent_layer" not in REPOSITORIES_YAML.read_text(), (
        "the configuration has been made to know about the distributor, which is the "
        "dependency the wrong way round"
    )
