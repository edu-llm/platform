# Using the capacity block

Eight `p5.48xlarge` in `us-east-2a`, sixty-four H100s, from 11:30 UTC on 2026-08-08 until 11:30 UTC on 2026-08-12. One purchase, no extension, no refund. This page is for somebody whose repository is not OLMo-core and who wants to know whether they can have some of it.

**The block is not the platform, and nothing you do on it is a run anybody can cite.** There is no admission record, no approval, no lineage entry and no run id. The platform in `us-east-1` is untouched, keeps working all week, and is where anything that has to be reproducible goes — [using the platform](the-platform.md) is that path and none of what follows replaces it. What the block is for is the work that needs sixty-four cards at once and the work that has to happen while that work is running.

> **Read this before the window opens, not during it.** Three of the decisions below are made once, at launch, by whoever dispatches the fleet, and cannot be revisited without giving a machine back. If your repository needs something the fleet is not going to have, the time to say so is now.

## Can your repository use it, and should it

Being registered in `config/repositories.yaml` has nothing to do with this. That registry is what the platform builds images from and prices runs against; the block lane never reads it. What decides whether you can run here is narrower and harder: your code must be on a **public** GitHub repository, and it must be able to run inside **the OLMo-core image**, because that is the only image any node can pull.

| Repository | Verdict | Why |
| --- | --- | --- |
| `OLMo-core` | **Yes, and it is what the block was bought for** | The flagship trains from `edullm/final-model` across nodes 1–7. Its image is the fleet's image, so it is the one codebase with nothing to arrange |
| An OLMo-core fork — `p7stuff`, `Memory-Split-P3` | **Yes, with the trap below** | The clone runs, but `import olmo_core` resolves to the image's copy and not to yours unless you say otherwise. See [your library is the image's](#your-library-is-the-images-not-your-branchs) |
| `olmo-eval-full` | **Probably, by installing on top — rehearse it first** | Needs a GPU and a checkpoint, which is exactly what the downstream lane is for. Its own image cannot be pulled here. Its dependencies are small and pure enough to install at run time, with one snag: `ifbench` is a `git+https` requirement and the block image carries no `git` |
| `open-instruct-scored-rewards` | **Needs the block and cannot use it** | GRPO against a hidden-state reward model genuinely wants these cards. It also wants vLLM, DeepSpeed and a pip-installed nvcc, none of which is in the fleet's image, and installing vLLM on top replaces the pinned torch underneath everything else. See [what it would take](#what-cannot-be-fixed-before-saturday) |
| `edullm-p1` | **No** | MixLaw validation at 370M, which the platform's GPU shapes already run, and its image needs a prebuilt flash-attn wheel the fleet's image does not carry |
| `edullm-alt-cl` | **No** | 370M arms and math SFT. Real work, and work `us-east-1` does this week without competing for a node |
| `edullm-data` | **No, and not because it is unwelcome** | Publishing and validating corpora is CPU work. It belongs on the platform, in `us-east-1`, where it already runs |
| `dolma`, `edullm-token-selection`, `tokenizer-flores-validation`, `p3math`, `grpo-tutor` | **No** | None of them is registered, none has a `.edullm/run.yaml`, and none has an argument for sixty-four H100s that a smaller shape does not answer |
| `p1-qa-results`, `p1-scaffolding-results`, `tutor-review` | **No** | Results and review artefacts. Nothing to train |
| `educompute`, `demo-repository`, `repo-template`, `sbsandbox-oidc-smoke` | **No, and they could not anyway** | Private. A node clones over HTTPS holding no credential |

**Be honest with yourself about the second column rather than the first.** Access is not the scarce thing here and never was — anybody with write on `edu-llm/platform` can start a run, deliberately, because roughly fifteen of the thirty-five of us hold no AWS role and a lane they could not reach would be a lane for the wrong half of the team. What is scarce is node-hours. Eight machines for ninety-six hours is 768 node-hours and the flagship wants most of them, so the question to answer before you take one is not "am I allowed" but "does this need sixty-four H100s, and does it need them this weekend". Data preparation, tokeniser work, a 370M ablation and anything that fits on a single A10G all have a home already, and that home is not competing with anybody.

**Nobody's ownership of any of these is written down.** `member_logins` is empty for every group in `config/organization.yaml`, which is the honest state rather than an unfinished one. If you need to find the person behind a repository, ask in the channel; there is no file that will tell you.

## The three things that are frozen once the window opens

**The image is chosen once, for the whole fleet, at launch.** Every node pulls `sbsandbox-intern-edullm-olmo-core` at one tag while it boots, because a cold cross-region pull is minutes and paying it once in a window nobody is waiting in is the whole reason the lane feels fast. There is no per-run image and no per-node image. What that image carries is torch 2.9.0 with CUDA, `olmo_core`, `edullm_data`, `boto3` with the CRT extra, `wandb`, `numpy`, `pandas`, `pyyaml`, `rich`, `safetensors`, and gcc and g++ so that `torch.compile` works. What it does not carry is `git`, `nvcc`, flash-attn, vLLM, DeepSpeed, `transformers` or `datasets`.

**A node may pull that one repository and nothing else.** This is the constraint people mistake for a missing feature. The instance role `sbsandbox-intern-edullm-block-node` grants `ecr:BatchGetImage` against exactly `sbsandbox-intern-edullm-olmo-core`, so `docker pull` of any other image is refused on the machine — from the helper, from a shell, from anywhere. Bringing your own container is not a flag that is missing; it is a permission that is not there, and widening it is a CloudFormation apply from a laptop that only a repository admin can make.

**The helper on each node is baked into its user-data.** `edullm-node` is written to `/usr/local/bin` while the machine boots, out of `infra/block-node-bootstrap.sh`. A change to that file reaches a running node only by relaunching it, and relaunching it destroys everything on its `/scratch`.

## What you need ready before 11:30 UTC

- [ ] Your code is on a **public** repository under `edu-llm`, on a branch that is pushed. A private one cannot be cloned by a node at all
- [ ] You have run your entrypoint against torch 2.9.0 somewhere, or you know which packages you are going to install on top and roughly what they cost
- [ ] Either your branch carries `.edullm/run.yaml` with a `command`, or you have the command written down to paste into the form. Only OLMo-core carries one today
- [ ] Your command writes checkpoints to `$EDULLM_CHECKPOINT_DIR` and everything else under `$EDULLM_OUTPUT_PREFIX`, both of which are `s3://` URIs the container is given
- [ ] You have agreed a node on the sheet, and a run name nobody else is using
- [ ] If you need a package that is only reachable through `git`, you have found another way to get it, because the image has no git client

## Getting your code onto a node

**Everything goes through a workflow, and that is not a formality.** The workflow holds the AWS credential; you hold a browser. Nothing you need is behind a role.

Dispatch **Block: start a run on a node** from the Actions tab. Four fields matter and the rest have defaults:

| Field | What to put |
| --- | --- |
| `node` | A number from the sheet that nobody else is on. The workflow reads the machine itself and refuses one somebody holds, naming them |
| `branch` | Your branch. Resolved against GitHub before any node is touched, so a typo costs nothing |
| `run_name` | Letters, digits, dot, dash and underscore. **Unique across the whole fleet** — see the rules below |
| `repository` | `edu-llm/<your repository>`. It defaults to `edu-llm/OLMo-core` and it is an ordinary input, so change it |
| `command` | Leave empty only if your branch carries `.edullm/run.yaml`. Otherwise the whole command, quoting included |

For one job spanning several machines, dispatch **Block: start one run across several nodes** instead. It takes every node it needs in one go or takes none, so a set that cannot be assembled leaves nothing locked, and its `command` field takes your entrypoint **with no launcher** — the `torchrun` rendezvous flags depend on which machines were claimed and are added for you. Dispatch it once with `dry_run` first; it prints the mesh and the exact command and claims nothing.

If you do hold an AWS role, the same thing from a shell on the machine is the same code path rather than a parallel one:

```bash
aws ssm start-session --target <instance id> --region us-east-2 --profile sbsandbox
sudo edullm-node status
sudo edullm-node run --name my-run --repository edu-llm/olmo-eval-full \
  --branch main --who <your github login> --command 'bash -lc "..."'
```

## Your library is the image's, not your branch's

**This is the one that will cost somebody a day.** Your branch is cloned onto the node and mounted at `/work`. The image already carries a full `olmo_core` at `/opt/olmo-core/src`, and it is on `PYTHONPATH`. So a command that runs `python .edullm/train.py` from `/work` imports the *image's* library, built from whatever OLMo-core commit the fleet was launched against, and your changes under `src/` are simply not in the process.

On the platform this cannot happen, because there the image is built from the commit being run. Here the image is fixed at launch and the branch is cloned per dispatch, so the two drift apart the moment anybody pushes. Nothing warns you. The run starts, the loss goes down, and it is the wrong model.

If your work is in library code rather than in an entrypoint script, say so explicitly:

```
bash -lc 'PYTHONPATH=/work/src:$PYTHONPATH python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" ...'
```

The same applies to every fork. `p7stuff` carries its own `src/olmo_core` and will run the image's unless that line is there.

## Three ways to have your dependencies, and only two of them exist

**Run inside the image as it is.** Free, instant, and correct for anything whose needs are torch, `olmo_core`, `boto3`, `wandb` and `edullm_data`. Reach for this first and stop here if it works.

**Install on top at run time.** Put the install in front of your command and it runs inside the container:

```
bash -lc 'pip install --no-cache-dir omegaconf datasets bm25s && python -m olmo_eval ...'
```

Two things to know before you rely on it. It happens again on **every** dispatch — each run gets a fresh container and there is no package cache anywhere that survives, so a two-minute install is two minutes off every iteration and a twenty-minute one is unaffordable. And `pip install` of anything spelled `git+https://…` fails, because the image is built from a slim Python base with no git client; a project whose own dependency list contains such a URL cannot be installed whole, and `--no-deps` plus the packages you actually need is the way through.

**Bring your own container.** This does not exist. The node's role permits one ECR repository and refuses every other pull. Do not plan around it.

## Knowing it is running, and where the output goes

The dispatch summary prints the instance, the commit that was actually cloned, and a Weights and Biases link. **W&B is the surface to watch if you hold no AWS credential**, and it works for everybody.

Each node copies every run's log to S3 once a minute, so a log is readable from outside the machine within about that. **Block: read a run's log** prints the tail of one into a job summary and needs nothing installed. From a laptop with a role, `uv run python tools/block_logs.py --node 3` does the same and `uv run python tools/block_status.py` reads the whole fleet — who holds which machine, how many cards are busy, and how long the window has left.

Everything a run produces lands under one prefix, built from the reservation and the node so that two blocks and two fleets cannot collide:

| | |
| --- | --- |
| `$EDULLM_OUTPUT_PREFIX` | `s3://edullm-block-outputs-us-east-2/block/cr-05872979e28a491aa/node-<n>/<run>/` |
| `$EDULLM_CHECKPOINT_DIR` | the same, with `checkpoints/` on the end |
| The log | the same, with `log/train.log` |
| Whatever else was on disk | the same, with `scratch/`, written by the drain rather than by you |
| `$EDULLM_DATA_BUCKET` | `edullm-data-us-east-2`, the corpus mirror, readable and not writable |

## The rules

**The claim on the machine is the lock and the sheet is the minutes.** The sheet is how eight people plan a weekend and it is genuinely useful; it cannot stop two dispatches ninety seconds apart, and on these cards that collision does not queue — both runs start, both allocate, and both die out of memory having each cost the other a slot. The refusal reads the node itself, so it is the same refusal from the workflow and from a shell. `take_the_node_anyway` exists and means what it says: the other run keeps its cards and yours fights it for memory. Ask them first.

**Run names must be unique across the whole fleet.** The name becomes the scratch directory, the container name, the S3 key segment *and* the Weights and Biases run id. The S3 prefix carries the node number so two nodes cannot overwrite each other's files, but W&B does not — two people using `eval-1` on two machines write to one W&B run and produce a chart that is the interleaving of two jobs. Put something of your own in it.

**Never stop an instance. Terminating is also not yours to do.** `/scratch` is a RAID0 stripe over the local NVMe, which does not survive a stop, so stopping a node destroys the tree, the logs and anything not already in S3 — and gives back nothing, because the window is paid for either way. If a machine is wedged, say so in the channel.

**Checkpoint often, and to S3.** AWS begins terminating the fleet thirty minutes before the window's end time, so the last usable moment on this block is 11:00 UTC on 2026-08-12 and not 11:30. Each node runs its own flush on a timer — hourly through the window, and every five minutes for the last two and a half hours — which copies `/scratch` up and counts the files rather than trusting the exit status. That flush is a safety net for the things you did not think about. It is not a checkpoint strategy: a save interval you chose because the run is long is a save interval that loses hours, and nothing about the reclaim is negotiable.

**A refusal is cheaper than a dispatch.** `dry_run` on the distributed workflow reads the fleet, prints the plan and claims nothing.

## The post-training and evaluation lane

**Node 8 is held back for downstream work**, which is why the distributed workflow's own field suggests `1,2,3,4,5,6,7`. The point is that post-training and evaluation happen *while* pretraining is still going, against checkpoints as they appear, rather than in whatever is left of the window afterwards — there is no afterwards.

Checkpoints from the flagship appear under the elected node's prefix, `s3://edullm-block-outputs-us-east-2/block/cr-05872979e28a491aa/node-<n>/<run>/checkpoints/`, and a distributed run writes one directory that every rank contributes to, so there is one prefix for the whole job rather than one per machine. Ask the person running it which node was elected; the dispatch summary says.

Inside it, each save is a directory named `step<N>` — a plain step count, so the newest is the highest. **Do not take the highest one you can see.** A save in progress is a `step<N>` that already exists and is not yet whole, and reading it gets you a partial model or an exception halfway through a load. A checkpoint is complete when `.metadata` is present, or when all three of `train/rank0.pt`, `model_and_optim/.metadata` and `.metadata.json` are; `Checkpointer.dir_is_checkpoint` in OLMo-core is that test and it is what the trainer's own resume path filters on, so using it means you and the resume agree by construction. Take the highest step that passes it and leave the rest alone.

How often they land is the training run's `--save-interval` rather than a property of this lane, and it moves. Read it off the command in the dispatch summary or ask; do not assume a number.

Your side of it runs the same way as everything else on this page: a node, a claim, a public branch, the fleet's image, and a command with the install in front of it if you need one. The checkpoint URI is an argument to your command — the container is given `$EDULLM_CHECKPOINT_DIR` for its *own* prefix, not for somebody else's, so the training run's prefix is something you pass in yourself.

## When it breaks

| What you see | What it is |
| --- | --- |
| `node_is_busy` naming somebody | They hold the claim. Take another node or talk to them |
| `node_has_unclaimed_work` | Cards are in use with nothing claiming them, which means somebody started a run outside the workflow, or a claim was released while a container was still training. Find them before you take the machine |
| `node_did_not_answer` | Systems Manager could not reach it, so nothing can say whether it is busy. This is a fleet problem rather than yours |
| `carries no .edullm/run.yaml, so pass --command` | Expected for every repository but OLMo-core. The node is released again, so just dispatch with a command |
| `no GitHub credential` on the clone | The repository is private, or the branch is gone. A node holds no credential and this cannot be worked around from here |
| The run dies in seconds on an import | Almost always the image: a package you assumed and it does not have. The dispatch reads forty lines of the log back into its own summary about a minute after starting, for exactly this |
| It trains, and the numbers are wrong for your change | Read [your library is the image's](#your-library-is-the-images-not-your-branchs) before anything else |
| A dispatch failed and you do not know what it left behind | Nothing. A failed start gives its claim back and a refused distributed launch rolls back every claim it took |

If a node is left claimed for a run that is not running and nobody can reach it, **Block: drain the fleet before AWS takes it back** reports every node's claim and what is still outstanding, and needs no role to read. `edullm-node release` on the machine is the cure, and it refuses while a container is up, which is the point of it.

## What cannot be fixed before Saturday

**A second image.** `open-instruct-scored-rewards` is the case that actually loses something: it needs the cards and it cannot have them, because vLLM, DeepSpeed and nvcc are not in the fleet's image and no other image can be pulled. Making it work is three changes rather than one — widen `TrainingRepository` in `infra/iam/block-fleet-roles.yaml` to more than a single repository and re-apply the stack, give `edullm-node run` an `--image` argument that re-authenticates to ECR before it pulls, because the boot-time login expires long before a ninety-six hour window does, and give the workflow a field for it. None of that is large and all of it is untested, on a path that runs once, against a purchase that cannot be repeated. **It is not worth doing in the hours before the window.** The honest answer for this weekend is that post-training with a vLLM actor happens on the platform in `us-east-1`, more slowly, and the block runs what the block's image can run.
