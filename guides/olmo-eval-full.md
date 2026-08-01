# Running an evaluation with olmo-eval-full

Measuring a model against a task suite. Access, the form, the corpora, the run id, the environment and stopping a run are in [`the-platform.md`](the-platform.md).

> **Nobody has run this path.** Registered, given a profile and first image published on 2026-08-01; nothing submitted since. All of it is read off configuration rather than observed, so there is no worked example here and the `basis` column says which is which.

## Prerequisites

- [ ] Your branch is named `edullm/…` — a merge to `main` builds nothing here, see below
- [ ] The build workflow has gone green on your commit
- [ ] You have the full commit SHA (`git rev-parse HEAD`)
- [ ] You have chosen a model name and a task to give a `mock` run

## Capabilities

| Capability | Available | Why |
| --- | --- | --- |
| `mock` provider | Yes | Exercises task loading, scoring and result writing without a model. This is the whole of what the published image does today |
| `vllm`, `hf`, `olmo_core` providers | No | `.edullm/Dockerfile` leaves torch and vllm out, so the import finds nothing to load. `uv` is on `PATH`, but installing a backend spends your one hour on a download |
| GPU eval profile | No | What is missing is a backend in the image, not a machine — the GPU shapes are provisioned and waiting |
| S3 | Write to the outputs bucket | The workload role holds `s3:PutObject` under `sbsandbox-intern-edullm-outputs/teams/*/runs/*` and no `s3:GetObject` at all |

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
| Dockerfile | `.edullm/Dockerfile`, never the root `Dockerfile` — that one is refused as `unregistered_stage_reference` |
| Base | `nvidia/cuda:12.8.1-runtime-ubuntu24.04`, pinned by digest |
| Tag and digest | The first twelve characters of the commit, and ECR refuses to overwrite a tag, so one commit is one image. Leave `image_digest` blank and it resolves from your commit |

## Workload profiles

| Profile | Machine | Limits | Use for |
| --- | --- | --- | --- |
| `olmo-eval-check-cpu` | `cpu-32vcpu` — c7i.8xlarge, 32 vCPU, $1.428/hr | 1h, 1 attempt, no checkpoint | The only entry this repository has. Deliberately the check rather than the eval, on `olmo-core-check-cpu`'s precedent: prove the path before spending a GPU on it |

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
| `provider.kind` | `mock` | The only provider the image can load; every other kind fails on the import | configuration |
| `--s3-bucket`, `--s3-prefix`, `--s3-group` | The outputs bucket, under your run's prefix | Anywhere else is a write this role does not hold, and should come back `AccessDenied`. `--output-dir` defaults to a local directory, which is disk on a machine that stops existing: the run exits zero and leaves nothing anybody can reach | configuration |
| Hugging Face Hub reach | Unknown | Nothing says whether a mock run fetches a tokenizer, nor whether this compute environment has the egress to get there | inference |
| Pull time inside the 1h bound | Unknown | The 2.5 GB CUDA base has a pull time nobody has measured against the bound | inference |

## Output locations

| Variable | Path | Contents |
| --- | --- | --- |
| `$EDULLM_OUTPUT_PREFIX` | `teams/{team}/runs/{run id}/` | Everything the run writes |

## Support

If you are first through this path, record the command as you typed it and what came back, and replace the note under the title with it. Everything else is in [`the-platform.md`](the-platform.md).
