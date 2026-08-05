# edu-llm platform

Shared research compute for eduLLM. Submit a training, evaluation or data job through a form on GitHub, a team lead approves it, and it runs on AWS. **No AWS account, credentials or local install required.**

[**Submit a run**](../../actions/workflows/submit-run.yml) · [**Look at a run, or stop it**](../../actions/workflows/cancel-run.yml)

## Start here

**[Using the platform](guides/the-platform.md)** takes you from nothing to a finished run in about five minutes. Read it first. It covers access, the form, approval, and how to look at or stop a run, and it applies whichever repository you work in.

Then the guide for what you are actually doing:

| Guide | For |
| --- | --- |
| [Training a model](guides/olmo-core.md) | OLMo-core. Pretraining and fine-tuning, one to eight GPUs |
| [Running an evaluation](guides/olmo-eval-full.md) | olmo-eval-full. Scoring a model against a task suite |
| [Validating a corpus](guides/edullm-data.md) | edullm-data. Checking and publishing a dataset |

Nothing above needs anything installed. If you would rather work in a terminal than in the Actions UI, `uv tool install --force git+https://github.com/edu-llm/platform` puts the `edullm` command on your path. It prices a submission offline before it sends it, and submits, follows and stops runs. [From a terminal](guides/the-platform.md#from-a-terminal) is the whole of it.

## What it does

You get CPU and GPU machines from a single T4 up to eight H100s, without touching AWS. Picking a workload profile fixes the machine, the time limit, the retry limit and the checkpointing together, so there is one decision rather than four.

Several corpora are published, frozen and consistently tokenised. Your job is handed the exact corpus, version and tokeniser that its record names, so a result cannot quietly disagree with the thing that produced it.

Every run is released by a person who is shown what it will cost, and is booked to a research group. Every run is also recorded: one run id names the job, its outputs and its Weights and Biases run, written before the job starts and never rewritten.

And commands that contradict the run you asked for are refused when you submit, rather than twelve hours and a bill later. Four GPUs with a single process is one of them, and so is a promised checkpoint written nowhere anyone can reach.

## Getting help

Open an issue. **@philote-dev reads these.** Include the workflow run link: it carries the run id, and the run id is what every record is filed under.

- [**Access request**](../../issues/new?template=access-request.yml) when you cannot see the Run button, or a submission says you are not on the roster
- [**Run problem**](../../issues/new?template=run-problem.yml) when a run was refused, failed, or is stuck
- [**Dataset request**](../../issues/new?template=dataset-request.yml) when you need a corpus that is not on the form
- [**Platform feedback**](../../issues/new?template=platform-feedback.yml) when something works but gets in your way

---

Changing the platform rather than using it, or reviewing what it claims? See **[MAINTAINING.md](MAINTAINING.md)**.
