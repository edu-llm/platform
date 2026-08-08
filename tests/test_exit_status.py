"""Whether a submission whose shell discards the trainer's status is refused.

THE MEASUREMENTS THIS MODULE RESTS ON ARE IN :mod:`edullm_platform.exit_status`, and the one
worth repeating is that ``pipefail`` covers a third of the rule. ``bash -lc 'false | tail'``
is 0 and ``bash -lc 'set -o pipefail; false | tail'`` is 28, so the option repairs the
pipeline; ``bash -lc 'false; echo done'`` is 0 with the option set and 0 without it, so no
shell option repairs the sequence. A test suite that only exercised the pipe would let the
guard be replaced by a wrapper that sets one flag.

:func:`test_no_recorded_command_is_refused` is the one that decides whether this rule could
be added at all. Every other guard over the text of a command records the same constraint --
a rule that retroactively refuses a manifest whose canonical digest is a recorded golden is a
rule that cannot ship -- so the fixtures are read and the count is asserted rather than the
emptiness alone, because a glob that stopped matching would pass an empty loop.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.errors import ExitStatusIsNotTheProgramsError
from edullm_platform.exit_status import (
    EXIT_STATUS_CHECK_WAIVER,
    require_the_program_to_report_its_own_failure,
    status_the_shell_would_report,
    waived_exit_status_note,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The line the guide prints, which every real submission is a variation of.
A_REAL_COMMAND = (
    """bash -lc 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" """
    """--save-folder "$EDULLM_CHECKPOINT_DIR" --steps 4000'"""
)


def allow(line: str) -> None:
    require_the_program_to_report_its_own_failure(tuple(shlex.split(line)))


def refuse(line: str) -> ExitStatusIsNotTheProgramsError:
    with pytest.raises(ExitStatusIsNotTheProgramsError) as raised:
        require_the_program_to_report_its_own_failure(tuple(shlex.split(line)))
    return raised.value


# ---------------------------------------------------------------------------------------
# What a shell actually does, measured rather than asserted from memory
# ---------------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(
    ("script", "status"),
    [
        ("python3 -c 'import sys; sys.exit(28)' 2>&1 | tail -n 200", 0),
        ("set -o pipefail; python3 -c 'import sys; sys.exit(28)' 2>&1 | tail -n 200", 28),
        ("python3 -c 'import sys; sys.exit(28)'; echo done", 0),
        ("set -o pipefail; python3 -c 'import sys; sys.exit(28)'; echo done", 0),
        ("python3 -c 'import sys; sys.exit(28)' || true", 0),
        ("set -o pipefail; python3 -c 'import sys; sys.exit(28)' || true", 0),
        ("python3 -c 'import sys; sys.exit(28)' && echo done", 28),
        ("set -o pipefail; python3 -c 'import sys; sys.exit(28)'", 28),
    ],
)
def test_the_shell_reports_what_this_module_claims_it_does(script: str, status: int) -> None:
    """The premise of the whole rule, taken from bash rather than from a docstring.

    Mutation: none available -- this asserts about bash and not about this repository, which
    is exactly why it is here. Every refusal below is worth having only while these eight
    numbers hold, and the two ``pipefail`` rows are what say the option is a third of the
    answer rather than the whole of it. A change of shell, or a reader who assumes
    ``set -euo pipefail`` covers a ``;``, meets this first.
    """
    finished = subprocess.run(
        ["bash", "-lc", script], capture_output=True, check=False, timeout=30
    )
    assert finished.returncode == status


# ---------------------------------------------------------------------------------------
# The three shapes
# ---------------------------------------------------------------------------------------


def test_a_pipeline_without_pipefail_is_refused() -> None:
    """Mutation: read the first stage's status, which is what nothing does.

    This is ``run_019fde29``: a trainer that died staging a corpus, piped into ``tail``,
    recorded as ``exit_code: 0, outcome: succeeded`` in a store that cannot be corrected.
    """
    refusal = refuse("""bash -lc 'python t.py 2>&1 | tail -n 200'""")
    assert "pipefail" in str(refusal)
    assert refusal.reason_code == "exit_status_is_not_the_programs"


