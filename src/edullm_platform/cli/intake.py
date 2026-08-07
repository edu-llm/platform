"""``add`` and ``ask``: the two ways somebody changes what this platform will accept.

**THEY ARE TWO VERBS AND NOT ONE, AND ``docs-frank/reference/decisions.md`` SETTLES WHY.**
Teaching the system about a thing, such as a repository, a dataset, a shape, a model or a
person, produces a config change that is permanent, shared and self-service, with an agent
writing the pull request. Asking for something for yourself, such as a credential the
platform does not hold, an unusual resource or an escalation for work the platform cannot
express, produces a time-boxed grant to one person and genuinely needs a human. The single
self-routing verb was proposed twice and rejected: the two acts do not feel the same to the
person making them, and folding them makes a pull request the researcher could have written
themselves into a queue.

**NEITHER IS SPELLED ``request``.** ``contracts/authorization.py`` owns that word.
``RequestFacts`` and ``classify_request`` mean "a submission under judgement", so
``edullm request`` would mean two different things in adjacent paragraphs of one design.

**FOUR OF THE FIVE KINDS OF ``add`` REFUSE, AND THE REFUSAL IS THE FEATURE.** The buildout
spec defers the intake surface beyond ``add repository``, so there is nothing behind the
other four. Leaving them off the parser answers ``edullm add dataset`` with argparse's
"invalid choice", which tells an agent the word is wrong when the word is right. A refusal
carrying a code says the true thing in the vocabulary a skill already matches on, and it
costs no network to say.

**AND THE REFUSAL NAMES NO TEMPLATE FILE.** The four intake templates are collapsing into one
under a plan this module does not own, so a path written here goes stale on somebody else's
merge. ``edullm ask`` is the address, and it prints its own kinds.
"""

from __future__ import annotations

from typing import Final

from edullm_platform.cli.preflight import Refusal

__all__ = [
    "ADD_KINDS",
    "ASK_KINDS",
    "ASK_KIND_FOR",
    "ASK_QUEUE_LABEL",
    "CAPACITY_BLOCK_FIELDS",
    "CAPACITY_BLOCK_KIND",
    "RESUME_TESTED_ANSWERS",
    "SELF_SERVICE_KINDS",
    "capacity_block_refusals",
    "capacity_block_section",
    "issue_body",
    "register_repository_form",
    "routed_to_ask",
]

#: The five things the platform can be taught, and what teaching it one means. The set is
#: ``system-overview.md``'s under "What you click" and is not this module's to extend: a kind
#: added here with nothing behind it is a promise the binary cannot keep.
ADD_KINDS: Final[dict[str, str]] = {
    "repository": "a codebase the platform may build an image from and run",
    "dataset": "a corpus a run may name, and the role it may be named in",
    "shape": "a machine the catalog prices and a queue can place",
    "model": "a set of weights a run may resolve as an input",
    "person": "somebody on the roster, with the team they belong to",
}

#: The kinds a pull request can be opened for without asking anybody first. One today, and
#: the registration workflow is what makes it one: ``register-repository.yml`` edits five
#: platform files, runs a local verification and prepares the pull request. Nothing equivalent
#: exists for the other four.
SELF_SERVICE_KINDS: Final = frozenset({"repository"})


#: The ask a kind is filed under, where the intake forms offer one that fits. Absent for a
#: kind whose ask has no form of its own, which is the ordinary case and is why this is a
#: partial map rather than a required column beside :data:`ADD_KINDS`.
#:
#: **NAMING THE FORM IS THE WHOLE DIFFERENCE BETWEEN THIS REFUSAL AND A DEAD END.** It said
#: "file this with edullm ask" and stopped, and ``ask`` requires ``--kind`` from a closed set
#: the refusal did not name, so a reader who followed the instruction met argparse's own list
#: and had to guess which of four applied to a corpus. The one that applies is here.
ASK_KIND_FOR: Final[dict[str, str]] = {"dataset": "dataset-request"}


