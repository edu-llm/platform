# Getting a module tested this week

For whoever is fielding "can I run my thing today". Sort a request in under a minute: find the
repository, find the shape, run `edullm check`, submit. Everything here is true of the platform
as deployed, and every refusal named below is one `edullm check` prints on a laptop without
reaching a network.

Three sentences of context. Every registered repository has a `-check` profile bounded at one
hour and, for four of them, a `-train` profile bounded at twenty-four. Nothing needs an
administrator any more: under policy v5 every request is `routine` and a team lead releases it.
The machine is the form's field, not the profile's, so the same workload profile runs on a T4 or
on eight A100s depending on what you ask for.

## Yes, go now

```bash
uv tool install --force git+https://github.com/edu-llm/platform
cd <your checkout>
git push -u origin HEAD                       # check refuses an unpushed commit
edullm check --experiment <slug> --dataset <corpus-or-none>
edullm check --json --experiment <slug> --dataset <corpus-or-none>   # for scripts
edullm submit --experiment <slug> --dataset <corpus-or-none>
edullm status
edullm logs <run-id>
```

`check` reaches no network, costs a fraction of a second, and lists every refusal at once. Run
it as often as you like. Loop on `check` before you ever submit.

### Which shape, by job size

Reach for the smallest thing that fits. Waits below are measured medians from real runs, not
estimates.

| Reach for | When | Cards / memory | Wait |
| --- | --- | --- | --- |
| `cpu-32vcpu` | data transforms, tokenization, mock-provider evals, anything with no model on a card | c7i.8xlarge | starts |
| `gpu-1xa10g` | one-card smoke test, "does my module import and step" | 1 × A10G, 24 GB | starts |
| `gpu-1xt4` / `gpu-4xt4` / `gpu-8xt4` | cheap shakedown where the card does not matter | T4, 16 / 64 / 128 GB | starts |
| `gpu-1xl4` | one-card, newer than a T4 | 1 × L4, 24 GB | starts |
| **`gpu-4xa10g`** | **the default for a real 370M–1B training step** | 4 × A10G, 96 GB | **~7 min** |
| `gpu-8xa10g` | same work, twice the memory | 8 × A10G, 192 GB | ~9 min, one job waited 200 |
| `gpu-4xl40s` | when you want 48 GB cards rather than 24 GB | 4 × L40S, 192 GB | ~19 min |
| `gpu-1xl40s` | one big card | 1 × L40S, 48 GB | one job, no median |
| **`gpu-8xa100`** | **the largest thing that places at all** | 8 × A100 40 GB, **320 GB** | **~61 min** |

The six `starts` rows are the ones a submitter gets without queueing. Everything above 320 GB —
`gpu-8xh100`, `gpu-8xh200`, `gpu-8xb200`, `gpu-8xb300`, `gpu-8xa100-80gb`, `gpu-1xh100` — is
`provisioned: false` with no queue behind it and will refuse with
`unprovisioned_compute_profile`. There is no route to one this week; see
[capacity blocks](capacity-blocks.md).

## Is the work in a registered repository?

This is the first fork. Six repositories are registered and nothing else can build an image.

| Repository | What it is for | Profiles |
| --- | --- | --- |
| `OLMo-core` | pretraining and fine-tuning; the model, optimizer and data-loader code | `olmo-core-check` 1 h · `olmo-core-train` 24 h, checkpoints |
| `edullm-alt-cl` | math front-load / anneal on OLMo2-370M, then math SFT and a GSM8K/MATH ladder | `edullm-alt-cl-check` 1 h · `edullm-alt-cl-train` 24 h, checkpoints |
| `open-instruct-scored-rewards` | GRPO and RLVR; carries vLLM, DeepSpeed and nvcc, which no other image does | `…-check` 1 h · `…-train` 24 h, checkpoints |
| `edullm-data` | the dataset standard: publish, validate, read | `edullm-data-validate` 1 h |
| `olmo-eval-full` | the eval harness | `olmo-eval-check` 1 h · `olmo-eval-sweep` 2 h |
| `edullm-p1` | MixLaw data-mixing-law validation at 370M | `edullm-p1-check` 1 h **only — no train profile** |