def test_the_same_pipeline_with_pipefail_is_allowed() -> None:
    """Mutation: refuse every pipeline, which would refuse the remedy this prints.

    A pipe is not the defect and must not be refused as one. ``tee`` into a log a sync
    carries to S3 is how a run on a capacity block node is read at all, and
    ``infra/block-node-bootstrap.sh`` writes exactly this line.
    """
    allow("""bash -lc 'set -o pipefail; python t.py 2>&1 | tee /work/log/train.log'""")
    allow("""bash -lc 'set -euo pipefail; python t.py 2>&1 | tail -n 200'""")
    allow("""bash -lc 'set -e -o pipefail; python t.py | tail'""")


def test_an_unconditional_or_true_is_refused() -> None:
    """Mutation: treat ``||`` as short-circuiting, which it is, of a status that is not.

    ``&&`` hands back the left side's status when it fails and ``|| true`` never does. The
    two look symmetrical and only one of them loses the failure.
    """
    refusal = refuse("""bash -lc 'python t.py || true'""")
    assert "|| true" in str(refusal)
    refuse("""bash -lc 'python t.py || :'""")


def test_a_command_after_a_semicolon_is_refused_and_pipefail_does_not_help() -> None:
    """Mutation: fix the pipeline and stop, which is the fix that reads as the whole fix.

    The upload is the case that costs something real: a run dies, ``aws s3 cp`` succeeds at
    copying nothing much, and the container exits zero.
    """
    refusal = refuse("""bash -lc 'python t.py; aws s3 cp out s3://b/k'""")
    assert "&&" in str(refusal), "the remedy is the operator that short-circuits"
    assert "pipefail` does not help" in str(refusal)
    # With the option set as well, so that a reader who added it is still told.
    refuse("""bash -lc 'set -euo pipefail; python t.py; echo done'""")


def test_the_program_is_found_rather_than_assumed_to_be_first() -> None:
    """Mutation: refuse any command with a ``;`` in it, which refuses the printed remedy.

    ``set -o pipefail; python t.py`` is a ``;`` with the program after it, which is the
    ordinary shape and the exact line the pipeline refusal tells people to write. A guard
    that refused it would be one nobody could satisfy.
    """
    allow("""bash -lc 'set -o pipefail; python t.py'""")
    allow("""bash -lc 'export WANDB_MODE=offline; cd /work; python t.py'""")
    allow(A_REAL_COMMAND)


def test_a_shell_inside_a_shell_is_read() -> None:
    """Mutation: read only the outermost command string.

    ``exec bash -c '...'`` is named in ``launchers.py`` as an ordinary wrapper, so a defect
    one level in is a defect, and a guard that stopped at the outer text would be got past
    by the shape the fan-out prologue itself produces.
    """
    refuse("""bash -lc 'exec bash -c "python t.py | tail"'""")
    allow("""bash -lc 'exec bash -c "set -o pipefail; python t.py | tail"'""")


def test_a_command_with_no_shell_is_not_checked() -> None:
    """Mutation: read the argv as though a shell would.

    ``ContainerOverrides.Command`` is exec form, so a ``|`` in an argument is a character
    the program receives and not a pipeline. A run that starts one program cannot have this
    defect, because its status is that program's by construction.
    """
    allow("""python -c 'import sys; print("edullm ready", sys.version)'""")
    allow("""python train.py --filter a|b""")


def test_a_pipe_inside_quotes_is_an_argument() -> None:
    """Mutation: split the command string with ``shlex`` or on the bare character.

    ``shlex`` removes the quotes that decide the answer, so a filter expression would read
    as a pipeline and a correct submission would be refused. Redirection is the same trap
    from the other side: ``2>&1`` carries an ``&``.
    """
    allow("""bash -lc 'python t.py --filter "{a|b}"'""")
    allow("""bash -lc 'python t.py --sep ";" --save-folder "$EDULLM_CHECKPOINT_DIR"'""")
    allow("""bash -lc 'python t.py > /tmp/out 2>&1'""")
    allow("""bash -lc 'python t.py  # piped into tail once, not any more'""")