def routed_to_ask(kind: str) -> Refusal:
    """Why this kind is not something a pull request can be opened for from here.

    One code over the four rather than one per kind, which is ``DeniedOutrightError``'s
    argument in ``edullm_platform.errors``: the kind is already on the command line, so a
    code per kind would name in the vocabulary a thing the caller already said. What the
    detail adds is what the kind means and where the act goes instead.

    **AND FOR A DATASET IT NOW SAYS WHAT A PERSON ACTUALLY DOES, WHICH IS NOT A COMMAND.**
    Registering a corpus is a hand-written entry in ``config/datasets.yaml`` carrying a
    ``manifest_sha256`` and a ``payload_profile`` read off the corpus's own sealed
    ``dataset.json``, which means opening ``s3://edullm-data/``, which means an AWS role that
    fifteen of the thirty-five people on the roster do not have and that this binary holds
    none of. There is no ``register-dataset.yml`` to mirror ``register-repository.yml``.
    Saying so plainly is the point: a refusal that gestured at self-service would send
    somebody to build a pull request they cannot fill in.

    It also names ``edullm data``, because the reader who reaches this most often is not
    registering anything. They are looking for a corpus that is already there.
    """
    lines = [
        (
            "file this with edullm ask, and say what you want rather than how it should be "
            f"built. Teaching the platform {kind!r} means {ADD_KINDS[kind]}, which lands "
            "across several reviewed files and a stack no workflow may deploy, so no pull "
            "request can be opened for it from here."
        )
    ]
    if (ask_kind := ASK_KIND_FOR.get(kind)) is not None:
        lines.append(f"edullm ask --kind {ask_kind} --title '<what you need>' is the route.")
    if kind == "dataset":
        lines.append(
            "Registering a corpus is a hand-written entry in config/datasets.yaml pinning "
            "the manifest digest and payload profile off its sealed dataset.json, which "
            "needs an AWS role this binary does not hold, so a person does it. Run edullm "
            "data first: the corpus you want may already be registered."
        )
    return Refusal(code="add_kind_is_not_self_service", detail=" ".join(lines))


def register_repository_form(
    *,
    repository: str,
    github_repository_id: str,
    reason: str,
    dockerfile_path: str,
    default_branch: str,
) -> dict[str, str]:
    """``register-repository.yml``'s inputs, filled in from the checkout somebody is in.

    THE THREE REQUIRED INPUTS ARE FILLED AND THE OPTIONAL ONES ARE LEFT ALONE, WHICH IS THE
    POINT OF THE SPLIT. ``base_image_repository`` and ``base_image_digest`` default to the
    base two registrations already share and to the digest an existing registration of that
    base carries, which is the reviewed one. Sending a value from a laptop would be a second
    base to review, scan and re-pin, chosen by whoever happened to run the command.

    ``reason`` is required and has no default anywhere. It is written into a comment above
    the entry and it answers a question nothing else can: why this needs a repository of its
    own rather than a workload in an existing one.
    """
    return {
        "repository": repository,
        "github_repository_id": github_repository_id,
        "reason": reason,
        "dockerfile_path": dockerfile_path,
        "default_branch": default_branch,
    }


#: The kinds of ask the intake forms offer, which are also the labels a counter groups on.
#:
#: **A COPY, DELIBERATELY, WITH A TEST HOLDING IT TO THE SOURCE.** An installed wheel carries
#: no ``.github/``, so this cannot be read at runtime, and reading it over the network would
#: put a call in front of an ask somebody is already annoyed enough to be making.
#: ``tests/test_cli_ask.py`` reads ``.github/ISSUE_TEMPLATE/*.yml`` and asserts set equality,
#: which is the same seam ``ADMISSION_JOB`` sits on. The four templates collapse into one
#: triage form under a plan this module does not own, and the day that lands this list is red
#: rather than quietly filing asks under labels nothing counts.
#:
#: ``capacity-block`` is the one kind here that asks somebody to spend money that cannot be
#: got back. A block is charged upfront and is not cancellable, so the ask is a purchase
#: request wearing an issue's clothes, and it is a kind of its own rather than an
#: ``access-request`` because what it needs is a decision about several thousand dollars
#: rather than a name added to a list.
#: The one kind of ask that has required fields, spelled once and then spelled nowhere else so
#: that the parser, the kind list and the checks below cannot come to disagree about which kind
#: they are talking about.
CAPACITY_BLOCK_KIND: Final = "capacity-block"

