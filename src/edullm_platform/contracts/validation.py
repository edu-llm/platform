def require_checkpoint_for_retries(
    *,
    maximum_attempts: int,
    checkpoint: object | None,
) -> None:
    if maximum_attempts > 1 and checkpoint is None:
        raise ValueError("retryable workloads require a checkpoint contract")


#: What a program name may not contain. A quote survives into the first element only when the
#: splitting went wrong, and whitespace is the signature of a command line that was never
#: split at all -- see :func:`require_startable_program`.
_UNRUNNABLE_IN_A_PROGRAM_NAME = frozenset('"\'')


def require_startable_program(command: tuple[str, ...]) -> tuple[str, ...]:
    """The first element must be able to name a program, or nothing can start.

    **This exists because a run reached an instance without it.** On 2026-07-30 a submitter's
    shell quoting survived into the form field, so ``shlex.split`` saw one fully quoted string
    and returned a single token holding the entire command line. That compiled, was admitted
    as routine, was priced, was read and released by a team lead, was accepted by Batch, and
    was discovered only when the container tried to execute the whole line as a program name
    and reported ``executable file not found in $PATH``. Nothing between the form and the
    instance had an opinion about what the first element could be.

    A refusal here lands in the compile job, ahead of the approval gate, which is the whole
    point: the cost of the version that got through was a lead's attention and an instance
    that pulled a three-gigabyte image before failing.

    **Only the first element.** Whitespace and quotes are ordinary in arguments -- the
    corrected form of that same submission passes ``print('hello')`` as one argument -- and a
    rule reaching past the program name would refuse most real commands. What makes the first
    element different is that the container runtime resolves it against ``$PATH`` rather than
    handing it to a shell, so it is a filename and nothing else.

    An empty ``command`` is returned untouched so that ``min_length`` reports it. An absent
    command and an unusable program name are different mistakes, and a submitter fixes them
    differently.
    """
    if not command:
        return command
    program = command[0]
    if not program:
        raise ValueError("the first command element must name a program, and this one is empty")
    if any(character.isspace() for character in program) or (
        _UNRUNNABLE_IN_A_PROGRAM_NAME & set(program)
    ):
        raise ValueError(
            "the first command element must name a program, and this one carries whitespace "
            "or a quote -- which is what a command line looks like when it was not split "
            "into arguments. Pass the program and each argument separately, and do not wrap "
            "the whole line in quotes."
        )
    return command


#: Programs that read one argument as an entire command line. The bare names and the
#: absolute forms both appear in real submissions, so both are recognised.
_SHELLS_THAT_TAKE_A_COMMAND_STRING = frozenset(
    {"sh", "bash", "dash", "zsh", "ksh", "/bin/sh", "/bin/bash", "/usr/bin/bash", "/bin/zsh"}
)


def require_a_shell_command_that_kept_its_quotes(command: tuple[str, ...]) -> tuple[str, ...]:
    """A shell invoked with ``-c`` must be handed exactly one word, or it runs the first one.

    **This exists because a run reached a GPU without it, and is the mirror of the mistake
    :func:`require_startable_program` catches.** That one is quoting that survived the form
    field, arriving as a single token holding a whole command line. This one is quoting that
    was lost. On 2026-08-01 a submission carried::

        bash -lc python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --steps 20

    without the single quotes the guide prints around the program. ``shlex.split`` on the
    runner turned it into nine words, and ``-c`` takes exactly one: bash ran ``python`` with
    no script, and everything after it became ``$0``, ``$1``, ``$2``. The container started a
    Python that had nothing to interpret and exited 1 in under five seconds.

    Nothing refused it. It compiled, it was priced, it passed the approval gate, it reached an
    A10G, it pulled a three-gigabyte image, and the only record of what went wrong was a
    CloudWatch stream that the deploy credential is deliberately not allowed to read. The
    submitter sees ``Essential container in task exited`` and an exit code of 1, which is what
    a bad hyperparameter looks like too.

    **The discriminator is whether the word after ``-c`` holds a command line.** A correctly
    quoted submission passes one word containing spaces -- ``python x.py --steps 20`` -- and
    may legitimately pass more after it, since a shell reads those as ``$0`` onward. A
    submission that lost its quotes passes a bare program name with its arguments trailing
    behind. So the refusal is narrow: more than one word after ``-c``, and the first of them
    has no whitespace in it. Everything a working submission does is still allowed.
    """
    if len(command) < 2 or command[0] not in _SHELLS_THAT_TAKE_A_COMMAND_STRING:
        return command

    # `-c` must end its cluster to take the next word as the command string, so `-lc` is the
    # form the guide prints and `-cl` would be a different thing entirely.
    for position, word in enumerate(command[1:], start=1):
        if not word.startswith("-") or not word.endswith("c"):
            break
        if word == "-c" or (len(word) > 1 and set(word[1:]) <= set("abcdefhiklmnprstuvx")):
            trailing = command[position + 1 :]
            if len(trailing) > 1 and not any(character.isspace() for character in trailing[0]):
                raise ValueError(
                    f"{command[0]} {word} reads exactly one word as the command, and this "
                    f"submission gives it {len(trailing)}. It would run "
                    f"`{trailing[0]}` alone and hand the rest to it as $0, $1, $2 -- which "
                    "starts, costs an instance, and exits without running your program. "
                    "Quote the whole program: "
                    f"{command[0]} {word} '{' '.join(trailing)}'"
                )
            break
    return command
