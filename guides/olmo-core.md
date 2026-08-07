# Training a model with OLMo-core

Access, the submission form, the corpora, the run id and how to stop a run are in [`the-platform.md`](the-platform.md). This guide is the training half.

## What you are actually writing

Usually nothing. There are three levels and most experiments never leave the first.

| You want to change | You write | Where it goes |
| --- | --- | --- |
| Model size, learning rate, batch size, sequence length, step count, seed, corpus | Nothing beyond flags on the command | The `command` field on the form |
| Something flags cannot express: a new callback, a different data mix, a custom evaluation | One Python file on your branch | Anywhere in the repo. Point the command at it |
| The training library's own behaviour | A pull request against `src/olmo_core/`, with a `CHANGELOG.md` entry | Reviewed like any other change |

**Level one is bigger than it sounds.** `.edullm/train_on_corpus.py` already takes `--model-factory`, `--learning-rate`, `--steps`, `--sequence-length`, `--global-batch-size`, `--rank-microbatch-size`, `--warmup-steps`, `--save-interval` and `--data-seed`, and anything after those is a dot-notation override into the config. A hyperparameter sweep is four submissions with different flags and no new code.

**Level two starts from a copy, not from scratch.** Copy `.edullm/train_on_corpus.py` into your branch and edit it. Every part of OLMo-core is a config dataclass with a `build()` method, so a script assembles configs and calls `build()` on them. You add a callback with `TrainerConfig.with_callback("name", YourCallback())`, and swappable components register themselves under a name with a decorator like `@SequenceMixerConfig.register("my_mixer")`. You almost never have to edit the library to change what a run does.

Whatever you write has to take the run name as its first argument, resolve its data from the dataset variables rather than a hard-coded path, and save to `$EDULLM_CHECKPOINT_DIR`.

### Context to give an assistant

Working in this repo with Cursor or Claude, paste this first. It is the set of constraints nothing in OLMo-core's own documentation knows about:

```text
I am writing a training script to run on the eduLLM platform, in the OLMo-core repo.

How my job runs: the platform builds my commit into a container image and runs ONE
command inside it. There is no shell, the command is exec'd exactly as written, and
nothing is appended. Starting more than one process is my job, not the platform's.

Already set in the container:
  EDULLM_RUN_ID              this run's id; use it as the run name
  EDULLM_CHECKPOINT_DIR      an s3:// prefix; checkpoints MUST go here
  EDULLM_OUTPUT_PREFIX       an s3:// prefix for everything else
  EDULLM_DATASET_ID          which corpus was chosen on the form
  EDULLM_DATASET_VERSION     its version
  EDULLM_DATASET_TOKENIZER   the tokenizer it was written with
  WANDB_PROJECT, WANDB_ENTITY

Rules my script has to satisfy:
  - Resolve data through edullm_data.read using those three dataset variables.
    Never hard-code a corpus path or a tokenizer.
  - Save to $EDULLM_CHECKPOINT_DIR, and put it on the command line rather than
    only inside the program. The platform reads the command text to check a run
    that promised a checkpoint will write one, and cannot see inside my code.
    The OLMo-core default is /tmp, a machine that stops existing; a run that takes
    it exits zero having saved nothing.
  - Pass the dtype and byte order from the manifest explicitly. Inferring either
    decodes the corpus wrongly without raising.
  - trainer.callbacks.checkpointer.max_checkpoints=null, or a prune deletes a
    checkpoint the workload role may not delete and the run dies.
  - Disable lm_evaluator and downstream_evaluator; both fail at trainer construction.
  - Set trainer.max_duration explicitly.
  - On a multi-GPU machine, start one process per device with
    python -m torch.distributed.run --nproc-per-node=N --standalone.

Start from .edullm/train_on_corpus.py, not src/examples/llm/train.py. The example
hard-codes a C4 shard and the GPT-2 tokenizer and would ignore the corpus I picked.

OLMo-core composes config dataclasses with .build(). Prefer assembling configs in my
own script over editing anything under src/olmo_core/.
```

## Prerequisites