# ---------------------------------------------------------------------------------------
# The waiver, spelled like the two beside it
# ---------------------------------------------------------------------------------------


def test_the_waiver_allows_it_and_says_so_where_the_approver_reads() -> None:
    """Mutation: allow the command and print nothing.

    The command is not on the approver page, so a waiver that only allowed would be a run
    recorded as a success with nobody having agreed to that. This is the same bargain
    ``EDULLM_LAUNCH_CHECK=waived`` strikes and it is spelled the same way on purpose.
    """
    waived = f"""bash -lc '{EXIT_STATUS_CHECK_WAIVER} python t.py | tail'"""
    allow(waived)
    note = waived_exit_status_note(tuple(shlex.split(waived)))
    assert note is not None
    assert EXIT_STATUS_CHECK_WAIVER in note


def test_a_clean_command_carrying_the_waiver_gets_no_note() -> None:
    """Mutation: print the note whenever the token is present.

    A line on every run that happens to carry the token is a line readers learn to skip,
    which is the failure mode of every warning that is not selective.
    """
    line = f"""bash -lc '{EXIT_STATUS_CHECK_WAIVER} python t.py'"""
    assert waived_exit_status_note(tuple(shlex.split(line))) is None


def test_the_reader_is_blind_to_the_waiver() -> None:
    """Mutation: have ``status_the_shell_would_report`` honour the waiver too.

    The note has to describe the defect a waived command still has. One function answering
    "is this refused" and "what is wrong with this" would make the note empty exactly when
    it is owed.
    """
    line = f"""bash -lc '{EXIT_STATUS_CHECK_WAIVER} python t.py | tail'"""
    assert status_the_shell_would_report(tuple(shlex.split(line))) is not None


# ---------------------------------------------------------------------------------------
# What this rule may not do to what is already recorded
# ---------------------------------------------------------------------------------------


def _every_recorded_command() -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []
    for path in sorted((PROJECT_ROOT / "fixtures").rglob("*")):
        if path.suffix not in {".yaml", ".yml", ".json"} or not path.is_file():
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError):
            continue
        stack: list[Any] = [document]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                command = node.get("command")
                if (
                    isinstance(command, list)
                    and command
                    and all(isinstance(word, str) for word in command)
                ):
                    found.append((str(path.relative_to(PROJECT_ROOT)), tuple(command)))
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return found


def test_no_recorded_command_is_refused() -> None:
    """THE TEST THAT DECIDES WHETHER THIS RULE COULD BE ADDED AT ALL.

    Mutation: any widening of the three shapes above. ``fixtures/manifests/`` carries
    canonical digests recorded as goldens, and ``launchers.py`` records the constraint in as
    many words: a rule that invalidates records written before it existed is a rule that
    cannot be added. This is that constraint, checked rather than asserted in prose.

    The count is asserted as well as the emptiness, because a glob that stopped matching
    would leave an empty loop passing quietly -- which is the defect
    ``tests/test_register_repository.py`` was carrying in three places.
    """
    recorded = _every_recorded_command()
    assert len(recorded) >= 15, "the fixtures stopped being read, so this checked nothing"
    refused = [
        (path, " ".join(command))
        for path, command in recorded
        if status_the_shell_would_report(command) is not None
    ]
    assert refused == []


def test_the_guide_prints_no_command_this_would_refuse() -> None:
    """Mutation: ship the rule and leave the researcher-facing surface contradicting it.

    A refusal for a line the guide tells somebody to copy is a rule that reads as a broken
    platform. Read out of the guides rather than restated, so the two cannot drift.
    """
    lines: list[str] = []
    for path in sorted((PROJECT_ROOT / "guides").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for fragment in text.split("bash -lc '")[1:]:
            lines.append("bash -lc '" + fragment.split("'", 1)[0] + "'")
    assert lines, "no command was read out of the guides, so this checked nothing"
    for line in lines:
        try:
            words = tuple(shlex.split(line))
        except ValueError:
            continue
        assert status_the_shell_would_report(words) is None, line
