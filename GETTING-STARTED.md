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

## Which corpus to pick

Five are published and readable. All use the dolma2 tokenizer, all are frozen, and none can
be written to by anything you run.

| `dataset_release` | Train tokens | Objects |
| --- | --- | --- |
| `refhq-regmix-5p5b-v2` | 5.5B | 24 |
| `regmix-10b-v1` | 10.0B | 41 |
| `olmo-original-30b-v1` | 31.3B | 120 |
| `olmo-127b-v1` | 126.5B | 474 |
| `olmo-150b-dolma2-v1` | 157.2B | 6,851 |

Size costs you nothing up front — the shards are memory-mapped from S3 as the loader reaches
them, so a 157B corpus starts as quickly as a 5B one and reads only what your step count
needs. Pick by what you are training, not by what you can afford to download.

## Real training: one line

Everything below is about `olmo-core-train-1gpu`. Set `dataset_release` to the corpus you
want and use this command:

```
bash -lc 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --steps 4000'
```

That is the whole thing. It opens the corpus you picked on the form, reads it at the width
the corpus was written at, checkpoints where a retry can find them, and reports to your W&B
project. Everything it needs it takes from the environment the container already has.

**`bash -lc` is not decoration.** The container runs your command directly rather than
through a shell, so without it `$EDULLM_RUN_ID` arrives as those fourteen literal characters
instead of your run id. Wrap the command in `bash -lc '...'` and the variable expands.

Anything after the flags is a config override, so you can change one thing without leaving
this entry point:

```
bash -lc 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --steps 4000 --model-factory olmo2_1B optim.lr=3e-4'
```

`--dry-run` resolves the corpus, prints the whole config, and trains nothing. It is the
cheapest way to find out that a flag you passed does not exist.

### Why not the OLMo-core example

`src/examples/llm/train.py` trains on a C4 shard fetched from `olmo-data.org` with the GPT-2
tokenizer, both written into the file. If you pick `regmix-10b-v1` on the form and run the
example, you get a loss curve, a checkpoint, and a corpus that was never opened. Nothing
fails and nothing says anything is wrong — the record says which corpus you asked for, and
the run read a different one.

The example also defaults `--save-folder` to `/tmp`, which is local disk on a machine that
stops existing when your job ends. A twelve-hour run that takes the default trains for
twelve hours, writes checkpoints nobody can reach, exits zero, and is recorded as a success.

You can still run the example, and the long command under **Six things that will bite you**
is what it takes. The entry point above exists because that list should not be something
each person rediscovers.

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

**`.edullm/train_on_corpus.py` already handles all six.** This section is here for anyone
running the OLMo-core example directly, or writing their own program — and as the list of
what the entry point is doing on your behalf, since none of it is obvious from the outside.

**`ephemeral_save_interval` must be below `save_interval`.** OLMo-core refuses the config
otherwise, in the first seconds, before anything trains. Set it to `null` if you do not
want ephemeral checkpoints.

**Turn off checkpoint pruning.** OLMo-core defaults to keeping the last three checkpoints
and deleting the rest. The workload role deliberately has no delete permission — every run
writes under its own id, so nothing ever needs to be deleted — and the prune fails. Pass
`trainer.callbacks.checkpointer.max_checkpoints=null` and keep them all.

**`torch.compile` needs a C compiler, and the image now has one.** It did not, and a run
died on the first compiled region with `Failed to find C compiler` — after the GPU had been
paid for. If you are on an older image digest than the one the GPU job definition pins, pass
`train_module.compile_model=false`.

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
| `EDULLM_DATASET_ID`, `EDULLM_DATASET_VERSION`, `EDULLM_DATASET_TOKENIZER` | Which published corpus that resolves to, and what tokenized it. Absent when you picked `none`. |
| `WANDB_PROJECT`, `WANDB_ENTITY` | Read by the W&B client directly |

The three dataset variables come from the registry entry behind the field you picked, not
from anything you typed, which is what makes it impossible for the record and the run to
name different corpora.

## Looking at a run, and stopping one

[Look at a run, or stop it](../../actions/workflows/cancel-run.yml) answers both. Give it a
run id and press the button: it reports what the run is doing — queued, running, finished,
why it is not running if it is not, its exit code, and the name of its CloudWatch log
stream. Nothing changes unless you tick **stop**.

> **Not live yet.** It assumes an identity of its own, and creating that identity needs an
> administrative credential rather than the one CI holds, so the stack behind it has not been
> applied. Press it today and it refuses, naming the missing stack. Until it is applied, ask
> an admin: they can read any run — status, reason, exit code, the command it was given and
> the environment it ran with — through **Deploy: Batch execution estate**, which takes a run
> id in its `describe_run` field.

That is the one to reach for when a run seems stuck. Batch says `RUNNABLE` both for a job
waiting on a machine and for one asking for more of a machine than exists, and the reason
beside the status is what tells them apart.

To stop a run, tick **stop** and give a reason. You can stop your own runs; admins can stop
anyone's. Looking is not restricted — you can look at anybody's.

The reason is recorded on the termination, so the run's history says it was cancelled
rather than that it failed. Anything already written to the output prefix stays, including
checkpoints — so stopping a run to fix its command and resubmitting does not throw away the
hours it already did.

A run that has already finished cannot be stopped, and the workflow says so rather than
reporting a failure. It can still be looked at for a few days, until Batch stops listing it.

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
