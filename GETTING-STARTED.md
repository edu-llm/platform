# Getting started

This platform runs training jobs on GPUs in AWS and keeps a record of every one: what
code ran, on what machine, who approved it, what it cost, and where its output went. You
submit through a form on GitHub. You do not need an AWS account, credentials, or anything
installed on your laptop.

If you are on the `team-members` team in this organisation, you already have everything you
need to submit. Nobody has to grant you anything first.

## Your first run takes about five minutes

Open [Submit a run](../../actions/workflows/submit-run.yml), press **Run workflow**, and
fill in five fields. The rest have defaults that are correct for a first run.

| Field | What to put |
| --- | --- |
| `commit_sha` | Your branch name. A tag or a full commit works too. |
| `workload_profile` | `olmo-core-check-cpu` |
| `team` | Your group, lower-case with hyphens, like `memory-split` |
| `experiment` | Anything, like `onboarding`. You are making it up; it needs no registering. |
| `wandb_project` | Your Weights and Biases project |

Leave `command` as it is. The default runs a one-line check that needs no code of your own,
which is the point of a first run: you are testing that the path from the form to a machine
and back works, not testing your model.

Press the green button. The run compiles, gets priced, and waits for a lead to approve it.
Once approved it reaches a machine, runs, and writes its record. Watch the workflow page —
every step says what it did and, if it refuses, why.

## What the fields actually decide

**`workload_profile` is the one that matters.** It fixes the machine, the time limit and
the retry limit together, so you do not have to know any of them. There are three:

- `olmo-core-check-cpu` — a CPU box, one hour. Start here.
- `olmo-core-check-gpu` — one A10G, one hour. Use it to check your code sees a GPU.
- `olmo-core-train-1gpu` — one A10G, twelve hours, two attempts, checkpointing required.
  This is the one for real training.

**`team` routes the approval and nothing else.** It records whose work a run is. Any lead
may approve any run, so a typo delays nothing and grants nothing.

**`experiment` groups related runs** — a sweep, an ablation, one week of work. It is also
what the cost view groups by, so it is worth being consistent within a project.

**`compute_profile` and `image_digest` are marked advanced and mean it.** Leave them alone.
The image is resolved from the commit you named, and the machine comes from the workload
profile.

## Real training: the one line that matters

Everything below is about `olmo-core-train-1gpu`, and one line decides whether twelve hours
of GPU time produces anything you can use.

**Your training command must write checkpoints to `$EDULLM_CHECKPOINT_DIR`.**

```
bash -lc 'python src/examples/llm/train.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR"'
```

Three things about that line are deliberate.

**`bash -lc` is not decoration.** The container runs your command directly rather than
through a shell, so without it `$EDULLM_CHECKPOINT_DIR` arrives as those twenty-two literal
characters and OLMo-core creates a directory with that name. Wrap the command in
`bash -lc '...'` and the variable expands.

**The variable, not a path you write yourself.** The platform mints your run id when it
compiles the submission, which is after you have filled in the form — so there is no path
you could have typed. It is handed to the container instead.

**Quote it.** `"$EDULLM_CHECKPOINT_DIR"` rather than bare, for the ordinary reason.

### Why this is the line that matters

OLMo-core's example defaults `--save-folder` to `/tmp`, which is local disk on a machine
that stops existing when your job ends. A twelve-hour run that takes the default trains for
twelve hours, writes checkpoints nobody can reach, exits zero, and is recorded as a
success. Nothing about that looks wrong until you go looking for the model.

### What resuming buys you

`olmo-core-train-1gpu` allows two attempts. If the machine your job is on goes away —
hardware failure, or a reclaimed spot instance later on — Batch starts a second attempt
with the same run id, therefore the same `$EDULLM_CHECKPOINT_DIR`, and OLMo-core's
`Trainer.fit()` picks up from the last checkpoint on its own. You write nothing to make
that happen beyond the save folder.

A retry only fires for a lost machine. A crash in your own code exits instead of running
again, because a traceback in the first minute produces the identical traceback in the
second and spends the budget twice.

## Six things that will bite you

Every one of these came out of getting a real twelve-hour run working, in the order they
were hit. They are not hypothetical.

**`ephemeral_save_interval` must be below `save_interval`.** OLMo-core refuses the config
otherwise, in the first seconds, before anything trains. Set it to `null` if you do not
want ephemeral checkpoints.

**Turn off checkpoint pruning.** OLMo-core defaults to keeping the last three checkpoints
and deleting the rest. The workload role deliberately has no delete permission — every run
writes under its own id, so nothing ever needs to be deleted — and the prune fails. Pass
`trainer.callbacks.checkpointer.max_checkpoints=null` and keep them all.

**Turn off `torch.compile`.** The research image carries no C compiler, so compilation dies
with `Failed to find C compiler`. Pass `train_module.compile_model=false`.

**Turn off the evaluators, or point them at local data.** The example's `lm_evaluator` wants
a `.csv.gz` metadata file that is not served over HTTP, so it fails while the trainer is
still being built. Pass `trainer.callbacks.lm_evaluator.enabled=false` and
`trainer.callbacks.downstream_evaluator.enabled=false` unless you have set up eval data.

**Set `max_duration` explicitly.** It defaults to one epoch, which may be far more or far
less than the twelve hours you have.

**Your command has to fit in 8,192 bytes.** That is Batch's limit on the whole container
override, command and environment together. A long program belongs in your repository, not
on the command line.

Put together, a working training command looks like this:

```
bash -lc 'python src/examples/llm/train.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR" --work-dir /tmp/dc train_module.compile_model=false trainer.max_duration.value=4000 trainer.max_duration.unit=steps trainer.callbacks.checkpointer.save_interval=200 trainer.callbacks.checkpointer.ephemeral_save_interval=null trainer.callbacks.checkpointer.max_checkpoints=null trainer.callbacks.lm_evaluator.enabled=false trainer.callbacks.downstream_evaluator.enabled=false'
```

## What the container gives you

Your command runs with these set. You never supply them, and a value you set yourself in
the command is a value the record will disagree with.

| Variable | What it holds |
| --- | --- |
| `EDULLM_RUN_ID` | This run's id, stable across retries |
| `EDULLM_CHECKPOINT_DIR` | Where checkpoints go |
| `EDULLM_OUTPUT_PREFIX` | Where everything else this run writes goes |
| `EDULLM_TEAM` | The team you claimed |
| `EDULLM_COMMIT_SHA` | The commit that was resolved and built |
| `EDULLM_DATASET_RELEASE` | The dataset you named |
| `WANDB_PROJECT`, `WANDB_ENTITY` | Read by the W&B client directly |

## When it goes wrong

Open an issue. There are templates for [a run that went
wrong](../../issues/new?template=run-problem.yml), [a dataset you
need](../../issues/new?template=dataset-request.yml), and [the platform getting in your
way](../../issues/new?template=platform-feedback.yml).

**@philote-dev reads these.** Include the workflow run link — it carries the run id, and
the run id is what every record is filed under.

Two failures are worth recognising yourself, because they read as something else.

A refusal that names a *field* is usually a form mistake and the message says which field.
A refusal that arrives *after* a lead approved it is usually a mismatch between two things
that were each individually valid — a workload whose repository is not registered, or a
compute profile nothing is provisioned for. Both name a reason code; quote it in the issue.

A run that succeeds and leaves an empty checkpoint prefix is the `/tmp` case above. Check
`$EDULLM_CHECKPOINT_DIR` is on your command line before you resubmit.
