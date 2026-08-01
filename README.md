# edu-llm platform

Shared research compute for eduLLM. Submit a training, evaluation or data job through a form on GitHub, a team lead approves it, and it runs on AWS. **No AWS account, credentials or local install required.**

[**Submit a run**](../../actions/workflows/submit-run.yml) · [**Look at a run, or stop it**](../../actions/workflows/cancel-run.yml)

## Start here

**[Using the platform](guides/the-platform.md)** takes you from nothing to a finished run in about five minutes. Read it first — it covers access, the form, approval, and how to look at or stop a run, and it applies whichever repository you work in.

Then the guide for what you are actually doing:

| Guide | For |
| --- | --- |
| [Training a model](guides/olmo-core.md) | OLMo-core — pretraining and fine-tuning, one to eight GPUs |
| [Running an evaluation](guides/olmo-eval-full.md) | olmo-eval-full — scoring a model against a task suite |
| [Validating a corpus](guides/edullm-data.md) | edullm-data — checking and publishing a dataset |

## What it does

You get CPU and GPU machines from a single T4 up to eight H100s, without touching AWS. Picking a workload profile fixes the machine, the time limit, the retry limit and the checkpointing together, so there is one decision rather than four.

Several corpora are published, frozen and consistently tokenised. Your job is handed the exact corpus, version and tokeniser that its record names, so a result cannot quietly disagree with the thing that produced it.

Every run is released by a person who is shown what it will cost, and is booked to a research group. Every run is also recorded: one run id names the job, its outputs and its Weights and Biases run, written before the job starts and never rewritten.

And commands that contradict the run you asked for — four GPUs and a single process, a promised checkpoint written nowhere anyone can reach — are refused when you submit, rather than twelve hours and a bill later.

## Getting help

Open an issue. **@philote-dev reads these.** Include the workflow run link: it carries the run id, and the run id is what every record is filed under.

- [**Access request**](../../issues/new?template=access-request.yml) — you cannot see the Run button, or a submission says you are not on the roster
- [**Run problem**](../../issues/new?template=run-problem.yml) — a run was refused, failed, or is stuck
- [**Dataset request**](../../issues/new?template=dataset-request.yml) — you need a corpus that is not on the form
- [**Platform feedback**](../../issues/new?template=platform-feedback.yml) — something works but gets in your way

---

Changing the platform rather than using it, or reviewing what it claims? See **[MAINTAINING.md](MAINTAINING.md)**.
