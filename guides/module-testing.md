# Testing a module

For sorting "can I run this today" in under a minute. Everything here is about work that fits on one machine — an ablation, a probe, a short fine-tune, an arm of a sweep.

## Start here, always

```bash
edullm check --json --experiment <slug> --dataset <release-or-none>
```

It reaches no network, answers in a fraction of a second, and lists every refusal at once rather than one per attempt. Run it as often as you like. **Push your branch first** — the image is built from a commit, and a commit no remote holds has built nothing.

Exit 0 means it stands. Exit 1 means read `refusals` and **match on `code`**; the prose beside it gets reworded.

## Which machine

The list is ordered by what you are doing, not by size, because the largest shape is rarely the right answer.

| You are | Pick | Typical wait to start |
| --- | --- | --- |
| Validating data, or an eval with the mock provider | `cpu-32vcpu` | immediate |
| Checking your code sees a GPU at all | `gpu-1xa10g` | immediate |
| A real step at 370M–1B | `gpu-4xa10g` | ~7 minutes |
| The same, wanting 192 GB | `gpu-8xa10g` | ~9 minutes |
| Something that needs 48 GB on a single card | `gpu-1xl40s` or `gpu-4xl40s` | ~19 minutes |
| The largest thing that exists here: 320 GB | `gpu-8xa100` | ~61 minutes |

**Nothing above 320 GB places.** Both H100 shapes are priced and have no queue behind them — one logged over seven thousand capacity refusals in a day and produced no instance. Asking for one is not a slow answer, it is no answer. If your work genuinely needs more, that is a capacity block: see [capacity blocks](capacity-blocks.md), and expect weeks rather than days.

The waits above are medians from real runs, and the tail is long — the 320 GB shape has a worst observed case measured in hours. Nothing has ever been cancelled for want of capacity on the shapes in this table.

## Which repository

The platform builds images for registered repositories only. Today those are `OLMo-core`, `edullm-alt-cl`, `edullm-data`, `edullm-p1`, `olmo-eval-full` and `open-instruct-scored-rewards`.

If your module lives somewhere else, **the same-day route is a branch on the closest registered repository**, not a new registration. `edullm add repository` is self-service, but it opens a configuration pull request that has to be merged and then deployed — a day or two, and not something to start on the morning you want a result.

Two traps in the catalog worth knowing before you pick from a dropdown. `edullm-p1` has only a one-hour `edullm-p1-check` profile and no train profile, so mixture-probing work is capped at an hour. And `dolma-tokenize` names a `dolma` repository that is not registered, so picking it refuses for a reason that is not your fault.

## Three things that will bite you

**The eval image cannot score a real checkpoint.** `olmo-eval-full` ships without a model backend, so only the `mock` provider runs. You can train this week; you cannot measure. Evals written in your own repository are unaffected — this is only about the shared harness.

**A run cannot declare that it reads another run's checkpoint.** Hardcoding an `s3://` prefix works and the training role can read the outputs bucket, but nothing resolves it, validates it, or records it. The lineage record will be silent about where your weights came from, so anything built on a prior run has a gap in its provenance.

**`$EDULLM_CHECKPOINT_DIR` is a URI, not a directory.** It holds an `s3://` prefix. `pathlib.Path("s3://bucket/key")` silently becomes the relative local path `s3:/bucket/key`, so a program that treats it as a filesystem path writes beside the process, raises nothing, exits zero, and loses everything when the container stops. Two registered repositories have lost a run this way. Detect the URI and upload, or stage locally and sync as you write.

## Refusals you will actually hit

| Code | What to do |
| --- | --- |
| `submitter_unknown` | `gh auth login`. Nothing can be priced until the roster can be asked about somebody |
| `team_is_ambiguous` | Pass `--team`. The detail lists the groups the roster puts you on, and names the one for work you will not keep |
| `submitter_not_in_claimed_team` | You are not recorded in that group. Use one the detail names, or ask to be added |
| `uncommitted_changes` | Commit or stash. The image is built from the commit, so what would run is not what is on disk |
| `commit_not_pushed` | Push to a branch named `edullm/<something>`. That push is what builds the image. If you just pushed, `git fetch` first |
| `unregistered_repository` | The platform does not carry this codebase. See *Which repository* above |
| `unprovisioned_compute_profile` | The shape is priced and has no queue behind it. Pick another `--compute` |
| `process_per_device` | Your command starts a different number of processes from the number of cards. Fix the launcher, pick a matching shape, or waive it deliberately |
| `bfloat16_not_in_the_hardware` | The card is Turing and has no bfloat16 at all. Pick another shape, or set the run to float32 |
| `checkpoint_path_not_in_command` | The workload promises a checkpoint and your command never expands `$EDULLM_CHECKPOINT_DIR`. Point your save folder at it, under a shell so it expands |
| `retry_without_a_checkpoint_contract` | More than one attempt on a workload that checkpoints nothing means a retry starts from the beginning |
| `unregistered_dataset` | The detail lists what is registered. Never invent a release id |

Anything else, read the `detail`. It was written to be acted on and usually names the file to change.

## Running fewer processes than the machine has cards

Sometimes correct — several independent single-GPU arms on one multi-card box, for instance. The launcher check refuses it by default because the far commoner cause is a launcher that was never configured, and the bill is the same either way. Waive it deliberately by putting this token in the command, matched exactly:

```
EDULLM_LAUNCH_CHECK=waived
```

The approver is shown that the run bills for every device and starts fewer, so the waiver is visible rather than silent.

## Attempts, and when a second one is worse than none

`olmo-core-train` allows two attempts and declares that a retry resumes from a checkpoint. If your program has no resume path, a second attempt silently restarts from the beginning and bills a second full run for one result. Submit with `--attempts 1` where that is you.

## What is not here

Fan-out (`--fanout-size` with `--fanout-index-parameter`) turns one submission into that many machines, which covers a hyperparameter grid or a mixture sweep. What it does not do is adapt: there is no Bayesian search, no successive halving, and no way to stop a bad arm early. Every cell runs to its bound.

There is also no serving surface of any kind. Everything is a batch job, so work that needs a model held up and queried — applying steering vectors at generation, measuring decoding throughput — has nowhere to run today.

## Then

```bash
edullm submit --experiment <slug> --dataset <release-or-none>
```

Keep the run id it prints. `edullm status --json <run-id>` answers from GitHub, costs nothing and may be polled. `edullm logs <run-id>` and `edullm status` without `--json` start a workflow, take tens of seconds, and do not belong in a loop.

If `check` was clean and `submit` refuses anyway, it will be one of the two image checks that a laptop cannot make: whether your commit published an image, and whether its scan findings have been read. Both need the container registry.

**If the `edullm` command misbehaves in a project virtualenv**, check you are running the one you think you are:

```bash
which -a edullm
```

A project that depends on `edullm-data` installs a console script of the same name, and a virtualenv earlier on your `PATH` will shadow the platform CLI.
