"""What ``torchrun`` will actually exec, derived the way ``torchrun`` derives it.

**A TEST THAT READS THE COMPOSED STRING IS NOT A TEST OF WHAT RUNS, AND THE DIFFERENCE ONCE
COST THE FLAGSHIP RUN.** ``torchrun --nnodes=8 ... python train.py --flag`` ends with
``python train.py --flag``, reads correctly to anybody, and starts nothing: torchrun's
positional is a *script path* and torchrun supplies the interpreter itself, so the child argv
is ``python -u python train.py --flag`` and every rank tries to open a file named ``python``.
An assertion on the tail of the string passes either way. An assertion on the argv does not,
which is why every launcher test in this project goes through here.

**IT IS A TRANSCRIPTION, AND ONE TEST IS WHAT KEEPS IT ONE.** ``torch`` is not a dependency of
this repository -- it is a hundred megabyte wheel in service of one function's argument
handling -- so the rule below is copied out of ``config_from_args`` in
``torch/distributed/run.py`` rather than imported.
``test_the_transcription_of_torchruns_argv_rule_agrees_with_torchrun`` in
``tests/test_block_multinode.py`` imports the real thing where it is installed and holds the
two answers against each other, on both branches of the rule, so this cannot drift silently on
any machine that has torch. Where torch is absent that test skips and this file stands on the
source it quotes.

Verified against torch 2.13.0. The rule, from that function verbatim:

    with_python = not args.no_python
    ...
    if with_python:
        cmd = os.getenv("PYTHON_EXEC", sys.executable)
        cmd_args.append("-u")
        if args.module:
            cmd_args.append("-m")
        cmd_args.append(args.training_script)
    else:
        cmd = args.training_script
    if not use_env:
        cmd_args.append(f"--local-rank={macros.local_rank}")
    cmd_args.extend(args.training_script_args)

``use_env`` is always true for ``torchrun`` -- ``get_use_env`` answers true when the namespace
carries no ``use_env`` attribute, and only the retired ``torch.distributed.launch`` parser
adds one -- so no ``--local-rank`` is ever inserted and this transcription does not model it.
"""

from __future__ import annotations

import shlex
from typing import Final

__all__ = ["INTERPRETER", "child_argv"]

#: Stands in for ``sys.executable``, which is a path to whichever interpreter is running
#: torchrun and is therefore not a thing a test can name. Its presence is the whole assertion:
#: it appears exactly when torchrun has decided to run the positional under an interpreter.
INTERPRETER: Final = "<sys.executable>"

#: torchrun's own options that take no value. Every other option this project writes is spelled
#: ``--flag=value``, and :func:`child_argv` refuses anything else rather than guessing -- a
#: ``--nproc-per-node 8`` written with a space would otherwise make ``8`` look like the
#: positional, and the test built on that reading would assert about a job nobody launched.
_VALUELESS: Final = frozenset({"--no-python", "--no_python", "--standalone", "--use-env"})

#: Options that change how the positional is interpreted. Nothing in this lane writes one, and
#: a transcription that ignored them would answer confidently about a line it does not model.
_CHANGES_THE_POSITIONAL: Final = frozenset(
    {"--module", "-m", "--run-path", "--run_path", "--no-python", "--no_python"}
)


def child_argv(line: str, *, launcher: str = "torchrun") -> tuple[str, ...]:
    """The argv of one worker process, out of the whole composed launch line.

    Takes the line as the container's ``bash -lc`` takes it -- :func:`shlex.split` is the same
    word splitting and the same quote handling -- so a quoted argument in the training command
    is one word here exactly as it is one word there.
    """
    words = shlex.split(line)
    if not words or words[0] != launcher:
        raise AssertionError(f"the line does not start with {launcher!r}: {words[:1]}")

    flags: list[str] = []
    index = 1
    while index < len(words) and words[index].startswith("-"):
        word = words[index]
        if "=" not in word and word not in _VALUELESS:
            raise AssertionError(
                f"{word!r} takes a value and is not written with an equals sign, so nothing "
                "here can tell its value apart from torchrun's positional. Write "
                "--flag=value in torchrun_command, or teach this helper the option."
            )
        flags.append(word.split("=", 1)[0])
        index += 1

    unmodelled = _CHANGES_THE_POSITIONAL.intersection(flags) - {"--no-python", "--no_python"}
    if unmodelled:
        raise AssertionError(f"this helper does not model {sorted(unmodelled)}")
    if index >= len(words):
        raise AssertionError("the line names no positional, so torchrun would run nothing")

    positional = tuple(words[index:])
    if {"--no-python", "--no_python"}.intersection(flags):
        return positional
    return (INTERPRETER, "-u", *positional)