ASK_KINDS: Final[tuple[str, ...]] = (
    "access-request",
    CAPACITY_BLOCK_KIND,
    "dataset-request",
    "feedback",
    "run-problem",
)

#: The four numbers a capacity block purchase is decided on, as ``(form id, what it asks)``.
#:
#: **REQUIRED HERE AND OPTIONAL ON THE FORM, AND THAT IS NOT AN INCONSISTENCY.**
#: ``.github/ISSUE_TEMPLATE/ask.yml`` is one triage form serving five kinds, and a GitHub form
#: cannot make a field required conditionally on a dropdown. So the form asks all four under a
#: heading saying they are what the purchase is decided on, and marks them ``required: false``
#: because marking them true would block somebody filing a ``run-problem``. A CLI has the kind
#: on the command line before it validates anything, so it can do what the form cannot, and
#: this is the door where the requirement is expressible.
#:
#: WHY REQUIRING THEM IS WORTH A REFUSAL AT ALL. A block is charged upfront and cannot be
#: cancelled, so the ask is answered by arithmetic rather than by judgement: peak memory picks
#: the machine, hours picks how many whole days are bought, the date decides whether any
#: offering can meet it, and a tested resume decides whether a crash costs an hour or the whole
#: window. An ask missing any of them cannot be priced, so it costs the asker a round trip and
#: two more weeks of lead time -- which is the thing this verb exists to save.
#:
#: The ids are the form's own field ids, so the two doors produce an issue with the same
#: headings and ``tools/report_asks.py`` reads one shape. ``tests/test_cli_ask.py`` holds them
#: equal to the form.
CAPACITY_BLOCK_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("peak_gpu_memory", "peak GPU memory the run needs, and how you arrived at it"),
    ("hours_needed", "hours of compute needed, and how you got that number"),
    ("needed_by", "the date you need it by"),
    ("resume_tested", "whether a resume from a checkpoint has been tested"),
)

#: What ``--resume-tested`` takes, and the sentence each token becomes in the issue.
#:
#: Short tokens on the command line and the form's own three options in the body, so an issue
#: filed from a terminal reads exactly like one filed from the browser and whoever answers it
#: does not have to learn two vocabularies. A free-text field here would accept "probably",
#: which is the one answer that is worse than "no": it reads as a yes to somebody skimming and
#: it is what somebody who has not tested a resume writes.
RESUME_TESTED_ANSWERS: Final[dict[str, str]] = {
    "tested": "Yes, I have restarted a run from a checkpoint and it continued",
    "writes-only": "It writes checkpoints but I have not tested a resume",
    "none": "No, it does not checkpoint",
}


