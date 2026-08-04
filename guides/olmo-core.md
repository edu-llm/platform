# Training a model with OLMo-core

Access, the submission form, the corpora, the run id and how to stop a run are in [`the-platform.md`](the-platform.md). This guide is the training half.

## What you are actually writing

Usually nothing. There are three levels and most experiments never leave the first.

| You want to change | You write | Where it goes |
| --- | --- | --- |
| Model size, learning rate, batch size, sequence length, step count, seed, corpus | Nothing — flags on the command | The `command` field on the form |
| Something flags cannot express: a new callback, a different data mix, a custom evaluation | One Python file on your branch | Anywhere in the repo. Point the command at it |
| The training library's own behaviour | A pull request against `src/olmo_core/`, with a `CHANGELOG.md` entry | Reviewed like any other change |

**Level one is bigger than it sounds.** `.edullm/train_on_corpus.py` already takes `--model-factory`, `--learning-rate`, `--steps`, `--sequence-length`, `--global-batch-size`, `--rank-microbatch-size`, `--warmup-steps`, `--save-interval` and `--data-seed`, and anything after those is a dot-notation override into the config. A hyperparameter sweep is four submissions with different flags and no new code.

**Level two starts from a copy, not from scratch.** Copy `.edullm/train_on_corpus.py` into your branch and edit it. Every part of OLMo-core is a config dataclass with a `build()` method, so a script assembles configs and calls `build()` on them — you add a callback with `TrainerConfig.with_callback("name", YourCallback())`, and swappable components register themselves under a name with a decorator like `@SequenceMixerConfig.register("my_mixer")`. You almost never have to edit the library to change what a run does.

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

Start from .edullm/train_on_corpus.py, not src/examples/llm/train.py — the example
hard-codes a C4 shard and the GPT-2 tokenizer and would ignore the corpus I picked.