- [ ] Your branch is named `edullm/…`. This is the one people miss, see below
- [ ] The build workflow has gone green on your commit
- [ ] The image has finished its security scan, a few minutes *after* the build goes green
- [ ] You have the full commit SHA (`git rev-parse HEAD`)
- [ ] You have a Weights and Biases project to report into

## Building your image

**Nothing runs from source.** Your commit becomes a container image in ECR and the run names that image, so having a built image is a prerequisite rather than a step on the form.

```bash
git switch -c edullm/my-experiment
git push -u origin edullm/my-experiment
git rev-parse HEAD                      # the commit you put on the form
```

Watch **Build eduLLM research image** on OLMo-core's Actions tab.

| You push to | Result |
| --- | --- |
| `edullm/**` | Image built and published |
| `main` | Image built and published |
| **Anything else** | **No image, and nothing warns you.** The push succeeds and the checks go green |
| Manual dispatch | Image built |

A branch outside those names fails later, at submission, with `commit <sha> has no image published from it`. The refusal is clear; the cost is that it arrives long after the push, by which time a branch with no image looks exactly like a branch with one.

| | |
| --- | --- |
| Before you can submit | The registry scans every published image, which finishes a few minutes after the push. See below |
| What the build checks | `ruff check .` over your checkout, not your tests |
| Tag | First twelve characters of the commit. ECR refuses to overwrite a tag, so one commit is one image |
| Digest | Printed in the build's step summary. Leave `image_digest` blank and it is resolved from your commit |
| Re-running a build | Resumes onto the existing image rather than failing |

**A green build is not the last step, and this one has already cost an afternoon.** The registry scans every image it accepts, and a submission naming an image whose scan has not finished is routed to an admin instead of to your team lead. Measured across the fourteen most recent images, the scan finished a median of about one minute forty seconds after the push, and the slowest of them took 5m41s. Watch for it under **Vulnerabilities** on the package in the registry, and give it two minutes rather than ten.

The scan is the short part of the wait. From pushing a commit to having a submittable image is eight to eleven minutes: six to eight of build for a 4.4 to 4.6 GB image, about a minute of gate jobs, then the scan. One measured example, run `30755029486`, started 15:44:27Z, image pushed 15:53:08Z, scan complete 15:54:45Z, 10m18s end to end.

If you submit inside that window the run becomes an exception and waits on an admin, and the summary on the run page says the scan was still in progress rather than naming any vulnerability. Until 2026-08-05 this was refused outright and nobody could release it. It is now releasable, and the honest thing to do is not to ask: wait a few minutes and resubmit the same commit, because the scan will have finished and the run goes back to your team lead. Nothing else about the submission needs changing.

## Workload profiles

A profile is a policy preset. It fixes how long a run may take, how many attempts it may have and whether it promises a checkpoint. It does not pick your machine.

| Profile | Limits | Use for |
| --- | --- | --- |
| `olmo-core-check` | 1h, 1 attempt | **Start here.** Proves the path works |
| `olmo-core-train` | 24h, 2 attempts, checkpoint every 30 min | Real training |

There were four of these and two of them were the other two on a different machine. `compute_profile` is a required field beside this one, so pick the machine there.

| You are | Pick | Costs |
| --- | --- | --- |
| Proving the path works | `olmo-core-check` on `cpu-32vcpu` | $1.43/hr |
| Checking your code sees a GPU | `olmo-core-check` on `gpu-1xa10g` | $1.01/hr |
| Training on one device | `olmo-core-train` on `gpu-1xa10g` | $1.01/hr |
| Training on four | `olmo-core-train` on `gpu-4xa10g` | $5.67/hr |

Twenty-four hours is the workload profile's own ceiling and it is what Batch enforces on each attempt. It was twelve, and a second ceiling in policy sent anything longer to an admin, so every sweep that ran overnight needed one. Both of those are gone. What decides who releases a run now is its worst-case total and nothing else, and [approval](the-platform.md#approval) has the figure. `olmo-core-check` carries no checkpoint contract because a twenty-step run has nothing worth resuming.

**The check and the training preset differ in what they promise, not in what they cost.** A one-hour check on eight A100s bills $21.96 an hour, comes to $21.96, and starts on its own, because what routes a run is the total rather than the rate. The same machine under `olmo-core-train` is $1,053.96 over twenty-four hours at two attempts and goes to a lead. What the approver is shown, when there is one, is the machine and its rate, which is the whole of what stands between a mistyped dropdown and a bill.

