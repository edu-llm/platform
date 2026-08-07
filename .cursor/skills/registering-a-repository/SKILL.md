---
name: registering-a-repository
description: >-
  Registers a research codebase with the eduLLM platform so it can build an image and accept
  runs, by writing the three files the repository needs and opening the configuration pull
  request through edullm add repository. Use when edullm check refuses with
  unregistered_repository, when a codebase has no .edullm directory, or when the user asks to
  get a new repository onto the platform.
---

# Registering a repository

Registration is two halves and they are easy to confuse. **The platform half** edits reviewed
configuration files and reaches a pull request a workflow prepares and a person opens. **The repository half** is
three files in the codebase being registered, and nothing writes them for you. A repository
whose pull request merged and whose three files are missing is registered and can never build
an image, which has already happened once here.

Do the repository half first. A pull request opened against a codebase with no Dockerfile
declares a path that points at nothing.

## The loop

```
- [ ] 1. Confirm it is not already registered
- [ ] 2. Resolve the base image against what is approved
- [ ] 3. Write .edullm/Dockerfile
- [ ] 4. Write .edullm/verify_image.py
- [ ] 5. Write the build-caller workflow
- [ ] 6. Write a first .edullm/run.yaml
- [ ] 7. Commit and push to an edullm/** branch
- [ ] 8. Prepare the configuration pull request
- [ ] 9. Open it, and say what happens next
```

Two facts about the environment, because both change what you write and neither is
discoverable from a research checkout.

**Containers have outbound internet on HTTPS and HTTP.** A run can pull from PyPI, GitHub
and the Hugging Face Hub. You do not have to bake assets into the image for network reasons,
though baking them still buys you not failing on somebody else's outage after a GPU has been
allocated. Nothing can reach in. `guides/the-platform.md` has the shape and the trade-off.

**A project `.venv` can shadow the platform CLI.** `edullm-data` ships a console script also
called `edullm`, so an active environment depending on it answers `edullm` with a different
program. `which -a edullm` is the diagnostic; the first line wins. Do not attempt to fix
`edullm-data`, which is a separate repository.

### 1. Confirm it is not already registered

```bash
edullm check --json --experiment a-first-look --dataset none
```

A `refusals` entry with code `unregistered_repository` means it is not. Its `detail` lists
what is registered. Any other answer means it already is, and you are in the wrong skill.

### 2. Resolve the base image against what is approved

This is the one question a reviewer has to answer, so answer it before asking.

Read `config/repositories.yaml` in the platform repository and list the base images existing
registrations already carry. Resolve this codebase's dependency set against them.

- If one of the approved bases satisfies the dependency set, use it. Say which, and say that
  it is one already reviewed.
- If none does, name the single pin that forces a new base. A new base is a second thing to
  review, scan and re-pin, so it needs a reason and the reason is that pin.

Do not pick a base because the project's own Dockerfile uses it. That is not a reviewed
answer.

### 3. Write `.edullm/Dockerfile`

It builds from the base you resolved, installs the dependency set, and does nothing at
runtime. The command a run executes comes from the submission, not from the image.

Keep it minimal. Every layer is rebuilt on every push to an `edullm/**` branch and the build
cache is one of two levers on a bill that is not small.

### 4. Write `.edullm/verify_image.py`

The one assertion the platform cannot make for you, run inside the assembled image on every
build, before the push. Write it, and a dependency your code needs and your image lacks is a
red build instead of a billed GPU allocation that dies in the first seconds.

This is not hypothetical and it is why the step exists. Every `olmo3_*` and `olmo2_*` factory
in OLMo-core hardcodes `attn_backend=flash_2`; `Attention.__init__` calls `assert_supported()`
while the model is being *constructed*; the registered image has no flash-attn and its base
has no compiler to build one. So no model could be instantiated at all, and the way that got
found out was a researcher waiting for a GPU, getting one, and losing it seconds later.

**Assert what the image has to be able to do, not what it has to contain.** A version pin
tells you a package is installed. Constructing the thing tells you the constructor does not
raise, which is the failure above and is the one a pin cannot see.

**Enumerate, never list.** A hardcoded set of model names is correct on the day it is typed
and silently incomplete afterwards, which is the failure mode the check exists to prevent
arriving one level up. Read the names off the object:

