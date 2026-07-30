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
