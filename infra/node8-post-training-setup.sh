#!/bin/bash
# Put open-instruct on the downstream lane node without touching the image or the node's role.
#
# WHAT THIS IS ROUTING AROUND, BECAUSE IT DECIDES EVERY CHOICE BELOW. A block node's instance
# profile grants `ecr:BatchGetImage` against exactly one repository ARN -- the OLMo-core
# training image `block-node-bootstrap.sh` pulls at boot. `docker pull` of a second image is
# refused on the machine, so the ordinary answer to "post-training needs a different
# dependency set" (build an open-instruct image, pull it here) is not available and will not
# be until a separate effort widens that grant. This script takes the other route: no second
# image, no new permission, just a Python virtual environment built out of PyPI and a handful
# of release URLs, which the node already has egress for.
#
# ON THE HOST AND NOT INSIDE THE TRAINING CONTAINER, AND THAT WAS MEASURED RATHER THAN
# ASSUMED. Two things make the in-container venv worse, and the second one is the sort of
# failure that costs a run rather than a minute.
#
#   The image has no git, and open-instruct needs one. `ai2-olmo-core` is locked to a git
#   revision of allenai/OLMo-core rather than to a published version, and uv shells out to a
#   git binary to fetch it -- there is no built-in client. Inside the training image the sync
#   stops with `Git executable not found`. It can be repaired with apt-get, but a container
#   is not a place repairs persist: every `docker run` starts from the image again, so that
#   apt-get is a step in front of every single invocation rather than a thing done once.
#
#   The image sets PYTHONPATH=/opt/olmo-core/src, and PYTHONPATH beats a virtual environment.
#   A venv rearranges sys.path but it does not remove what PYTHONPATH put at the front, so a
#   venv-owned `python` inside that image still resolves `import olmo_core` to the image's
#   checkout instead of the revision uv.lock pins. That is not an error anybody sees. It is a
#   different OLMo-core, built against the image's torch 2.9, imported quietly underneath a
#   post-training run using torch 2.10 -- which is the most expensive kind of wrong, because
#   everything starts and the logs look ordinary. The host has no PYTHONPATH set at all and
#   the problem does not exist there. This script clears it anyway, below, so that the same
#   file still does the right thing if somebody later runs it inside the container.
#
# THE DLAMI'S OWN PYTHON IS NOT USED AND ITS VERSION DOES NOT MATTER. open-instruct requires
# python 3.12.* exactly, and the AMI's interpreters are a moving target -- a framework DLAMI
# keeps its python inside /opt/pytorch alongside a torch that is not the one being installed
# here. open-instruct's pyproject.toml sets `python-preference = "only-managed"`, so uv
# downloads and manages its own standalone CPython 3.12 regardless of what the host ships,
# and the venv is built on that. Nothing here reads /usr/bin/python3 or activates
# /opt/pytorch, and nothing here can be broken by AWS shipping a new AMI with a new python.
#
# CUDA COMES OUT OF THE WHEELS. Nothing on the host is needed beyond the NVIDIA kernel driver
# the AMI already loaded: the cu128 torch wheel and the nvidia-*-cu12 packages beside it carry
# their own CUDA userspace, the flash-attn builds are prebuilt against cu128torch2.10, and no
# nvcc, no CUDA toolkit and no headers are involved. That is the same arrangement the
# OLMo-core image uses, and it is why this can be a venv at all rather than an image.

set -euo pipefail

# ---------------------------------------------------------------------------------------
# WHAT CAN BE SAID FROM OUTSIDE. Everything is overridable and everything has a default that
# is right on node 8, because the common case is a person typing the bare command under
# Systems Manager with no arguments at all.
# ---------------------------------------------------------------------------------------
BRANCH="${EDULLM_OI_BRANCH:-edullm/add-research-image}"
REPOSITORY="${EDULLM_OI_REPOSITORY:-edu-llm/open-instruct}"
REFRESH=no

usage() {
  cat <<'USAGE'
usage: node8-post-training-setup.sh [--branch REF] [--repository OWNER/NAME]
                                    [--root DIR] [--refresh]

Builds a python 3.12 virtual environment holding open-instruct and its cuda12
dependency group on this node, and proves it can see the GPUs.

  --branch REF         branch or tag to install (default edullm/add-research-image)
  --repository O/N     GitHub repository (default edu-llm/open-instruct)
  --root DIR           where the tree, the venv and the uv cache live
  --refresh            discard an existing checkout and clone it again; without
                       this an existing tree is left exactly as it is, edits and all
USAGE
}