Two traps in that table. `edullm-p1` has no `-train` profile, so mixing-law work is capped at one
hour per run until somebody adds one. And the catalog carries a `dolma-tokenize` profile naming a
`dolma` repository that is **not registered** — pick it and you get `unregistered_repository`.

**If the code is not in one of those six:** the same-day answer is to work on a branch inside the
registered repository that is closest to the work. A branch is enough — the image builds from
whatever commit you push to `edullm/**`, and `check` keys on the repository, not the branch. That
is the route to use this week.

Registration itself is a configuration pull request through `edullm add repository`, which means
a review, a merge, an image build, and — because `config/repositories.yaml` is one of the seven
files the admission validator packages — a validator release before admission agrees the
repository exists. Realistically a day or two with everyone available, and not something to
start on a Friday. See `.cursor/skills/registering-a-repository/`.

## Where each module goes

**Start today, no blockers.**

| Module | Repository / profile | Shape |
| --- | --- | --- |
| Difficulty gating, curriculum | `edullm-alt-cl-train` | `gpu-4xa10g` → `gpu-8xa100` |
| SFT + DPO + RLVR, pedagogy SFT/RLVR | `open-instruct-scored-rewards-train` | `gpu-4xa10g` → `gpu-8xa100` |
| QA formatting, worked examples | `edullm-data-validate`, branch | `cpu-32vcpu` |
| MTLD labelling | `edullm-data-validate`, branch | `cpu-32vcpu` |
| Non-finite context, Engram, KDA | `olmo-core-check` then `olmo-core-train` | `gpu-1xa10g` → `gpu-4xa10g` |
| MuonH | `olmo-core-check` then `olmo-core-train` | `gpu-4xa10g` |

The two strongest this weekend are **curriculum through `edullm-alt-cl-train`** and **GRPO
through `open-instruct-scored-rewards-train`**. Both have a 24-hour bound, two attempts, a
checkpoint contract at 30-minute intervals, and an image already carrying what they need — vLLM
and DeepSpeed in the second case. Point people at these first.

**Start today, but bounded.**

| Module | Where | The bound |
| --- | --- | --- |
| Mixture probing, domain weighting | `edullm-p1-check` | one hour per run, no train profile |
| Latent reasoning | `open-instruct-scored-rewards-*`, branch | fine as a training-side change; not as a serving experiment |
| Steering vectors | `open-instruct-scored-rewards-*`, branch | the only image with vLLM, so this is where a loaded model lives |
| HPO | any `-train` profile with `--fanout-size` | static fan-out only; see below |

**Blocked this week.**

| Module | Blocked by |
| --- | --- |
| Adaptive checkpoint evals | no eval backend, and a run cannot declare it reads another run's checkpoint |
| Base bench recovery, pedagogy evals | no eval backend in the image; `mock` provider only |
| Late-checkpoint EMA | needs to read checkpoints a previous run wrote, which no run can declare |
| Speculative decoding | needs a serving surface; the platform has none |
| Adaptive HPO | fan-out is decided at submit time and cannot read its own results |

## The three that will bite people

**1. The eval image has no model backend.** `olmo-eval-full`'s `.edullm/Dockerfile` leaves torch
and vLLM out, so only the `mock` provider loads. `config/workload-catalog.yaml` says the fix
plainly: "there will not be one until the image carries a backend, which is one line in that
repository's Dockerfile and a rebuild."

That is accurate and it is the highest-value thing available this weekend. The repository already
declares a `vllm` dependency group — its own build step runs
`uv run --frozen --no-group vllm pytest`, which is what excluding it looks like. So the change is
to stop excluding that group in the image's `uv sync`, push to `edullm/**`, and let
`build-research-image.yml` rebuild. Then `olmo-eval-sweep` on a real checkpoint becomes possible
and three eval modules unblock at once. Budget an afternoon, not a day: the image grows by
several gigabytes and the first build is the slow one. Nobody has done it, so treat the first
attempt as the test.