## Running a training job

Set `dataset_release` to the corpus you want, then:

```
bash -lc 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR" --steps 4000'
```

That is the whole thing. It opens the corpus you picked, reads it at the width the corpus was written at, checkpoints where a retry can find them, and reports to your W&B project.

| | |
| --- | --- |
| `bash -lc` is required | The container runs your command directly, with no shell. Without it, `$EDULLM_RUN_ID` arrives as fourteen literal characters |
| Config overrides | Append them: `--model-factory olmo2_1B optim.lr=3e-4` |
| `--dry-run` | Resolves the corpus, prints the config, trains nothing. The cheapest way to find a bad flag |
| `--save-interval` | Counts steps, defaults to 100. `olmo-core-train` promises a checkpoint every 30 minutes, so stay under that. At `200` a 190M model on one A10G saves about every 23 minutes, writing 3.2 GB in roughly 40 seconds on its own thread |

**A retry only fires for a lost machine.** Batch starts a second attempt with the same run id, so the same `$EDULLM_CHECKPOINT_DIR`, and `Trainer.fit()` resumes on its own. A crash in your own code exits instead, because the same traceback twice costs the budget twice.

## One big card

Six profiles put one card under a run. Four of them can be started, and they differ only in how much fits on the card.

| Compute profile | Device | Memory | Rate | Placing |
| --- | --- | --- | --- | --- |
| `gpu-1xt4` | 1 x T4 | 16,384 MiB | $0.526/hr | reliably |
| `gpu-1xl4` | 1 x L4 | 22,888 MiB | $0.8048/hr | reliably |
| `gpu-1xa10g` | 1 x A10G | 22,888 MiB | $1.006/hr | reliably |
| `gpu-1xa10g-sagemaker` | 1 x A10G | 22,888 MiB | $1.515/hr | **refused** |
| `gpu-1xl40s` | 1 x L40S | 45,776 MiB | $1.861/hr | after a wait |
| `gpu-1xh100` | 1 x H100 | 81,920 MiB | $6.88/hr | **refused** |

`gpu-1xl40s` is the largest single card this account can start, and it is the answer when a recipe you were given fits on one A100 or one H100 elsewhere. A100 is sold only as the eight-card `p4d.24xlarge`, so there is no `gpu-1xa100` to ask for and there never will be. One H100 does exist as `p5.4xlarge` and the catalogue registers `gpu-1xh100` on it, but EC2 has never sold this account a p5 of any size, so it reads `provisioned: false` and asking for it is refused. 45,776 MiB against 81,920 is the trade, and it is usually cheaper than reshaping the recipe. `gpu-1xa10g-sagemaker` is the same A10G at half again the rate and nothing was ever built for it.

## Multi-GPU jobs

| Compute profile | Devices | Memory | Rate | Placing |
| --- | --- | --- | --- | --- |
| `gpu-4xt4` | 4 x T4 | 65,536 MiB | $3.912/hr | reliably |
| `gpu-4xl4` | 4 x L4 | 91,552 MiB | $4.6016/hr | unreliably |
| `gpu-4xa10g` | 4 x A10G | 91,552 MiB | $5.672/hr | after a wait |
| `gpu-8xt4` | 8 x T4 | 131,072 MiB | $7.824/hr | reliably |
| `gpu-4xl40s` | 4 x L40S | 183,104 MiB | $10.4926/hr | after a wait |
| `gpu-8xl4` | 8 x L4 | 183,104 MiB | $13.3504/hr | unreliably |
| `gpu-8xa10g` | 8 x A10G | 183,104 MiB | $16.288/hr | after a wait |
| `gpu-8xa100` | 8 x A100 | 327,680 MiB | $21.9576/hr | after a wait |
| `gpu-8xl40s` | 8 x L40S | 366,208 MiB | $30.1312/hr | unreliably |
| `gpu-8xh100` | 8 x H100 | 655,360 MiB | $55.04/hr | **refused** |