OLMo-core composes config dataclasses with .build(). Prefer assembling configs in my
own script over editing anything under src/olmo_core/.
```

## Prerequisites

- [ ] Your branch is named `edullm/…` — this is the one people miss, see below
- [ ] The build workflow has gone green on your commit
- [ ] The image has finished its security scan — a few minutes *after* the build goes green
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
| What the build checks | `ruff check .` over your checkout — not your tests |
| Tag | First twelve characters of the commit. ECR refuses to overwrite a tag, so one commit is one image |
| Digest | Printed in the build's step summary. Leave `image_digest` blank and it is resolved from your commit |
| Re-running a build | Resumes onto the existing image rather than failing |

**A green build is not the last step, and this one has already cost an afternoon.** The registry scans every image it accepts, and a submission naming an image whose scan has not finished is refused. Measured across the fourteen most recent images, the scan finished a median of about one minute forty seconds after the push, and the slowest of them took 5m41s. Watch for it under **Vulnerabilities** on the package in the registry, and give it two minutes rather than ten.

The scan is the short part of the wait. From pushing a commit to having a submittable image is eight to eleven minutes: six to eight of build for a 4.4 to 4.6 GB image, about a minute of gate jobs, then the scan. One measured example, run `30755029486`, started 15:44:27Z, image pushed 15:53:08Z, scan complete 15:54:45Z, 10m18s end to end.

If you submit inside that window the refusal reads `image_scan_findings_unreviewed`, which says your image carries vulnerabilities nobody has signed off. That is usually not what happened — the scan had not finished, and the platform reported the wrong reason. Wait a few minutes and resubmit the same commit; nothing else about the submission needs changing.

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

Twenty-four hours is the ceiling for routine approval, not a round number: a longer run would make every training submission an exception and put a second approver in front of all of them. It was twelve, which made an exception of every sweep that ran overnight. `olmo-core-check` carries no checkpoint contract because a twenty-step run has nothing worth resuming.

**The check and the training preset differ in what they promise, not in what they cost.** A one-hour check on eight H100s is available to you and bills $55 an hour, and nothing refuses it. What the approver is shown is the machine and its rate, which is the whole of what stands between a mistyped dropdown and a bill.

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

Three single-GPU profiles exist and they differ only in how much fits on the card.

| Compute profile | Device | Memory | Cost |
| --- | --- | --- | --- |
| `gpu-1xt4` | 1 × T4 | 16 GB | $0.53/hr |
| `gpu-1xl4` | 1 × L4 | 24 GB | $0.80/hr |
| `gpu-1xa10g` | 1 × A10G | 24 GB | $1.01/hr |
| `gpu-1xl40s` | 1 × L40S | 48 GB | $1.86/hr |

`gpu-1xl40s` is the largest single card this account can start, and it is the answer when a recipe you were given fits on one A100 or one H100 elsewhere. Neither of those is sold by AWS as a one-card or four-card instance, only as eight-card `p4d.24xlarge` and `p5.48xlarge`, so there is no `gpu-1xa100` to ask for and there never will be. 48 GB against 80 GB is the trade, and it is usually cheaper than reshaping the recipe.

## Multi-GPU jobs

| Compute profile | Devices | Memory | Cost |
| --- | --- | --- | --- |
| `gpu-4xt4` | 4 × T4 | 64 GB | $3.91/hr |
| `gpu-4xl4` | 4 × L4 | 96 GB | $4.60/hr |
| `gpu-4xa10g` | 4 × A10G | 96 GB | $5.67/hr |
| `gpu-8xt4` | 8 × T4 | 128 GB | $7.82/hr |
| `gpu-4xl40s` | 4 × L40S | 192 GB | $10.49/hr |
| `gpu-8xl4` | 8 × L4 | 192 GB | $13.35/hr |
| `gpu-8xa10g` | 8 × A10G | 192 GB | $16.29/hr |
| `gpu-8xa100` | 8 × A100 | 320 GB | $21.96/hr |
| `gpu-8xl40s` | 8 × L40S | 384 GB | $30.13/hr |
| `gpu-8xh100` | 8 × H100 | 640 GB | $55.04/hr |

Memory is the total across the devices, and it is the column to read first because it decides whether the job runs at all. Anything at or above `gpu-8xa100` needs an admin rather than a team lead, since the platform sends every profile over $20/hr that way whatever the run costs in total.

**Your command must start one process per device.** Nothing wraps what you type, so the launcher goes in the command:

```
bash -lc 'python -m torch.distributed.run --nproc-per-node=4 --standalone .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --save-folder "$EDULLM_CHECKPOINT_DIR" --steps 4000'
```

Set `--nproc-per-node` to the device count of the shape you picked. `torchrun`, `accelerate launch`, `deepspeed`, `mpirun` and `srun` all work.

Leaving the launcher out used to be free and silent: the run trained on one device, billed for four, and exited zero — $136 for a quarter of the work over twenty-four hours. It is now refused at submission, and the refusal prints the corrected command. The same check catches too few ranks, too many ranks, and `torchrun` with no `--nproc-per-node` at all.

**To run one process on a multi-GPU machine deliberately** — a benchmark, a memory profile — waive the check. `olmo-core-train` also declares a checkpoint contract, so a benchmark under it is waiving both, which is why two tokens appear. Each is recorded on the manifest and shown to the approver:

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

The refusal names which of those it found. The unexpanded forms reach your program as the literal text `$EDULLM_CHECKPOINT_DIR`, and OLMo-core creates a directory by that name rather than failing — which is why they count as absent.

**This is what the check exists to stop.** A trainer that is not told where to save uses its own default, `/tmp` for the OLMo-core example, on a machine that stops existing. The run trains for a day, writes checkpoints nobody can reach, exits zero, and is recorded as an unqualified success. One run in this account is in that state and nothing is recoverable from it.

**If your run genuinely does not save where the platform looks** — a program that derives its own path, or a throwaway nobody will resume — waive it:

```
bash -lc 'EDULLM_CHECKPOINT_CHECK=waived python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" --dry-run'
```

Same convention as the launcher waiver, deliberately the same spelling. What it does not do is make a retry work: a waived run that loses its machine starts from nothing.

## The bfloat16 refusal

**The T4 shapes — `gpu-1xt4`, `gpu-4xt4`, `gpu-8xt4` — have no bfloat16.** T4 is a Turing card, which is the one NVIDIA generation with tensor cores and without the format. Every other card in the table above is Ampere, Ada or Hopper and has it.

This matters more than it reads, because `gpu-8xt4` is currently the only shape above four cards this account can get. Scarcity pushes multi-card work onto exactly the machine that cannot run the format multi-card work usually wants.

**A command that asks for bfloat16 on one of those three is refused when it compiles**, before a lead is asked. The refusal names the shape, the card and the words of your command it matched:

```
train_module.dp_config.param_dtype=bfloat16   # refused on gpu-8xt4
--dtype bfloat16   --torch_dtype bfloat16   --mixed_precision bf16   --bf16
```

**It reads your command text and nothing else, and the gap is large enough to state plainly.** `.edullm/train_on_corpus.py` builds its data-parallel config with `param_dtype=DType.bfloat16` and offers no flag for it, so the getting-started command at the top of this guide **is a bfloat16 run that carries no bfloat16 token** — and this check will not refuse it on a T4. The same is true of a dtype set in a config file inside the image or read from a shell variable.

So treat the refusal as a backstop rather than a guarantee. If you are picking a T4 shape, the question to ask is what your program does, not what your command says:

| If your run | On a T4 shape |
| --- | --- |
| Passes a bfloat16 flag on the command line | Refused at submission |
| Runs `train_on_corpus.py`, or anything else that sets bfloat16 in code | **Accepted, and it will fail on the device** |
| Uses fp16 with loss scaling, or fp32 | Fine — this is what a T4 is for |

There is no waiver. The other two checks have one because the waived run still works; a waived bfloat16 run on a T4 does not.

## Required configuration

`.edullm/train_on_corpus.py` already sets all of these. You need them if you run the OLMo-core example directly or write your own program.

| Setting | Value | Why |
| --- | --- | --- |
| `--save-folder` | `"$EDULLM_CHECKPOINT_DIR"` | Defaults to `/tmp`, which is local disk on a machine that stops existing. A twenty-four-hour run writes checkpoints nobody can reach, exits zero, and is **recorded as a success** |
| `trainer.callbacks.checkpointer.max_checkpoints` | `null` | OLMo-core keeps three and deletes the rest. The prune deletes `.metadata.json` first and the workload role is denied that key by name, so the run dies with `OLMoNetworkError` — at `save_interval=200` that is step 600, about an hour in |
| `trainer.callbacks.checkpointer.ephemeral_save_interval` | `null` | Must be below `save_interval` or OLMo-core refuses the config in the first seconds |
| `trainer.callbacks.lm_evaluator.enabled` | `false` | Reads a C4 validation shard whose `.csv.gz` index was never published — the URL 404s |
| `trainer.callbacks.downstream_evaluator.enabled` | `false` | Scores HellaSwag through `ai2-olmo-eval`, which the training image does not install |
| `trainer.max_duration` | Set it | Defaults to one epoch, which may be far more or far less than twenty-four hours |
| `train_module.compile_model` | `false`, if needed | `torch.compile` needs a C compiler. Recent images have one; older commits do not |

Both evaluators fail while the trainer is being built, before the first step, so disabling one sends you back to a crash seconds later with the obvious fix already applied.

Your whole command and environment must fit in 8,192 bytes — Batch's limit. A long program belongs in your repository.

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
