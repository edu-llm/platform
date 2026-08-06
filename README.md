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

The three above need nothing installed and no AWS account. If you would rather work in a terminal than in the Actions UI, `uv tool install --force git+https://github.com/edu-llm/platform` puts the `edullm` command on your path, and `edullm --version` has to read 3.4.8 or higher afterwards, because below that `submit` unquotes your command and the submission is refused. It prices a submission offline before it sends it, and submits, follows and stops runs. Re-running that same line is how you upgrade, whichever way the tool was installed. `uv tool upgrade` follows the ref the install named, so it upgrades one made from the bare URL above and answers `Nothing to upgrade` to one pinned at a release tag, however far behind that one is. If you installed before v4.2.2, when the package was called `edullm-platform` rather than `edullm`, run `uv tool uninstall edullm-platform` **before** that install line and not after: both installs own the same `edullm` executable and uv deletes it with the old entry, which leaves you with a healthy-looking `uv tool list` and no command. [Day one](guides/day-one.md) is the short way through it and [from a terminal](guides/the-platform.md#from-a-terminal) is the reference.

Working through Cursor, Claude Code or Codex rather than typing the commands yourself? [**A skill for your coding agent**](skills/README.md) is one file to drop into your own repository, and it is what stops an agent writing a shell script that talks to AWS.

## What it does

You get CPU and GPU machines from a single T4 up to eight A100s, without touching AWS. Picking a workload profile fixes the time limit, the retry limit and the checkpointing together. **It does not fix the machine.** `compute_profile` is a field of its own beside it, and it is the one that decides what a run costs.

Several corpora are published, frozen and consistently tokenised. Your job is handed the exact corpus, version and tokeniser that its record names, so a result cannot quietly disagree with the thing that produced it.

An expensive run, or a sweep of any size, is released by a team lead who is shown what it will cost. A cheap single run starts on its own. [Using the platform](guides/the-platform.md#approval) has the figure that draws the line, and it is worth reading from the tool rather than from here.

Either way the run is booked to a research group and recorded. One run id names the job, its outputs and its Weights and Biases run, written before the job starts and never rewritten, and that record is what makes a run one you can cite.

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
