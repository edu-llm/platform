# Using the platform

Everything true whichever repository you work in. For what to actually run, see [training a model](olmo-core.md), [running an evaluation](olmo-eval-full.md) or [validating a corpus](edullm-data.md). If you would rather not open a browser at all, skip to [from a terminal](#from-a-terminal).

## Your first run

Open [Submit a run](https://github.com/edu-llm/platform/actions/workflows/submit-run.yml), press **Run workflow**, and fill in six fields. Everything else has a correct default.

| Field | Value |
| --- | --- |
| `repository` | `OLMo-core` |
| `commit_sha` | `main` |
| `workload_profile` | `olmo-core-check` |
| `compute_profile` | `cpu-32vcpu` |
| `team` | `scratch` |
| `experiment` | Anything, such as `onboarding` |
| `wandb_project` | Your Weights and Biases project |

**Type `main` and do not go looking for a branch.** The field takes a branch, a tag or a full commit, but your code has to have been built into a container image before a run can name it, and on your first run you have not built anything. `main` always has one. When you come to run your own work, *Building your image* in your repository's guide is the step that gets you there.

Leave `command` alone. Its default runs a one-line check needing no code of your own, which is the point of a first run: you are testing the path from the form to a machine and back.

Use `scratch` the first time. It is the bin for work nobody intends to keep, so a first run costs no research group anything.

## Access

Two lists, and you need both. They are separate systems, which is the most confusing thing about getting started here.

| List | What it gates | If you are missing |
| --- | --- | --- |
| `team-members` GitHub team | Write access, and therefore whether the **Run workflow** button exists at all | The page has no button. Nothing you type will help |
| `members` in `config/organization.yaml` | Admission | Your submission is refused when it compiles, before any lead is asked, naming the file |
| A W&B account on the `eduLLM` team, recorded on the roster | Attribution only | The run works and logs as the platform rather than as you. Nothing warns you |

Ask for all three through the [access request](https://github.com/edu-llm/platform/issues/new?template=access-request.yml) template.

## The submission form

| Field | What it decides |
| --- | --- |
| `repository`, `commit_sha` | What code runs. The commit must already have an image. See *Building your image* in your repository's guide |
| `workload_profile` | The time limit, the retry limit and the checkpoint contract, together. Not the machine |
| `compute_profile` | The machine. Required, and the field that decides what your run costs |
| `team` | Who reviews it, and the S3 prefix your output lands under. Closed dropdown: `platform`, `memory-split`, `input-core`, `pre-training`, `post-training`, `data-prep`, `eval-inference`, `scratch` |
| `experiment` | Groups related runs, such as a sweep, an ablation or a week. Free text, registers nothing, and what the cost view groups by |
| `dataset_release` | The corpus your job reads. `none` if it reads none |
| `command` | What the container runs. No shell: the first word must be a program name, and the line must not be wrapped in outer quotes |
| `wandb_project` | Where the run reports |
| `image_digest`, `maximum_runtime_hours`, `maximum_attempts`, the fan-out fields | Advanced. Leave them. The image comes from your commit, and the two bounds come from the workload profile |

## Choosing a machine

`compute_profile` is a closed dropdown of every shape with a queue behind it, and it is the most expensive field on the form by two orders of magnitude. The range runs from $0.53 an hour to $55.04. Nothing infers it from what you are running, and nothing refuses a small job on a large machine.

| You are | Pick |
| --- | --- |
| Doing anything for the first time | `cpu-32vcpu` |
| Checking your code sees a GPU | `gpu-1xa10g` |
| Training, one device | `gpu-1xa10g` |
| Training, several devices | A `4x` or `8x` shape, and start one process per device. See your repository's guide |

**This used to say to leave the field alone, and that advice was wrong the whole time it was there.** The workload profile appeared to name a machine and the form outranked it silently, so a run labelled `olmo-core-train-1gpu` could land on eight H100s and nothing anywhere said so. The catalog no longer claims a machine and this field is required, so you are asked once rather than defaulted somewhere you did not choose.

Your image is unaffected by this field. It is built from your commit for one architecture and runs on every shape here, so picking a bigger machine changes what you are billed and nothing about what runs.

## Approval

**A run estimated under 5 USD that asks for under an hour starts on its own.** No lead, no wait. It is still recorded and still attributed to you, and you still have to be on the roster and running registered code. What you skip is the queue, not the checks. Both halves have to hold: four hours at 50 cents waits, and one hour at 8 USD waits. So does any fan-out, whatever it costs, because a sweep is worth a person's eyes on the total before sixty-four machines start.

Everything else waits for a person. Any of the eight team leads can release any group's run, so you are not blocked on one individual. But nobody is paged, so if a run has been waiting, ask.

If you are approving, it is not a formality. Before you release a run you are shown its cost, its machine, the team it is booked to, whether the submitter will be attributed, and whether it waived any check.

## The run id

Every run gets an id like `run_019fbce3-…`. That one string is the name of the job on AWS, the folder your output lands in, and the name of your run in Weights and Biases. It is stable across retries, and it is the only thing anyone needs from you when something goes wrong.

## Guards and waivers

The platform refuses commands that contradict the run you asked for. Each guard exists because it cost somebody a real run. Asking for four GPUs and starting one process trains on a quarter of the machine, bills for all of it, and exits zero.

Override by putting a token in the command, which records the decision rather than routing around the check:

```
bash -lc 'EDULLM_LAUNCH_CHECK=waived python benchmarks/memory.py --batch 64'
```

There are two of them today and they are spelled the same way on purpose: `EDULLM_LAUNCH_CHECK=waived` for a multi-GPU machine running one process, and `EDULLM_CHECKPOINT_CHECK=waived` for a run that promised a checkpoint and saves somewhere the platform will not look. A command can carry both.

A waiver lands in the run's manifest and the approving lead is told which check was waived. What it does not do is make the underlying thing work. A waived checkpoint run that loses its machine starts from nothing.

## The corpora

| `dataset_release` | Train tokens | Objects |
| --- | --- | --- |
| `math-frontload-100m-v1` | 0.1B | 3 |
| `formal-proof-premises-500m-v2` | 0.5B | 12 |
| `fineweb-edu-1b-v6` | 1.0B | 4 |
| `fineweb2-phase0-equal-bpe-2b-v1` | 2.0B | 12 |
| `fineweb2-phase0-equal-superbpe-2b-v1` | 2.0B | 12 |
| `refhq-regmix-5p5b-v2` | 5.5B | 24 |
| `regmix-10b-v1` | 10.0B | 41 |
| `fineweb2-unimax-bpe-20b-v1` | 21.0B | 166 |
| `fineweb2-unimax-superbpe-20b-v1` | 18.9B | 151 |
| `olmo-original-30b-v1` | 31.3B | 120 |
| `olmo-127b-v1` | 126.5B | 474 |
| `olmo-150b-dolma2-v1` | 157.2B | 6,851 |

All are frozen and nothing you run can write to them. Most use the dolma2 tokenizer, which the training image has built in. The exceptions are: `fineweb-edu-1b-v6` (SmolLM2 from Hub), `formal-proof-premises-500m-v2` (vendored Qwen2.5 from Hub), and the four `fineweb2-*` Plan B releases (gigatoken BPE / SuperBPE, configured in the image with no Hub fetch). Hub outages refuse only the SmolLM2 and Qwen corpora; Plan B and dolma2 start without it.

Two more things about `formal-proof-premises-500m-v2` are worth knowing before you report a number from it. Its shards are `uint32` rather than the usual `uint16`, which the loader must take from the manifest and never infer; and ATP/TPTP traces carry most of its token mass, so a single loss over the whole corpus is mostly measuring two of its six sources. The Plan B `fineweb2-*` shards are also `uint32` (100k-vocab gigatoken).

**More corpora are published than are offered here, and the reason is never the corpus.** `lean4-mathlib-bytes-v3` and `math-memory-full-v1` are sealed, frozen and readable, and they are tokenized with raw UTF-8 bytes, which OLMo-core has no tokenizer for. They stay in the registry and off this list until it does, because a run that resolved one would reach a container that cannot build a model for the tokens it just read. `fineweb-edu-1b-v6` was in that state until somebody wrote the one line naming its tokenizer, which is the difference between a missing upstream feature and a job nobody had done.

**Size costs nothing up front.** Shards are memory-mapped from S3 as the loader reaches them, so a 157B corpus starts as quickly as a 5B one and reads only what your step count needs. Pick by what you are training, not by what you can afford to download.

## Container environment

Set for you. You never supply these, and a value you set yourself is one the record will disagree with.

| Variable | What it holds |
| --- | --- |
| `EDULLM_RUN_ID` | This run's id, stable across retries |
| `EDULLM_CHECKPOINT_DIR` | Where checkpoints go |
| `EDULLM_OUTPUT_PREFIX` | Where everything else this run writes goes |
| `EDULLM_TEAM` | The team you claimed |
| `EDULLM_COMMIT_SHA` | The commit that was resolved and built |
| `EDULLM_DATASET_RELEASE` | The dataset you named |
| `EDULLM_DATASET_ID`, `EDULLM_DATASET_VERSION`, `EDULLM_DATASET_TOKENIZER` | Which published corpus that resolves to, and what tokenised it. Absent when you picked `none` |
| `AWS_BATCH_JOB_ARRAY_INDEX` | Which cell of a fan-out this is, counting from zero. Set by Batch, and only on a fan-out |
| `EDULLM_FANOUT_INDEX_PARAMETER` | What that index varies, copied from what you put on the form. Only on a fan-out |
| `WANDB_PROJECT`, `WANDB_ENTITY` | Read by the W&B client directly |

The three dataset variables come from the registry entry behind the field you picked rather than from anything you typed.

## Fan-out

A fan-out runs your command once per cell. Give the form a `fanout_size` and a `fanout_index_parameter` naming what varies, such as `seed` or `shard`.

Each cell gets its own output prefix. `EDULLM_OUTPUT_PREFIX` and `EDULLM_CHECKPOINT_DIR` already carry the `cell-<index>/` segment by the time your program starts, so a command that writes where it is told needs no change to be safe in a fan-out. A single run is unaffected and keeps the prefix it has always had.

```
teams/curriculum/runs/run_019fbce3-…/cell-0/
teams/curriculum/runs/run_019fbce3-…/cell-1/
```

Read `AWS_BATCH_JOB_ARRAY_INDEX` to decide what this cell should do, and `EDULLM_FANOUT_INDEX_PARAMETER` to know what you said it varies.

```
bash -lc 'python train.py --seed "$AWS_BATCH_JOB_ARRAY_INDEX" --save-folder "$EDULLM_CHECKPOINT_DIR"'
```

**How many cells run at once is not something you set.** Batch takes a size for an array job and no concurrency cap, so what bounds it is how much of the queue's capacity one cell reserves. A fan-out is priced and approved as one submission at its full size.

## Looking at a run, and stopping one

[Look at a run, or stop it](https://github.com/edu-llm/platform/actions/workflows/cancel-run.yml) does both. Nothing changes unless you tick **stop**.

| You want to | Do this |
| --- | --- |
| See your latest run | Leave `run_id` blank |
| See a specific run | Give it the run id. You can look at anybody's |
| Stop a run | Tick **stop** and give a reason. Your own, any time; admins can stop anyone's |

You get the status, why it is not running if it is not, the exit code, and the CloudWatch log stream name. Batch reports `RUNNABLE` both for a job waiting on a machine and for one asking for more machine than exists. The reason beside the status is what tells those apart. A queued job bills nothing, but nobody is watching the queue, so ask if yours has not started within an hour.

**Pressing cancel on Submit a run is not this.** That stops the workflow and leaves the job running. Come here with the run id instead.

The reason you give is recorded, so the run's history says it was cancelled rather than that it failed. Anything already written stays, checkpoints included, so stopping a run to fix its command does not throw away the hours it already did.

## From a terminal

The same loop without the Actions UI. One binary:

```
uv tool install --force git+https://github.com/edu-llm/platform
```

You need [uv](https://docs.astral.sh/uv/) and a `gh` that is logged in with `gh auth login`. Nothing else. `edullm` drives `git` and `gh` rather than holding a credential of its own, so it can do what you can do and nothing more, and there is still no AWS account anywhere in this.

Then, from a checkout of the repository you work in:

```
edullm check --experiment onboarding --dataset none --team scratch
```

**`check` is the half that happens on your laptop, and it is the one to lean on.** It writes a first `.edullm/run.yaml` if the repository has none, then prices what you are about to submit and lists every refusal. They are the same refusals admission makes, decided against the reviewed configuration your install carries. It opens no connection and answers in about a fifth of a second, so it is a thing to run while you are still editing rather than once at the end. It works on a login node with no egress.

```
worst case
  $0.526/hour x 1 node x 24h x 2 attempts x 1 cell = $25.25
  This is the ceiling, not an estimate. It is also what routes the run, so lowering
  --hours is what moves a short run under the automatic bound.

approval
  routine -> run-approval-lead

no refusals. edullm submit will dispatch this.
```

| Verb | What it does |
| --- | --- |
| `edullm check` | Prices a submission here and lists every refusal. Dispatches nothing |
| `edullm submit` | Dispatches it, then waits and prints the run id and where it is parked |
| `edullm status` | Your recent runs. Give it a run id for one of them |
| `edullm logs <run-id>` | The last lines that run printed |
| `edullm cancel <run-id> --reason ...` | Stops it. The reason is required, and is recorded |

The flags are the fields the form asks for, and `check` and `submit` take the same ones. The command, the workload profile and a suggested machine are properties of the code, so they live in `.edullm/run.yaml` and travel with it in git; what a run costs today is typed on the command line, because one commit run by two people belongs to two teams.

`status`, `logs` and `cancel` reach AWS, and the only identity allowed to read a Batch job lives in `cancel-run.yml`, so those three dispatch that workflow and wait for a runner. Tens of seconds, not a moment. `check` and `submit` do not.

## Keeping edullm current

Run the install line again. `--force` makes it idempotent, so the one line installs, upgrades and repairs.

**Do not reach for `uv tool upgrade`.** For a tool installed from git it answers `Nothing to upgrade` whatever state your install is in, and `--reinstall` does not change that, so the obvious command tells you that you are current when you are months behind.

The reviewed configuration travels inside the install, which is what stops a config change bricking every `edullm` in the field, and means an old install is checking against an old copy. `edullm submit` asks for the current release before it dispatches and says so if yours is not it. It never refuses on that: a release is cut most days, so being a little behind is the normal state, and admission re-derives every verdict from inside AWS regardless.

`edullm --version` prints the version and the commit it was built from. That is the pair worth quoting when a refusal looks wrong.