**Both tables are written from configuration rather than typed here.** The card and the memory come from `config/accelerators.yaml`, which holds one `aws ec2 describe-instance-types` answer for all seventeen shapes; the rate from `config/workload-catalog.yaml`; the last column from `config/capacity.yaml`. `uv run python tools/render_profile_table.py` prints the same figures in one table, and a test in this repository fails if a row here disagrees with any of the three files.

Memory is the total across the devices and it is the column to read first, because it decides whether the job runs at all. **It is MiB rather than the GB the card is sold as, and the difference is not pedantry.** An A10G is sold as 24 GB and reports 22,888 MiB, which is the same quantity counted honestly; somebody who sizes a batch against 24 GiB has overcommitted the card by more than a gigabyte before the run starts, and what they get for it is a CUDA out-of-memory some way into the first epoch. The figure is also what the hardware carries rather than what your process can have: the CUDA context, the allocator and the framework come out of it first.

The last column is `places`. `reliably` and `unreliably` are usually a probe asking EC2 for one instance; `after a wait` is always a queue that watched real jobs sit in `RUNNABLE`, and `config/capacity.yaml` records what the wait was for each. **`refused` is a different kind of answer from a bad one:** those three shapes have no compute environment at all, so `edullm check` turns them away by name rather than warning you about a queue. For anything that is not `reliably`, `check` prints a line about it above the cost.

**`gpu-8xh100` is priced and cannot be started, and this is the row that catches people.** EC2 has never once sold this account a p5 of any size: `config/capacity.yaml` records 7,654 capacity refusals against `p5.48xlarge` and `p5en.48xlarge` in a single day, a median of a tenth of a second apart, and not one instance out of any of them. So the catalogue reads `provisioned: false` and a submission naming it is refused with `unprovisioned_compute_profile` before anything is dispatched. Eight A100s is the substitution to reach for, at 327,680 MiB against 655,360. `gpu-1xh100` is refused for the same reason and is why the single-card table above stops, in practice, at the L40S.

**It stays in the catalogue on purpose, and the refusal is why.** Withdrawing it would move you from `unprovisioned_compute_profile`, which says the shape is real and priced and has nothing behind it and then lists what does, to `unregistered_compute_profile`, whose whole detail is the name you typed. That is the refusal a misspelling earns, and getting it for a correctly spelled shape sends you looking for a typo you did not make.

