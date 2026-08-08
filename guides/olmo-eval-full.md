# Running an evaluation with olmo-eval-full

Measuring a model against a task suite. Access, the form, the corpora, the run id, the environment and stopping a run are in [`the-platform.md`](the-platform.md).

> **Nobody has run this path.** Registered, given a profile and first image published on 2026-08-01; nothing submitted since. All of it is read off configuration rather than observed, so there is no worked example here and the `basis` column says which is which.

## Prerequisites

- [ ] Your branch is named `edullm/…`. A merge to `main` builds nothing here, see below
- [ ] The build workflow has gone green on your commit
- [ ] The image has finished its security scan, a few minutes *after* the build goes green
- [ ] You have the full commit SHA (`git rev-parse HEAD`)
- [ ] You have chosen a model name and a task to give a `mock` run

## Capabilities

| Capability | Available | Why |
| --- | --- | --- |
| `mock` provider | Yes | Exercises task loading, scoring and result writing without a model. This is the whole of what an image built at the default does |
| `vllm`, `hf`, `olmo_core` providers | On the branch you build | `.edullm/Dockerfile` carries `ARG MODEL_BACKEND`, defaulting to `none`, which installs no torch and leaves those three failing on the import. Set it to `vllm` or `olmo-core` on your `edullm/**` branch and the providers that value installs become constructible. `olmo-core` is the one that reads a sharded OLMo-core checkpoint |
| GPU eval profile | Yes | The GPU shapes are provisioned. Read the two S3 rows before picking one, because the profile decides what the run may read and not only what it costs |
| S3 writes | Either profile | Both workload roles hold `s3:PutObject` and `s3:ListBucket` under `sbsandbox-intern-edullm-outputs/teams/*/runs/*` |
| S3 reads | Only on a GPU profile | The GPU workload role holds `s3:GetObject` on `sbsandbox-intern-edullm-outputs/teams/*/runs/*` and on `edullm-data`. The CPU workload role holds `s3:GetObject` on `edullm-data` and `edullm-landing` and **none on the outputs bucket** |

**A run that reads a checkpoint on `cpu-32vcpu` is denied after it has been paid for.** The
checkpoints a training run writes live under `sbsandbox-intern-edullm-outputs/teams/*/runs/*`,
which the CPU workload role may write and list and may not read. Nothing refuses that
submission: it is priced, approved, queued and given a machine, and then the first read comes
back `AccessDenied`. Pick a GPU profile for anything that loads weights.

Those are the only three buckets either role reaches. A checkpoint anywhere else — another
account, or a bucket a team made for itself — is unreadable from a run no matter which profile
it lands on, and the fix is an IAM change and a deploy rather than anything on the form.

## Building your image

```bash
git push -u origin edullm/my-eval    # watch "Build eduLLM research image" on Actions
git rev-parse HEAD                   # the commit you put on the form
```

| You push to | Result |
| --- | --- |
| `edullm/**` | Image built and published |
| **`main`** | **No image, and nothing warns you.** The push succeeds, the checks go green, and the commit is refused at submission long after the merge |
| Anything else | No image, and nothing warns you |
| Manual dispatch | Image built |

**Unlike OLMo-core, whose caller also lists `main`, this one builds on `edullm/**` and manual dispatch only.**

| | |
| --- | --- |
| What the build checks | `uv run --frozen --no-group vllm pytest tests/ --ignore=tests/integration/`, this repository's own suite minus the parts needing a GPU |
| Dockerfile | `.edullm/Dockerfile`, never the root `Dockerfile`, which is refused as `unregistered_stage_reference` |
| Base | `nvidia/cuda:12.8.1-runtime-ubuntu24.04`, pinned by digest |
| Tag and digest | The first twelve characters of the commit, and ECR refuses to overwrite a tag, so one commit is one image. Leave `image_digest` blank and it resolves from your commit |

**A green build is not the last step.** The registry scans every image it accepts, and a submission naming an image whose scan has not finished is routed to an admin rather than to your team lead. The summary on the run page says the scan was still running rather than naming a vulnerability. It takes a few minutes. Wait, then resubmit the same commit, and the run goes back to the lead gate.

## Workload profiles

| Profile | Limits | Use for |
| --- | --- | --- |
| `olmo-eval-check` | 1h, 1 attempt, no checkpoint | Start here. Deliberately the check rather than the eval, on `olmo-core-check`'s precedent, which is to prove the path before spending a GPU on it |
| `olmo-eval-sweep` | 2h, 1 attempt, no checkpoint | A full benchmark split, where the hour above runs out. The ceiling is what Batch enforces on each attempt rather than across a fan-out's array, so a twenty-cell sweep is twenty two-hour attempts |

Pick `cpu-32vcpu` for `compute_profile`, which is c7i.8xlarge at $1.428/hr, **for a run that loads no weights**. The profile stopped being part of either entry's name because the entry never decided it and the form always did. It is the right answer for a `mock` run and the wrong one for every other kind, and the reason is the S3 rows above rather than the card: the CPU workload role cannot read the outputs bucket, so an eval that opens a checkpoint is denied on a machine it has already been given. A `mock` provider on eight A100s costs $21.96 an hour to do the same thing, and the eight-H100 shape people reach for instead cannot be started at all.

## Running an evaluation

```
bash -lc 'olmo-eval run --harness default -m <model> -t <task> -o provider.kind=mock'
```

`bash -lc` is required, for the reason [`the-platform.md`](the-platform.md) gives.

| | |
| --- | --- |
| `olmo-eval` | On `PATH` from a virtualenv at `/opt/venv`, working directory `/opt/olmo-eval`. The image's `CMD` of `olmo-eval --help` is replaced by whatever you type on the form |
| `-m/--model`, `-t/--task` | Both required |
| `-o/--override` | Attaches to whichever `--harness` or `--task` precedes it, so argument order carries meaning rather than being a matter of taste |
| `--dry-run` | Prints the resolved config and evaluates nothing |

## Required configuration

| Setting | Value | Why | Basis |
| --- | --- | --- | --- |
| `provider.kind` | `mock`, or a kind your `MODEL_BACKEND` installed | At the default there is no torch, so every other kind fails on the import. Built at `olmo-core` the image also loads `olmo_core` and `hf`; built at `vllm` it also loads `vllm`, `vllm_server` and `hf` | configuration |
| The checkpoint you evaluate | A URI under `sbsandbox-intern-edullm-outputs/teams/*/runs/*` | Nothing on the form declares it and nothing validates it, so the run finds out at its first read. That prefix and `edullm-data` are what a GPU run may open | configuration |
| `--s3-bucket`, `--s3-prefix`, `--s3-group` | The outputs bucket, under your run's prefix | Anywhere else is a write this role does not hold, and should come back `AccessDenied`. `--output-dir` defaults to a local directory, which is disk on a machine that stops existing: the run exits zero and leaves nothing anybody can reach | configuration |
| Hugging Face Hub reach | Unknown | Nothing says whether a mock run fetches a tokenizer, nor whether this compute environment has the egress to get there | inference |
| Pull time inside the 1h bound | Unknown | The 2.5 GB CUDA base has a pull time nobody has measured against the bound | inference |

## Output locations

| Variable | Path | Contents |
| --- | --- | --- |
| `$EDULLM_OUTPUT_PREFIX` | `teams/{team}/runs/{run id}/` | Everything the run writes |

## Support

If you are first through this path, record the command as you typed it and what came back, and replace the note under the title with it. Everything else is in [`the-platform.md`](the-platform.md).