# THE VENV LIVES ON /scratch AND UNDER A NAME BEGINNING WITH A DOT, AND THE DOT IS LOAD
# BEARING RATHER THAN A HABIT.
#
# /scratch is the right filesystem. It is the RAID0 over the p5's local NVMe that the
# bootstrap assembles, it has terabytes free where the root EBS volume has a few hundred
# gigabytes shared with the docker images, and unpacking roughly twenty-five gigabytes of
# wheels is the one part of this that is genuinely disk-bound. Putting the uv cache on the
# same filesystem matters for the same reason twice over: uv hardlinks from its cache into
# the venv when the two share a filesystem, which saves both the copy and a second six
# gigabytes on disk, and pyproject.toml already asks for `link-mode = "hardlink"`.
#
# The dot is what keeps it out of the drain. `edullm-node drain` walks `${SCRATCH}/*/` and
# treats every directory it finds as a run to be flushed to S3, and the log sync walks
# `${SCRATCH}/*/log` every minute. Neither sets `dotglob`, so neither can see a directory
# whose name starts with a dot. Without that, this venv would be twenty-five gigabytes of
# reconstructible wheels uploaded to S3 -- competing, in the final minutes of the block, with
# the files that are actually about to be destroyed and cannot be rebuilt from a lockfile.
#
# /scratch does not survive termination and nothing here pretends otherwise. This environment
# is rebuilt on a new block, from the same lockfile, by re-running this script.
ROOT="${EDULLM_OI_ROOT:-/scratch/.post-training}"

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
    --repository) REPOSITORY="${2:?--repository needs a value}"; shift 2 ;;
    --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
    --refresh) REFRESH=yes; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown argument $1" >&2; usage >&2; exit 2 ;;
  esac
done

readonly BRANCH REPOSITORY ROOT REFRESH
readonly TREE="${ROOT}/open-instruct"
readonly VENV="${ROOT}/venv"
readonly UV="${ROOT}/bin/uv"
readonly STAMP="${ROOT}/installed.json"

# How many cards this has to end up seeing. The bootstrap already wrote the number the
# instance type is supposed to have into /etc/edullm-block.env and refused to make the node
# ready without it, so that file is asked rather than a number being written here twice.
EXPECTED_GPUS=8
if [ -r /etc/edullm-block.env ]; then
  # shellcheck disable=SC1091
  . /etc/edullm-block.env
  EXPECTED_GPUS="${EDULLM_BLOCK_EXPECTED_GPUS:-8}"
fi
readonly EXPECTED_GPUS

step() {
  printf '\n==> %s\n' "$*"
}

die() {
  echo "node8-post-training-setup: $*" >&2
  exit 1
}

# ---------------------------------------------------------------------------------------
# PREFLIGHT. Everything that would make the rest pointless, checked before six gigabytes are
# downloaded rather than after.
# ---------------------------------------------------------------------------------------
step "checking this node before spending anything"

# No `set -e` help here: `command -v` returning false is the answer, not an accident.
command -v git > /dev/null 2>&1 ||
  die "git is not on this host, and it is not optional -- ai2-olmo-core is locked to a git
revision of allenai/OLMo-core, so uv cannot install the locked set without a git binary.
The Deep Learning AMI ships one; a host that has lost it is a host to look at rather than
a thing to work around here."

command -v curl > /dev/null 2>&1 || die "curl is absent, so uv cannot be installed"

# A WARNING RATHER THAN A REFUSAL, BECAUSE THE COMPILER IS NEEDED BY TWO PACKAGES OUT OF 273
# AND THE FAILURE IS LOUD. `deepspeed` and `langdetect` are the only two distributions in the
# locked cuda12 set that arrive as source and have to be built here; everything else, CUDA
# extensions included, is a prebuilt wheel. The Deep Learning AMI ships a toolchain, so this
# should never fire -- and if it does, the sync stops with a compiler error rather than
# producing something subtly wrong, which is why it is not worth refusing a node over.
command -v cc > /dev/null 2>&1 ||
  echo "WARNING: no C compiler on PATH; deepspeed and langdetect build from source" >&2