```python
from olmo_core.nn.transformer import TransformerConfig

RUNGS = sorted(
    name for name in dir(TransformerConfig)
    if name.startswith(("olmo2_", "olmo3_"))
)
for rung in RUNGS:
    getattr(TransformerConfig, rung)(vocab_size=100352).build(init_device="meta")
```

`init_device="meta"` is load-bearing: it runs every constructor and allocates no storage, so
the whole ladder costs seconds and no memory. Print each name before you build it, so a red
build names the rung that failed.

**It runs with no network, with no GPU, and with only `.edullm/` mounted.** No device is
available and none can be asked for, which is affordable for the case above — flash-attn is
absent, so the import raises with no device in the question — and is affordable in the
passing direction too, because loading a CUDA extension needs no card and constructing a
module launches no kernel. **A check that genuinely needs a device must not go here.** It
would go red on every build of a correct image. Put it behind your `*-check` workload
profile instead, which is a short run on the smallest GPU shape and is what that profile is
for.

Exit non-zero, or raise, to refuse the image. Whatever the check printed is reproduced in the
build log, so print what a reader will need. A repository with no such file is not refused —
the build says so on every run rather than passing over it silently — so leaving this out is
a choice you are making rather than a step you can skip unnoticed.

### 5. Write the build-caller workflow

A workflow in the research repository that calls the platform's reusable build. **Check what
it fires on.** A caller that fires only on `edullm/**` pushes and manual dispatch never fires
for a branch named anything else, and that is exactly how a registered repository ends up with
zero images while looking correct. If the work lives on a branch that is not `edullm/**`, say
so, and say the two ways out, which are renaming the branch or dispatching the caller by hand.

**Then set `AWS_ECR_PUBLISHER_ROLE_ARN` as a repository variable, which the workflow you just
wrote reads and which nothing gives you.** Settings, then Secrets and variables, then Actions,
then Variables, on the research repository itself. `gh variable set` does it from a terminal,
given the name, the value and the repository.

The ARN is the one `infra/README.md` records for `sbsandbox-intern-edullm-ecr-publisher`. It
is set per repository, by hand, in each: **there is no organization variable behind it**, so
the repositories that already have one tell you nothing about this one, and registering a
repository does not create it.

Until 2026-08-06 this step was in no document at all. `edullm-p1` read as fully registered and
published nothing for days because of exactly that. It is not a step the platform can check for
you either — a token scoped to `edu-llm/platform` is refused by every other repository's
variables endpoint — so the check lives in the reusable build, whose first step refuses an
empty value with `publisher_role_arn_is_empty` and the variable's name. If you see that, this
is the step you skipped.

### 6. Write a first `.edullm/run.yaml`

It holds what is a property of the code, which is the command, the workload profile and a
suggested machine. Everything else is supplied at submit time.

`edullm check` writes this file itself once the repository is registered, so the version you
write here is a placeholder that gets replaced. Write it anyway. It is what makes the pull
request reviewable.

### 7. Commit and push to an `edullm/**` branch

```bash
git switch -c edullm/register
git add .edullm/
git commit -m "Add the platform's build inputs"
git push -u origin edullm/register
```

The push is what builds the first image.

### 8. Prepare the configuration pull request

```bash
edullm add repository --reason "<why this needs a repository of its own>"
```

`--reason` has no default and it is the only part a reviewer cannot derive. Answer why this
needs a repository of its own rather than a workload profile in one that is already
registered.

The command prints two links: the workflow run page, and the compare URL to open the pull
request at. **The workflow does not open it.** This organization forbids Actions from
creating a pull request, and the one setting that would allow it also allows a workflow to
submit an approving review, which is what protects the very files a registration edits.

### 9. Open it, and say what happens next

Wait for the run to go green, then open the compare URL. The title is filled in and the body
is not — it runs to about eleven thousand characters, which is over twice what a URL will
carry, so the run's job summary prints it in a block to copy into the description. Copy the
whole of it; it records which claims were checked against the repository, which ones nothing
can check, and the follow-ups.

Then tell the user plainly. The pull request has to be merged by the platform owner and then
deployed, and **nothing is registered until both have happened.** A merged configuration
change does nothing in the account until somebody deploys, and that has already cost one
incident. `edullm check` keeps refusing this repository until then.

## Never

- Never edit the platform's configuration files directly. The workflow edits five of them and
  runs a verification; a hand edit skips it.
- Never invent a base image. Resolve against `config/repositories.yaml`.
- Never claim the repository is registered because the pull request is open.
