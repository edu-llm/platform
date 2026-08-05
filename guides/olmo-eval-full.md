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
| `mock` provider | Yes | Exercises task loading, scoring and result writing without a model. This is the whole of what the published image does today |
| `vllm`, `hf`, `olmo_core` providers | No | `.edullm/Dockerfile` leaves torch and vllm out, so the import finds nothing to load. `uv` is on `PATH`, but installing a backend spends your one hour on a download |
| GPU eval profile | No | What is missing is a backend in the image, not a machine. The GPU shapes are provisioned and waiting |
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
| Dockerfile | `.edullm/Dockerfile`, never the root `Dockerfile`, which is refused as `unregistered_stage_reference` |
| Base | `nvidia/cuda:12.8.1-runtime-ubuntu24.04`, pinned by digest |
| Tag and digest | The first twelve characters of the commit, and ECR refuses to overwrite a tag, so one commit is one image. Leave `image_digest` blank and it resolves from your commit |

**A green build is not the last step.** The registry scans every image it accepts, and a submission naming an image whose scan has not finished is refused with `image_scan_findings_unreviewed`, which reads as though your image carries unapproved vulnerabilities, and usually means only that the scan was still running. It takes a few minutes. Wait, then resubmit the same commit.

## Workload profiles

| Profile | Limits | Use for |
| --- | --- | --- |
| `olmo-eval-check` | 1h, 1 attempt, no checkpoint | The only entry this repository has. Deliberately the check rather than the eval, on `olmo-core-check`'s precedent: prove the path before spending a GPU on it |

Pick `cpu-32vcpu` for `compute_profile`, which is c7i.8xlarge at $1.428/hr. The profile stopped being part of this entry's name because the entry never decided it and the form always did. It is still the right answer here for as long as the image carries no GPU backend. A `mock` provider on eight H100s costs $55 an hour to do the same thing.

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