# The cards, asked the same way the bootstrap asks. This is not a duplicate of the boot-time
# check: this script may run days into a block, and a card that has fallen off the bus since
# then is worth finding now rather than at the first backward pass. No retry loop, unlike the
# bootstrap -- that one races the driver loading on a fresh boot, and this one does not.
command -v nvidia-smi > /dev/null 2>&1 || die "nvidia-smi is absent; this is not a GPU node"
observed_gpus="$(nvidia-smi -L 2> /dev/null | grep -c '^GPU ' || true)"
[ "${observed_gpus}" = "${EXPECTED_GPUS}" ] ||
  die "the driver enumerates ${observed_gpus} devices and this node should have ${EXPECTED_GPUS}"
echo "driver     $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)"
echo "gpus       ${observed_gpus}"

mkdir -p "${ROOT}/bin"
# Written where the researcher will look for it rather than into the ambient environment: a
# venv on a filesystem this size wants its cache beside it, and $HOME is on the root volume.
export UV_CACHE_DIR="${ROOT}/cache"

# AND THE INTERPRETER TOO, WHICH IS NOT THE SAME DIRECTORY AND IS THE ONE THAT BREAKS THINGS.
#
# uv keeps downloaded CPythons under $XDG_DATA_HOME/uv/python -- $HOME/.local/share -- and not
# in its cache, and the venv it builds is a symlink to one of them. Left at the default that
# puts the actual interpreter on the root EBS volume while the venv pointing at it is on
# /scratch, which is a 14 GB environment depending on a file somewhere else, on a smaller and
# slower disk, under the home directory of whoever happened to run this. Two people, or one
# person through Systems Manager and then through a shell, do not reliably have the same HOME.
#
# This was not a theory. Building the venv with the default and then losing that directory
# left `venv/bin/python` a broken symlink and every uv command answering "Broken symlink at
# .../venv/bin/python3, was the underlying Python interpreter removed?". Keeping the
# interpreter under ROOT makes the whole environment one self-contained directory.
export UV_PYTHON_INSTALL_DIR="${ROOT}/python"