**No shape in either table above needs an admin, and no rate sends a run anywhere.** This section used to say that everything at or above `gpu-8xa100` went to an admin, because the platform routed every profile over $20 an hour that way whatever the run cost in total. Policy v5 deleted that ceiling and no rate routes anything now. Measured on 2026-08-06, a one-hour single-attempt check on `gpu-8xa100` at $21.96 an hour and one on `gpu-8xl40s` at $30.13 an hour both come back `automatic`, which is released by nobody at all. What routes a run is its worst-case total, and [approval](the-platform.md#approval) has the figure. The one thing that does reach an admin is the block-backed table below, and it is a fact about the purchase rather than about the rate.

Four more eight-card shapes exist, and they are backed by [capacity blocks](capacity-blocks.md) — dated windows somebody buys in advance — rather than by a standing queue. They are listed here so that a researcher who needs more than 640 GB knows the route exists and asks, rather than concluding the platform tops out at `gpu-8xh100`.

Three of the four are refused outright: `edullm check` answers `unprovisioned_compute_profile` until a block has been bought and wired up. **`gpu-8xb200` is the exception and the column says so rather than `yes`.** You can name it and it will be admitted, because its execution target is in place — but the queue behind it is created by a purchase and deleted when that window closes, so a submission made when no block is live fails at Batch. It fails before a machine starts and before any money moves, which is why the shape is nameable at all: getting it wired up is a Lambda release, and doing that inside a paid window would spend the thing being bought.

| Compute profile | Devices | Memory | Rate | Placing |
| --- | --- | --- | --- | --- |
| `gpu-8xa100-80gb` | 8 x A100 | 655,360 MiB | $17.712/hr | **refused** |
| `gpu-8xh200` | 8 x H200 | 1,155,072 MiB | $54.92/hr | **refused** |
| `gpu-8xb200` | 8 x B200 | 1,466,872 MiB | $98.84/hr | unreliably |
| `gpu-8xb300` | 8 x B300 | 2,200,320 MiB | $112.32/hr | **refused** |

Those rates are what a reserved hour costs rather than an on-demand one, because on-demand is not available for any of them, and they are the only rates in this guide that are not a Price List figure. The launcher rule below applies unchanged: all four are eight-device machines and need eight processes.

**These four are the only shapes on the platform that need a platform admin rather than a team lead**, and it is the purchase rather than the price that sends them there. A block is charged upfront, in full, and cannot be cancelled, so `classify_request` answers `exception` for any run naming one whatever the total comes to. Nothing else in either table above reaches that class. [Approval](the-platform.md#approval) has the rest of it.

If you want one, file `edullm ask --kind capacity-block`. Expect weeks rather than days — the earliest offering is usually two to four weeks out — and expect to be asked for your peak GPU memory, your hours, and whether you have tested that your job resumes from a checkpoint.

**Your command must start one process per device.** Nothing wraps what you type, so the launcher goes in the command:

```
bash -lc 'python -m torch.distributed.run --nproc-per-node=4 --standalone .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR" --steps 4000 train_module.dp_config.param_dtype=float32'
```

Set `--nproc-per-node` to the device count of the shape you picked. `torchrun`, `accelerate launch`, `deepspeed`, `mpirun` and `srun` all work.

**The dtype override is on that line because of which of the ten rows above you can actually get.** `config/capacity.yaml`, measured on 2026-08-04 by asking EC2 for one instance of each, records `gpu-4xt4` and `gpu-8xt4` as the only multi-card shapes that place at an instant. Four more arrive after a wait. Both instant survivors are T4, and **T4 has no bfloat16**; the section below is about that. `.edullm/train_on_corpus.py` builds its data-parallel config in bfloat16, so without the override that command is a bfloat16 run on hardware with no bfloat16, and it dies after the machine is billed. Writing the dtype on the command rather than leaving it in code is also what lets the check below see it, so a wrong answer here is refused at submission instead of on the device. Single-card work is unaffected: `gpu-1xa10g` and `gpu-1xl4` both place and both have the format, which is why the command at the top of this guide carries no dtype.

**That override is a fact about `train_on_corpus.py` and not about the platform.** Most of the `edullm/**` branches launch a training entrypoint of their own, and most of those exit 2 on the line above. The table in the bfloat16 section below says which take it.

Leaving the launcher out used to be free and silent: the run trained on one device, billed for four, and exited zero. That is $136 for a quarter of the work over twenty-four hours. It is now refused at submission, and the refusal prints the corrected command. The same check catches too few ranks, too many ranks, and `torchrun` with no `--nproc-per-node` at all.

**To run one process on a multi-GPU machine deliberately**, for a benchmark or a memory profile, waive the check. `olmo-core-train` also declares a checkpoint contract, so a benchmark under it is waiving both, which is why two tokens appear. Each is recorded on the manifest and shown to the approver:

```
bash -lc 'EDULLM_LAUNCH_CHECK=waived EDULLM_CHECKPOINT_CHECK=waived python benchmarks/memory.py --batch 64'
```

## The checkpoint refusal

`olmo-core-train` declares a checkpoint contract, which is what its second attempt is granted on. **A command under it has to expand `$EDULLM_CHECKPOINT_DIR`, or the submission is refused when it compiles**, before a lead is asked.

Keep `--save-folder "$EDULLM_CHECKPOINT_DIR"` on the line even though `train_on_corpus.py` already defaults to it. The check reads your command text and cannot see inside your program, so writing the flag costs nothing at runtime and puts the save folder in the manifest where the approver can see it.

| Satisfies the check | Does not |
| --- | --- |
| `"$EDULLM_CHECKPOINT_DIR"` | Inside single quotes, where no shell expands it |
| `${EDULLM_CHECKPOINT_DIR}` | Behind a backslash, or after a `#` |
| `${EDULLM_CHECKPOINT_DIR}/step` | A command with no shell in front of it |

The refusal names which of those it found. The unexpanded forms reach your program as the literal text `$EDULLM_CHECKPOINT_DIR`, and OLMo-core creates a directory by that name rather than failing, which is why they count as absent.

**This is what the check exists to stop.** A trainer that is not told where to save uses its own default, `/tmp` for the OLMo-core example, on a machine that stops existing. The run trains for a day, writes checkpoints nobody can reach, exits zero, and is recorded as an unqualified success. One run in this account is in that state and nothing is recoverable from it.

**If your run genuinely does not save where the platform looks**, because it derives its own path or is a throwaway nobody will resume, waive it:

```
bash -lc 'EDULLM_CHECKPOINT_CHECK=waived python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --dry-run'
```

Same convention as the launcher waiver, deliberately the same spelling. What it does not do is make a retry work: a waived run that loses its machine starts from nothing.

## The bfloat16 refusal

**The three T4 shapes have no bfloat16: `gpu-1xt4`, `gpu-4xt4` and `gpu-8xt4`.** T4 is a Turing card, which is the one NVIDIA generation with tensor cores and without the format. Every other card in the table above is Ampere, Ada or Hopper and has it.

This matters more than it reads, because `gpu-4xt4` and `gpu-8xt4` are currently the only multi-card shapes this account can get at all. Scarcity pushes every multi-card run onto exactly the card that cannot do the format multi-card work usually wants, and there is no second shape to move it to.

**A command that asks for bfloat16 on one of those three is refused when it compiles**, before a lead is asked. The refusal names the shape, the card and the words of your command it matched:

```
train_module.dp_config.param_dtype=bfloat16   # refused on gpu-8xt4
--dtype bfloat16   --torch_dtype bfloat16   --mixed_precision bf16   --bf16
```

**It reads your command text and nothing else, and the gap is large enough to state plainly.** `.edullm/train_on_corpus.py` builds its data-parallel config in bfloat16 by default, so the getting-started command at the top of this guide **is a bfloat16 run that carries no bfloat16 token**, and this check will not refuse it on a T4. The same is true of a dtype set in a config file inside the image or read from a shell variable.

So treat the refusal as a backstop rather than a guarantee. If you are picking a T4 shape, the question to ask is what your program does, not what your command says:

| If your run | On a T4 shape |
| --- | --- |
| Passes a bfloat16 flag on the command line | Refused at submission |
| Runs `train_on_corpus.py` | Accepted here, then refused inside the container in the first seconds at exit 73, before the process group or any GPU work |
| Sets bfloat16 in code any other way | **Accepted, and it will fail on the device** |
| Uses fp16 with loss scaling, or fp32 | Fine, and what a T4 is for |

**On a T4 the answer is float32, and float16 is a trap.** `--param-dtype float16` clears both this refusal and the one inside the container, because a T4 does have fp16 in hardware, but OLMo-core ships no gradient scaler: that route is fp16 with no loss scaling, so small gradients underflow to zero and the run trains worse than it looks. float32 is the one that is simply correct. How you ask for it depends on what your command runs.

### Which entrypoint takes which spelling

Read against the eight entrypoints the 28 `edullm/**` branches carry, on 2026-08-05. Three of the eight take a dtype off the command line and five exit 2 on one, so the line the section above prints is a remedy for three of them and a dead end for the rest.

| Entrypoint | `--param-dtype float32` | `train_module.dp_config.param_dtype=float32` |
| --- | --- | --- |
| `.edullm/train_on_corpus.py` | yes | yes |
| `.edullm/train_liv_arm.py` | no such flag | yes |
| `.edullm/probe_group_words.py` | passed through | passed through, and `train_probe` is not an OLMo-core config |
| `.edullm/curriculum_entrypoint.py` | **exit 2** | **exit 2** |
| `.edullm/mixlaw_entrypoint.py` | **exit 2** | **exit 2** |
| `.edullm/skillit_entrypoint.py` | **exit 2** | **exit 2** |
| `.edullm/token_selection_entrypoint.py` | **exit 2** | **exit 2** |
| `.edullm/train_skillit_370m.py` | **exit 2** | **exit 2** |

`train_on_corpus.py` and `train_liv_arm.py` call `parse_known_args` and merge whatever is left over into the OLMo-core config, which is what makes a dotted override work. The five below them call `parse_args`, which rejects any argument the parser does not declare, and none of them declares a dtype. `curriculum_entrypoint.py` would still not take one if it did: it re-launches itself under `torchrun` with a fixed list of flags to forward.

**If your entrypoint is one of the five, the command line is not the way and there is no waiver.** Three of them write `param_dtype=DType.bfloat16` into the training config in code, and the other two launch one that does. So on a T4 shape a run under any of them dies on the first kernel that needs the format, whatever your command says, and the submission check above cannot see it coming because there is no bfloat16 token to read. Two things work. Pick a shape whose card has bfloat16, which is every row in the multi-GPU table except the three T4 ones. Or change the dtype in your branch's copy of the entrypoint and submit that commit, which is a code change to your own repository rather than a platform one.

**The second row of the table above is [OLMo-core#49](https://github.com/edu-llm/OLMo-core/pull/49), merged on 2026-08-05.** It does not reach a run of yours until your own commit carries it, because your image is built from the commit you declare. Every `edullm/**` branch has its own copy of `.edullm/train_on_corpus.py`, and merging that one file does not touch a separate entrypoint. So the in-container check arrives on your branch when you merge `main` into it, and not before, and it protects only the branches that run `train_on_corpus.py`.

There is no waiver. The other two checks have one because the waived run still works; a waived bfloat16 run on a T4 does not.

## Required configuration

`.edullm/train_on_corpus.py` already sets all of these. You need them if you run the OLMo-core example directly or write your own program.

| Setting | Value | Why |
| --- | --- | --- |
| `--save-folder` | `"$EDULLM_CHECKPOINT_DIR"` | Defaults to `/tmp`, which is local disk on a machine that stops existing. A twenty-four-hour run writes checkpoints nobody can reach, exits zero, and is **recorded as a success** |
| `trainer.callbacks.checkpointer.max_checkpoints` | `null` | OLMo-core keeps three and deletes the rest. The prune deletes `.metadata.json` first and the workload role is denied that key by name, so the run dies with `OLMoNetworkError`. At `save_interval=200` that is step 600, about an hour in |
| `trainer.callbacks.checkpointer.ephemeral_save_interval` | `null` | Must be below `save_interval` or OLMo-core refuses the config in the first seconds |
| `trainer.callbacks.lm_evaluator.enabled` | `false` | Reads a C4 validation shard whose `.csv.gz` index was never published, so the URL 404s |
| `trainer.callbacks.downstream_evaluator.enabled` | `false` | Scores HellaSwag through `ai2-olmo-eval`, which the training image does not install |
| `trainer.max_duration` | Set it | Defaults to one epoch, which may be far more or far less than twenty-four hours |
| `train_module.compile_model` | `false`, if needed | `torch.compile` needs a C compiler. Recent images have one; older commits do not |

Both evaluators fail while the trainer is being built, before the first step, so disabling one sends you back to a crash seconds later with the obvious fix already applied.

Your whole command and environment must fit in 8,192 bytes, which is Batch's limit. A long program belongs in your repository.

**The example is not a shortcut.** `src/examples/llm/train.py` has the C4 shard and the GPT-2 tokenizer written into it, so picking `regmix-10b-v1` on the form and running the example gives you a loss curve for a corpus that was never opened. Run it only with everything above applied:

```
bash -lc 'python src/examples/llm/train.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR" --work-dir /tmp/dc train_module.compile_model=false trainer.max_duration.value=4000 trainer.max_duration.unit=steps trainer.callbacks.checkpointer.save_interval=200 trainer.callbacks.checkpointer.ephemeral_save_interval=null trainer.callbacks.checkpointer.max_checkpoints=null trainer.callbacks.lm_evaluator.enabled=false trainer.callbacks.downstream_evaluator.enabled=false'
```

## Output locations

| Variable | Path | Contents |
| --- | --- | --- |
| `$EDULLM_CHECKPOINT_DIR` | `teams/{team}/runs/{run id}/checkpoints/` | Checkpoints a retry can resume from |
| `$EDULLM_OUTPUT_PREFIX` | `teams/{team}/runs/{run id}/` | Everything else the run writes |

Both are handed to the container rather than worked out by it, which is what keeps the location in the record and the location on disk from being two different answers. The full variable list is in [`the-platform.md`](the-platform.md).