Workaround until then: `-o provider.kind=mock` on `cpu-32vcpu` exercises task loading and result
writing without a model. Do not put a mock-provider eval on a GPU — it costs $55 an hour to do
what $1.43 does.

**2. A run cannot declare it reads another run's checkpoint.** There is no field for it. Each run
gets its own id and writes under `teams/<team>/runs/<run_id>/checkpoints/`. `resume_required` is a
declaration nothing branches on, and `parent_run_id` exists in the schema with nothing writing it.

Workaround: the GPU workload role can `GetObject` across `teams/*/runs/*`, so a command that
**hardcodes the prior run's S3 prefix** will read it. It works. What you lose is the link — the
lineage record will show two unrelated runs, and nobody reading it later can tell that the second
continued the first. Write the prior run id into the experiment slug so at least the grouping
survives. The real fix is a schema and form change, which is weeks.

**3. Nothing above 320 GB places.** `gpu-8xa100` at 320 GB is the ceiling and it has no peer:
every larger shape needs a capacity block that has not been bought. If a module needs more than
320 GB of device memory, the answer this week is to shrink the model or shard differently, not to
wait for a machine.

## Refusals, and the one-line fix

| Code | Fix |
| --- | --- |
| `uncommitted_changes` | commit or stash; the image builds from a commit |
| `commit_not_pushed` | `git push -u origin HEAD` |
| `commit_not_in_this_clone` | fetch it, or drop `--commit` |
| `no_origin_remote` | add the remote, or pass `--repository` |
| `unregistered_repository` | work on a branch of one of the six, or register it (not today) |
| `unregistered_workload_profile` | check the table above for the exact profile name |
| `workload_profile_repository_mismatch` | the profile belongs to a different repository |
| `unprovisioned_compute_profile` | that shape has no queue; pick one from the shapes table |
| `runtime_above_the_workload_bound` | lower `--hours`; the bound is 24 for training, 1 for checks |
| `retry_without_a_checkpoint_contract` | `--attempts 1`, or use a `-train` profile that has one |
| `unregistered_dataset` | `--dataset none`, or name a registered corpus |
| `dataset_is_not_a_corpus` | you named a tokenizer input; pick the corpus |
| `no_experiment` / `no_dataset` | pass `--experiment` and `--dataset` |
| `experiment_not_a_slug` | lowercase, hyphens, no spaces |
| `team_is_ambiguous` / `unregistered_team` | pass `--team` with a registered slug |
| `submitter_unknown` | you are not on the roster; `edullm ask` |
| `process_per_device` | `--nproc-per-node` must match the cards on the shape |
| `checkpoint_path_not_in_command` | the command must pass `$EDULLM_CHECKPOINT_DIR` |
| `bfloat16_not_in_the_hardware` | T4s have no bf16; move to A10G or better |
| `fanout_incomplete` | `--fanout-size` needs `--fanout-index-parameter` |
| `no_published_image` / `image_scan_findings_unreviewed` | **deferred, not decided on a laptop.** These need the registry, so a clean `check` is not a promise a submission goes through |

## Say no to these

| Request | Honest reason | Realistic |
| --- | --- | --- |
| H100 / H200 / B200 / anything over 320 GB | no queue exists; needs a purchased capacity block | earliest H100 block is 2026-08-29 |
| Multi-node anything | every job definition is single-node, no EFA, no rendezvous | not scoped |
| A new dataset registered today | validation, a configuration PR, and a validator release | days |
| A new repository registered today | same chain plus an image build | a day or two, not Friday |
| Adaptive HPO | fan-out cannot read its own results | needs a design |
| Serving, speculative decoding, an inference endpoint | the platform has no serving surface | not scoped |
| A training run over 24 hours | the profile's bound, and raising it moves two Lambda zips | chunk it into 24-hour runs instead |
| GPU evals against a real checkpoint | no backend in the image | this weekend, if somebody does the Dockerfile line |

Last thing worth saying out loud: a 24-hour ceiling is not a problem to route around. Four
consecutive 24-hour runs need no configuration change at all, whereas raising the bound is an
edit to a reviewed file, a new CLI release everybody must install, and a rebuild of both the
admission validator and the notifier zips. Chunk the work.