# A venv path the drain can see would be silently expensive rather than broken, so it is
# checked rather than trusted to the comment above it. The test is the drain's own glob.
case "${ROOT}" in
  /scratch/.*) ;;
  /scratch/*)
    echo "WARNING: ${ROOT} sits directly under /scratch, where 'edullm-node drain' will" >&2
    echo "find it and copy the whole venv to S3 every five minutes. Prefer a name that" >&2
    echo "begins with a dot, which the drain's glob cannot match." >&2
    ;;
esac

echo "root       ${ROOT}"
df -h "${ROOT}" | tail -n 1 || true

# ---------------------------------------------------------------------------------------
# uv. Fetched into the tree rather than into $HOME, so that one person's install is every
# person's install and re-running this does not depend on who is logged in.
# ---------------------------------------------------------------------------------------
#
# THE VERSION IS PINNED TO THE ONE open-instruct's OWN DOCKERFILE PINS. That is the resolver
# this lockfile is read by in CI and in the research image build, and it was checked here
# against this uv.lock -- revision 3, the conflicting cuda12/cuda13 groups, the URL sources
# and the explicit pytorch index all read correctly. A newer uv would very probably also
# work and would also be a second opinion about a lockfile that already has one.
readonly UV_VERSION="${EDULLM_OI_UV_VERSION:-0.8.6}"
if [ -x "${UV}" ] && "${UV}" --version 2> /dev/null | grep -q "${UV_VERSION}"; then
  step "uv ${UV_VERSION} is already here"
else
  step "installing uv ${UV_VERSION}"
  # `env` in the second half of the pipe and not an assignment in front of curl. The
  # installer is `sh`, and `VAR=x curl ... | sh` gives the variable to curl, which has no use
  # for it -- the installer then writes to ~/.local/bin and edits a shell profile, and the
  # check below is what would catch it. UV_UNMANAGED_INSTALL rather than UV_INSTALL_DIR
  # because this is exactly the ephemeral case it is for: it places the binary and touches no
  # profile and no PATH, so nothing about the login shells on this node changes.
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" |
    env UV_UNMANAGED_INSTALL="${ROOT}/bin" sh > /dev/null
  [ -x "${UV}" ] || die "uv did not land at ${UV}"
fi
"${UV}" --version

# ---------------------------------------------------------------------------------------
# THE SOURCE TREE.
# ---------------------------------------------------------------------------------------
#
# AN EXISTING CHECKOUT IS LEFT ALONE, WHICH IS THE OPPOSITE OF WHAT `edullm-node run` DOES
# AND IS RIGHT FOR A DIFFERENT REASON. That helper clones fresh every time because the commit
# it records has to be a claim about what is going to run, and a tree with unknown local edits
# makes that claim false. This is a setup script for a place a person then works: they will
# edit files in this tree, and re-running a setup script -- which is the whole point of it
# being idempotent -- must not be the thing that deletes an afternoon. `--refresh` is the
# sentence somebody types when they mean to throw the tree away.
if [ -d "${TREE}/.git" ] && [ "${REFRESH}" = no ]; then
  step "using the checkout already at ${TREE}"
  echo "branch     $(git -C "${TREE}" rev-parse --abbrev-ref HEAD 2> /dev/null || echo detached)"
  echo "commit     $(git -C "${TREE}" rev-parse HEAD)"
  echo "(pass --refresh to discard this and clone ${BRANCH} again)"
else
  step "cloning ${REPOSITORY} at ${BRANCH}"
  rm -rf "${TREE}"
  mkdir -p "$(dirname "${TREE}")"
  # GIT IS TOLD NOT TO ASK FOR A PASSWORD, FOR THE REASON `edullm-node run` RECORDS AGAINST
  # THE SAME LINE. This node holds no GitHub credential and a Systems Manager invocation has
  # no terminal, so a prompt is a hang rather than a question. Git's own message is about
  # failing to read a username, which names neither the design nor the way out of it.
  #
  # NOT --depth 1, WHICH IS THE OPPOSITE OF `edullm-node run` AND IS ABOUT PEOPLE RATHER THAN
  # PACKAGING. This tree is where somebody then works for a week: they will want `git log`,
  # `git diff` against the branch point, and the ability to check out something else without
  # re-cloning. That is worth the extra seconds here. It is deliberately NOT for
  # setuptools_scm's benefit -- see the version note below, which is a separate problem a
  # deep clone does not solve.
  if ! GIT_TERMINAL_PROMPT=0 git clone --branch "${BRANCH}" \
    "https://github.com/${REPOSITORY}.git" "${TREE}"; then
    echo "this node holds no GitHub credential, so a private repository cannot be cloned" >&2
    echo "here at all. On a public one the branch is gone or GitHub refused." >&2
    die "could not clone ${REPOSITORY}@${BRANCH}"
  fi
  echo "commit     $(git -C "${TREE}" rev-parse HEAD)"
fi

cd "${TREE}"
commit="$(git rev-parse HEAD)"

# ---------------------------------------------------------------------------------------
# THE ENVIRONMENT.
# ---------------------------------------------------------------------------------------
#
# PYTHONPATH IS CLEARED HERE AND NOT ONLY AT THE ASSERTIONS. On the DLAMI host it is unset
# and this line does nothing, which is the ordinary case. It exists for the day somebody runs
# this file inside the OLMo-core training image, where PYTHONPATH=/opt/olmo-core/src would
# put a second, different OLMo-core in front of the one uv.lock pins -- for the build as well
# as for the run.
unset PYTHONPATH
export VIRTUAL_ENV="${VENV}"
export UV_PROJECT_ENVIRONMENT="${VENV}"

# THE VERSION IS STATED RATHER THAN DERIVED, BECAUSE THERE IS NOTHING HERE TO DERIVE IT FROM.
# open-instruct's build backend is setuptools_scm, which reads the version out of the tags git
# can see. `edu-llm/open-instruct` carries no tags at all -- `git ls-remote --tags` returns
# nothing, where upstream `allenai/open-instruct` has three -- so cloning deeper does not help
# and setuptools_scm falls back to counting commits, which produces a number that means
# nothing and warns while doing it. The commit is the only honest identifier this checkout
# has, so it is the one put in the version, and the failure mode where the build cannot name
# a version at all stops existing. `.edullm/Dockerfile` sets the same variable for a related
# reason: its build context has no .git in it whatsoever.
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPEN_INSTRUCT="0.0.0+edullm.g$(git rev-parse --short HEAD)"
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPEN_INSTRUCT

step "installing the locked dependency set (about 6 GiB the first time, seconds after that)"

# THE COMMAND `.edullm/Dockerfile` RUNS, WORD FOR WORD. The two steps after it are not in that
# file and each says below why it has to be here.
#
# --frozen, so this resolves nothing: every version installed is one uv.lock already names,
# including the three flash-attn wheels and the cu128 torch, which come from release URLs and
# an explicit index rather than from PyPI. A resolution here would be a second opinion about a
# lockfile that the image build and CI both already read, taken on a machine costing money by
# the minute.
#
# --no-default-groups --group cuda12 is that Dockerfile's line exactly. cuda12 rather than
# cuda13 because the two are declared conflicting and cuda12 is what pyproject.toml defaults
# to and what CI exercises; dropping the defaults drops `dev`, which is pytest, ruff, mkdocs
# and pre-commit -- a contributor's toolchain rather than anything post-training needs.
#
# Split into dependencies and then the project, again as the Dockerfile does, so that a
# re-run after somebody edits the tree reinstalls the project in a second or two and leaves
# the six gigabytes below it untouched.
"${UV}" sync --frozen --no-default-groups --group cuda12 --no-install-project

# THE ONE PACKAGE THE cuda12 GROUP DOES NOT CARRY AND open_instruct CANNOT START WITHOUT.
#
# `open_instruct/utils.py` line 56 is a bare `import beaker`, at module scope, guarded by
# nothing -- and `beaker-py` is declared only in the `dev` group, which `--no-default-groups`
# drops. So the locked cuda12 set produces an environment where `import open_instruct.utils`
# raises ModuleNotFoundError, and utils is imported by every training entrypoint here:
# grpo_fast, finetune, dpo_tune_cache. Without this line the install completes, the torch and
# vllm checks pass, and post-training cannot be started at all. Observed, on 2026-08-08, by
# running exactly the Dockerfile's two commands into a clean venv.
#
# WORTH KNOWING SEPARATELY: this means `.edullm/Dockerfile`'s own final assertion -- the
# `import open_instruct.utils, open_instruct.dataset_transformation` line -- cannot pass at
# this commit either. That is a thing to fix in open-instruct rather than here, most likely by
# moving beaker-py into the main dependencies or putting the import behind a try.
#
# THE VERSION IS READ OUT OF uv.lock RATHER THAN WRITTEN HERE, in the same spirit as the
# Dockerfile deriving its botocore pin from the botocore uv already installed. A number typed
# into this file would be a second copy of what the lockfile pins, free to go stale against it
# in silence.
#
# --no-deps BECAUSE NOTHING HERE MAY MOVE. beaker-py's six dependencies -- google-crc32c,
# grpcio, packaging, protobuf, pyyaml, requests -- are all already in the synced set at the
# versions uv.lock chose, and a resolving install is precisely the operation that can quietly
# replace one of them. If one ever is genuinely missing, the import check below says so.
#
# --no-config BECAUSE OTHERWISE uv REFUSES THE COMMAND ENTIRELY. Run anywhere inside this
# project, `uv pip install` reads its pyproject.toml, and uv 0.8.6 answers:
#
#   error: `torch` was declared as an extra build dependency with `match-runtime = true`,
#   but was not found in the resolution
#
# `[tool.uv.extra-build-dependencies]` binds flash-attn's build to the runtime torch, and a
# one-package resolution has no torch in it to match against. `--no-deps` alone does not avoid
# it -- that was tried. Changing directory out of the project also works and is what
# `.edullm/Dockerfile` does before its own checks, but saying `--no-config` says what is meant
# rather than depending on where the shell happens to be standing.
step "adding beaker-py, which open_instruct.utils imports and the cuda12 group omits"
beaker_version="$("${VENV}/bin/python" -c "
import tomllib
lock = tomllib.load(open('uv.lock', 'rb'))
print(next(p['version'] for p in lock['package'] if p['name'] == 'beaker-py'))
")"
[ -n "${beaker_version}" ] || die "uv.lock names no beaker-py, so open_instruct.utils cannot load"
"${UV}" pip install --no-deps --no-config "beaker-py==${beaker_version}"

# --no-config HERE FOR THE SAME REASON IT IS ON THE LINE ABOVE, AND THIS ONE IS A DEVIATION
# FROM `.edullm/Dockerfile` WORTH BEING EXPLICIT ABOUT. That file writes this step as a bare
# `uv pip install --no-deps .`, and with the uv it pins -- 0.8.6, which is the uv pinned here
# too -- that command cannot run in this project at all. It stops on the same complaint the
# beaker line hit: `torch` was declared as an extra build dependency with `match-runtime =
# true`, but was not found in the resolution. uv validates every `[tool.uv.extra-build-
# dependencies]` entry against the resolution in hand, and the resolution for a single local
# project has no torch in it. A much newer uv (0.12.3, checked) accepts the bare form, so this
# is a behaviour that changed rather than a rule.
#
# The practical reading is that the research image build is broken at this step at commit
# 1cf5f26 and wants either this flag or a newer pin. The `[tool.uv.extra-build-dependencies]`
# block is recent in that tree, which fits.
"${UV}" pip install --no-deps --no-config .

# The three corpora open_instruct's data paths load at import time. The research image
# downloads them at build time because a Batch job has no general egress; this node does have
# egress, but a fetch that happens once here is better than one racing eight dataloader
# workers the first time somebody trains.
step "nltk corpora"
"${VENV}/bin/python" -m nltk.downloader -d "${ROOT}/nltk_data" punkt punkt_tab words \
  > /dev/null 2>&1 || echo "nltk download did not complete; open_instruct will retry at use"

# ---------------------------------------------------------------------------------------
# WHAT THE ENVIRONMENT CLAIMS. The research image's assertions, plus the one it cannot make.
# ---------------------------------------------------------------------------------------
#
# `torch.version.cuda` is the load-bearing one and the most expensive silent failure
# available: a resolution that put the CPU wheel here leaves an environment that imports,
# runs and trains -- on the CPU, on a p5, with every log line looking ordinary.
#
# vllm and flash-attn are checked by version rather than by import in the image, because that
# build has no GPU. This node does, so they are imported: an import is the check that finds a
# wheel built against the wrong torch ABI, and finding it here costs seconds where finding it
# in a training run costs the queue.
#
# The open_instruct check names submodules rather than the package. Stopping at the package
# name proves a distribution is on disk, not that the code loads -- the top-level package
# imports lazily and nothing under the training paths is touched until a workload asks.
#
# And the last one is the whole reason this is a node and not a laptop.
step "checking what was installed"
"${VENV}/bin/python" - "${EXPECTED_GPUS}" <<'CHECKS'
import sys
from importlib.metadata import version

expected = int(sys.argv[1])

import torch
assert torch.version.cuda, "the CUDA build of torch was replaced by a CPU wheel"
print("torch         ", torch.__version__, "cuda", torch.version.cuda)

import vllm
import flash_attn
print("vllm          ", version("vllm"))
print("flash-attn    ", version("flash-attn"))

import open_instruct.utils, open_instruct.dataset_transformation  # noqa: F401
print("open-instruct ", version("open-instruct"), "(loads)")

count = torch.cuda.device_count()
print("torch sees    ", count, "device(s)")
assert count == expected, f"torch sees {count} devices and this node should have {expected}"
CHECKS

printf '{"repository":"%s","branch":"%s","commit":"%s","venv":"%s","installed_at":"%s","gpus":%s}\n' \
  "${REPOSITORY}" "${BRANCH}" "${commit}" "${VENV}" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${EXPECTED_GPUS}" > "${STAMP}"

# ONE FILE TO SOURCE, RATHER THAN THREE THINGS TO REMEMBER. `activate` alone is not enough
# here: it does not know about NLTK_DATA, and -- the part that matters -- it does not unset
# PYTHONPATH. Somebody who activates this venv from a shell inside the OLMo-core container
# gets the image's OLMo-core imported ahead of the pinned one, silently, which is the exact
# failure this whole arrangement is built to avoid. So the activation people are told to use
# is this wrapper, and it closes that door on the way through.
cat > "${ROOT}/env.sh" <<ENVFILE
# Source this to work with open-instruct on this node.
unset PYTHONPATH
export NLTK_DATA="${ROOT}/nltk_data"
export UV_CACHE_DIR="${ROOT}/cache"
export UV_PYTHON_INSTALL_DIR="${ROOT}/python"
export PATH="${ROOT}/bin:\${PATH}"
. "${VENV}/bin/activate"
ENVFILE
chmod 0644 "${ROOT}/env.sh"

cat <<DONE

open-instruct is installed on node ${EDULLM_BLOCK_NODE:-8} and sees ${EXPECTED_GPUS} GPUs. SUCCEEDED.

  source ${ROOT}/env.sh
  cd ${TREE}

Use that rather than ${VENV}/bin/activate: it also sets NLTK_DATA and clears PYTHONPATH,
which is what keeps the OLMo-core inside the training image from shadowing the one this
environment pins. A workflow that activates nothing wants:

  env -u PYTHONPATH NLTK_DATA=${ROOT}/nltk_data ${VENV}/bin/python -m open_instruct.grpo_fast --help

This lives on /scratch and goes away when the block does; re-run this file to rebuild it.
DONE
