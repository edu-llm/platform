# edu-llm platform

Shared research compute for eduLLM. Submit a training, evaluation or data job through a form on GitHub, a team lead approves it, and it runs on AWS. **No AWS account, credentials or local install required.**

[**Submit a run**](../../actions/workflows/submit-run.yml) · [**Look at a run, or stop it**](../../actions/workflows/cancel-run.yml)

## Start here

**[Day one](guides/day-one.md)** is the one page to read if you have nothing installed and no AWS account. It gets you to a run that printed a number, and it names every wall that is still a wall. Two screens.

**[Using the platform](guides/the-platform.md)** is the reference behind it. The form, the corpora, the machines, approval, and how to look at or stop a run, whichever repository you work in.

Then the guide for what you are actually doing:

| Guide | For |
| --- | --- |
| [Training a model](guides/olmo-core.md) | OLMo-core, for pretraining and fine-tuning on one to eight GPUs |
| [Running an evaluation](guides/olmo-eval-full.md) | olmo-eval-full, for scoring a model against a task suite |
| [Validating a corpus](guides/edullm-data.md) | edullm-data, for checking and publishing a dataset |

The three above need nothing installed and no AWS account. If you would rather work in a terminal than in the Actions UI, `uv tool install --force git+https://github.com/edu-llm/platform` puts the `edullm` command on your path. It prices a submission offline before it sends it, and submits, follows and stops runs. Re-running that same line is how you upgrade. Do not reach for `uv tool upgrade`, which answers `Nothing to upgrade` however far behind a git-installed tool is. [Day one](guides/day-one.md) is the short way through it and [from a terminal](guides/the-platform.md#from-a-terminal) is the reference.

Working through Cursor, Claude Code or Codex rather than typing the commands yourself? [**A skill for your coding agent**](skills/README.md) is one file to drop into your own repository, and it is what stops an agent writing a shell script that talks to AWS.

## What it does

You get CPU and GPU machines from a single T4 up to eight H100s, without touching AWS. Picking a workload profile fixes the machine, the time limit, the retry limit and the checkpointing together, so there is one decision rather than four.

Several corpora are published, frozen and consistently tokenised. Your job is handed the exact corpus, version and tokeniser that its record names, so a result cannot quietly disagree with the thing that produced it.

Every run is released by a person who is shown what it will cost, and is booked to a research group. Every run is also recorded: one run id names the job, its outputs and its Weights and Biases run, written before the job starts and never rewritten.

And commands that contradict the run you asked for are refused when you submit, rather than twelve hours and a bill later. Four GPUs with a single process is one of them, and so is a promised checkpoint written nowhere anyone can reach.

## Getting help

Open an issue. **@philote-dev reads these.** Include the workflow run link: it carries the run id, and the run id is what every record is filed under.

[**Ask for something**](../../issues/new?template=ask.yml) is the one form now, and picking a kind on it is what makes a repeated ask visible as a missing feature rather than as three people needing a favour.

- `access-request` when you cannot see the Run button, or a submission says you are not on the roster
- `run-problem` when a run was refused, failed, or is stuck
- `dataset-request` when you need a corpus that is not on the form
- `feedback` when something works but gets in your way

`edullm ask --kind <kind>` files the same thing from a terminal and attaches which version and which reviewed configuration you were on.

---

Changing the platform rather than using it, or reviewing what it claims? See **[MAINTAINING.md](MAINTAINING.md)**.