def capacity_block_refusals(kind: str, answers: dict[str, str | None]) -> list[Refusal]:
    """Every missing field at once, or nothing, and nothing at all for the other four kinds.

    One refusal naming all of them rather than one per field. The action is the same whichever
    is absent -- go and find the number -- so four entries carrying one code would be four
    things to match on that mean one thing, and a caller fixing them one per attempt is the
    round trip this refusal exists to prevent.

    The mirror case is a field supplied on a kind that has no use for it, which is refused
    rather than dropped. A flag a verb accepts and ignores is one somebody goes on passing while
    believing it arrived, and the shape of that mistake here is a purchase argued for in fields
    nobody reading a ``run-problem`` will look at.
    """
    supplied = {name: value for name, value in answers.items() if value}
    if kind != CAPACITY_BLOCK_KIND:
        if not supplied:
            return []
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in sorted(supplied))
        return [
            Refusal(
                code="ask_field_belongs_to_another_kind",
                detail=(
                    f"{flags} describes a capacity block purchase and this is a "
                    f"{kind!r} ask, which nothing prices. Drop the flags or pass "
                    f"--kind {CAPACITY_BLOCK_KIND}."
                ),
            )
        ]
    missing = [name for name, _ in CAPACITY_BLOCK_FIELDS if not answers.get(name)]
    if not missing:
        return []
    asked = dict(CAPACITY_BLOCK_FIELDS)
    return [
        Refusal(
            code="capacity_block_ask_is_incomplete",
            detail=(
                "a capacity block is charged upfront and cannot be cancelled, so it is "
                "priced from four numbers rather than judged, and these are missing: "
                + "; ".join(
                    f"--{name.replace('_', '-')} ({asked[name]})" for name in missing
                )
                + ". An estimate you can say the basis of is a fine answer and an absent "
                "one costs a round trip and another two weeks of lead time."
            ),
        )
    ]


def capacity_block_section(answers: dict[str, str | None]) -> str:
    """The four answers as part of what the asker said, in the order the form asks them.

    Appended to the sentence somebody typed rather than kept beside it, because these arrived
    on flags the asker chose to pass and they are the asker's words. ``issue_body``'s footer is
    the other thing, four facts about the install that nobody typed, and the ``---`` it draws
    keeps that boundary. Only called once every field is present, so there is no absent case.
    """
    lines = ["", "", "## What the purchase is decided on", ""]
    for name, asked in CAPACITY_BLOCK_FIELDS:
        value = answers[name]
        assert value is not None  # capacity_block_refusals has already refused an absence
        lines.append(f"- **{asked}**: {value}")
    return "\n".join(lines) + "\n"


#: The label that puts an ask in the queue, which is a separate fact from what kind it is.
#:
#: **THE KIND IS NOT THIS, AND CONFUSING THE TWO IS HOW THE COUNT WENT WRONG.**
#: ``tools/report_asks.py`` asks GitHub for issues carrying this and then groups those by kind,
#: so a kind label on its own reaches no queue. Both doors have to put this on. The form does,
#: unconditionally, in its ``labels:``; the CLI did not, and every ask filed through it between
#: the verb shipping and 2026-08-06 is absent from the board while looking correctly labelled
#: on the issue itself. That is the failure the four-into-one collapse existed to prevent,
#: arriving through the door nobody was watching.
#:
#: One constant read by all three rather than the string typed in three places. The counter
#: imports it, ``edullm ask`` sends it, and ``tests/test_triage_form.py`` holds the form's
#: ``labels:`` equal to it, so the queue cannot be renamed on one side only.
ASK_QUEUE_LABEL: Final = "ask"


def issue_body(
    *,
    detail: str,
    submitter: str | None,
    version: str | None,
    config_directory: str,
    run_id: str | None,
) -> str:
    """What somebody typed, and the three facts they could not have known to include.

    **THE FOOTER IS THE REASON THIS VERB BEATS OPENING THE FORM IN A BROWSER.** Half of what
    is asked here is about a refusal, and the first questions anybody answering one asks are
    which ``edullm``, which reviewed configuration and who. A stale install checking against
    the frozen copy it was built with is the commonest cause of a refusal that looks wrong,
    and it is invisible from the sentence somebody types. The form cannot ask for it and a
    person cannot be expected to volunteer it.

    Nothing else is collected. No paths, no repository name, no commit. The footer is four
    facts about the tool rather than about the work, which is the same line
    ``docs-frank/reference/designing-the-cli.md`` draws around a usage log.
    """
    lines = [detail.strip(), "", "---", "", "Filed by edullm."]
    lines.append(f"- who: {submitter or 'gh has recorded nobody'}")
    lines.append(f"- edullm: {version or 'an install with no recorded version'}")
    lines.append(f"- reviewed configuration: {config_directory}")
    if run_id is not None:
        lines.append(f"- run: {run_id}")
    return "\n".join(lines) + "\n"
