#!/usr/bin/env bash
# What one capacity block node does when it is one rank group of a job spanning several of them.
#
# THIS FILE IS NOT RUN FROM THIS REPOSITORY AND NOTHING HERE EXECUTES IT. It is the tail of the
# Systems Manager command `tools/block_run_distributed.py` sends: that tool prints a handful of
# `NAME=value` lines and concatenates this file after them, which is exactly the arrangement
# `.github/workflows/block-launch-fleet.yml` uses for `infra/block-node-bootstrap.sh` and is
# here for the same reason -- a script a reader, `bash -n` and `shellcheck` can each take on
# their own, rather than a three-hundred-line quoted string inside YAML or inside Python.
#
# IT IS SENT OVER SYSTEMS MANAGER RATHER THAN BAKED INTO USER-DATA, WHICH IS NOT A PREFERENCE.
# The bootstrap compresses to within a few thousand bytes of what EC2 accepts as user-data, and
# `tests/test_block_workflows.py` measures that on every pull request. Adding this to it would
# spend most of the remaining headroom on a path the fleet does not need in order to boot, and
# would mean a fleet already up could not be given a fixed launcher without being relaunched.
#
# WHY IT DOES NOT GO THROUGH `edullm-node run`, WHICH IS THE FIRST THING TO CHECK BEFORE
# CHANGING IT. That helper is the single-node path and the container it starts is a different
# container: bridge networking, no fabric devices, no rendezvous. A job spanning machines needs
# host networking -- the c10d store binds inside the container and has to be dialled from the
# other nodes -- the EFA character devices where there are any, and a launcher whose flags
# depend on which machines were claimed thirty seconds ago. Those are not arguments that could
# be added to `run`; they are what makes this a second shape.
#
# WHAT IT DELIBERATELY KEEPS IDENTICAL is everything the rest of the lane reads. The claim file
# has the same fields, the container is named `edullm-<run>` like every other, the log lands at
# `/scratch/<run>/log/train.log` where the sync unit already looks, and the run directory sits
# under `/scratch` where the drain timer already walks. So `edullm-node status`,
# `tools/block_status.py`, the drain and `block-logs.yml` all see a distributed run without
# knowing there is such a thing.

set -euo pipefail

# THE SETTINGS THE TOOL PREPENDS. `:?` on every one of them rather than a default, for the
# reason the bootstrap gives: a default here is a value that looks right on a node nobody
# configured, and the ones that would hurt most -- the rendezvous endpoint and the node count --
# are exactly the two a plausible default gets silently wrong.
readonly RUN_NAME="${EDULLM_DIST_RUN:?the tool must prepend EDULLM_DIST_RUN}"
readonly WHO="${EDULLM_DIST_WHO:?the tool must prepend EDULLM_DIST_WHO}"
readonly REPOSITORY="${EDULLM_DIST_REPOSITORY:?the tool must prepend EDULLM_DIST_REPOSITORY}"
readonly BRANCH="${EDULLM_DIST_BRANCH:?the tool must prepend EDULLM_DIST_BRANCH}"
readonly LAUNCH_BASE64="${EDULLM_DIST_LAUNCH_BASE64:?the tool must prepend EDULLM_DIST_LAUNCH_BASE64}"
readonly OUTPUT_NODE="${EDULLM_DIST_OUTPUT_NODE:?the tool must prepend EDULLM_DIST_OUTPUT_NODE}"
readonly WANDB_PROJECT="${EDULLM_DIST_WANDB_PROJECT:?the tool must prepend EDULLM_DIST_WANDB_PROJECT}"
readonly RENDEZVOUS_HOST="${EDULLM_DIST_RENDEZVOUS_HOST:?the tool must prepend EDULLM_DIST_RENDEZVOUS_HOST}"
readonly WORLD_SIZE="${EDULLM_DIST_WORLD_SIZE:?the tool must prepend EDULLM_DIST_WORLD_SIZE}"

# auto, efa or tcp. `auto` takes the fabric if the devices are there, which is what a fleet
# launched without EFA network interfaces needs, and `efa` refuses a node that has none --
# because somebody who typed it is asking for a guarantee rather than a preference, and a job
# that quietly falls back to the ordinary interface is several times slower with nothing
# anywhere saying so.
readonly FABRIC_MODE="${EDULLM_DIST_FABRIC:?the tool must prepend EDULLM_DIST_FABRIC}"

# Written by the bootstrap. Its absence means this node never finished booting, which the tool
# refuses on before it gets here -- but the failure without this check is `set -u` firing on a
# variable name several lines later, which names nothing.
if [ ! -f /etc/edullm-block.env ]; then
  echo "this node has no /etc/edullm-block.env, so its bootstrap never finished" >&2
  exit 1
fi
# shellcheck disable=SC1091  # written on the machine by infra/block-node-bootstrap.sh
. /etc/edullm-block.env

readonly TREE="${EDULLM_BLOCK_SCRATCH}/${RUN_NAME}"
readonly CLAIM="${EDULLM_BLOCK_STATE}/claim.json"

# THE SAME STRING ON EVERY NODE, WHICH IS WHY IT IS BUILT FROM A NODE NUMBER RATHER THAN
# ARRIVING AS A URI. A distributed checkpoint is one directory written by all of the ranks
# together, so a per-machine prefix produces as many partial saves as there are machines and no
# whole one. The elected node's number is what is passed, every machine builds the same prefix
# out of the bucket and the reservation the bootstrap already wrote into its settings, and no
# copy of either lives anywhere else. It points at a node prefix rather than somewhere new so
# that the drain report -- which reads a run's checkpoints under the node it found the run
# directory on -- goes on finding them.
readonly OUTPUT_PREFIX="s3://${EDULLM_BLOCK_OUTPUTS_BUCKET}/block/${EDULLM_BLOCK_RESERVATION}/node-${OUTPUT_NODE}/${RUN_NAME}"

# ---------------------------------------------------------------------------------------
# THE TREE. Cloned fresh, for the reason the single-node helper gives: a directory left by
# an earlier attempt is at an unknown commit with unknown local edits, and the commit this
# records would then describe the branch rather than what is about to run.
# ---------------------------------------------------------------------------------------
#
# EVERY NODE CLONES ITS OWN COPY RATHER THAN ONE NODE CLONING AND SHARING. There is no shared
# filesystem here and standing one up for this would be a component that can fail inside a
# window that cannot be extended. Eight clones of a depth-one tree run concurrently and cost
# what one costs.
if [ -n "$(docker ps --quiet --filter "name=^edullm-${RUN_NAME}$" 2> /dev/null)" ]; then
  echo "edullm-${RUN_NAME} is already running on node ${EDULLM_BLOCK_NODE}" >&2
  exit 1
fi

rm -rf "${TREE}"
mkdir -p "${TREE}/log"
git clone --depth 1 --branch "${BRANCH}" "https://github.com/${REPOSITORY}.git" "${TREE}/repo"
commit="$(git -C "${TREE}/repo" rev-parse HEAD)"

# The same six fields `edullm-node`'s own `write_claim` writes, so that `status`, the drain and
# `tools/block_status.py` read a distributed run exactly as they read any other.
# `tests/test_block_distributed_tool.py` holds the two field lists against each other.
printf '{"run":"%s","who":"%s","repository":"%s","branch":"%s","commit":"%s","started_at":"%s"}\n' \
  "${RUN_NAME}" "${WHO}" "${REPOSITORY}" "${BRANCH}" "${commit}" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${CLAIM}"

# ---------------------------------------------------------------------------------------
# THE FABRIC, DECIDED ON THE MACHINE BECAUSE IT IS A FACT ABOUT THE MACHINE.
# ---------------------------------------------------------------------------------------
#
# EFA IS NOT A PROPERTY OF THE INSTANCE TYPE. A `p5.48xlarge` can carry thirty-two of them and
# carries none unless `run-instances` asked for them by name, and a launch that did not ask
# produces a machine with the driver installed, the plugin installed, three thousand two hundred
# gigabits of advertised fabric and no `/dev/infiniband` at all. So the question is asked of the
# device nodes rather than of the shape, and the answer is printed.
#
# `.github/workflows/block-launch-fleet.yml` does ask, and asks for the layout AWS documents for
# this shape: one ordinary interface carrying the IP, and thirty-two `efa-only` interfaces
# carrying no address at all. Both `efa` and `efa-only` produce the same character devices here
# -- the difference between them is whether an ENA shares the interface, which is invisible from
# the machine -- so this probe is unchanged by which of the two a launch asked for. What it
# cannot see is a fleet that got *some* of its devices; the launch workflow counts them against
# what it asked for and refuses there, because that is where the number is known.
#
# The AMI already carries both halves of the host side: the Deep Learning Base OSS NVIDIA
# driver image ships the EFA installer and the `aws-ofi-nccl` plugin under `/opt/amazon`, so
# nothing is installed here. What the *container* carries is the other question -- the training
# image is a bare Python base with torch from an index, and torch's bundled NCCL has no idea
# the fabric exists -- which is why the host's `/opt/amazon` is mounted in rather than the
# plugin being rebuilt. The libraries there are linked against an older glibc than the image
# has, which resolves; the reverse would not.
fabric=tcp
fabric_arguments=()
if [ -e /dev/infiniband/uverbs0 ] && [ -d /opt/amazon/ofi-nccl/lib ]; then
  fabric=efa
fi
if [ "${FABRIC_MODE}" = tcp ]; then
  fabric=tcp
fi
if [ "${FABRIC_MODE}" = efa ] && [ "${fabric}" != efa ]; then
  echo "node ${EDULLM_BLOCK_NODE} has no EFA device, and fabric=efa was asked for" >&2
  echo "A p5 carries no EFA unless run-instances asked for the interfaces, so this node was" >&2
  echo "launched without them -- either before that landed, or with efa_interfaces=0. Read the" >&2
  echo "efa column of the launch summary. Dispatch with fabric=auto to run over TCP instead." >&2
  exit 1
fi

if [ "${fabric}" = efa ]; then
  for device in /dev/infiniband/*; do
    [ -e "${device}" ] || continue
    fabric_arguments+=(--device "${device}")
  done
  fabric_arguments+=(--volume /opt/amazon:/opt/amazon:ro)
  fabric_arguments+=(--env "LD_LIBRARY_PATH=/opt/amazon/efa/lib:/opt/amazon/ofi-nccl/lib")
  # `efa` by name rather than left to libfabric's own ranking. The provider it picks otherwise
  # depends on what else the image happens to expose, and picking `tcp` there is the failure
  # that presents as the fabric being slow rather than as the fabric being unused.
  fabric_arguments+=(--env FI_PROVIDER=efa)
  # An absolute path rather than a name. NCCL resolves a bare plugin name against its own
  # search order, which does not include a directory bind-mounted from the host after the
  # image was built, and a plugin it cannot find is a silent fall back to sockets.
  plugin="$(find /opt/amazon/ofi-nccl/lib -maxdepth 1 -name 'libnccl-net*.so*' 2> /dev/null |
    sort | head -n 1)"
  if [ -n "${plugin}" ]; then
    fabric_arguments+=(--env "NCCL_NET_PLUGIN=${plugin}")
  fi
  # FI_EFA_USE_DEVICE_RDMA and NCCL_PROTO=simple are deliberately absent. Both are AWS's advice
  # for aws-ofi-nccl at or below 1.6, the AMI carries 1.17 or later, and on that software they
  # are either a no-op or a cap on the protocol NCCL would have chosen better.
else
  # The interface holding the default route rather than a name typed in. It is `ens5` on some
  # of this AMI family's boots and `enX0` on others, and NCCL given a name that does not exist
  # does not fall back -- it fails to find any usable interface and the job dies at
  # initialisation on all of the nodes at once.
  interface="$(ip route show default 2> /dev/null | awk '{print $5; exit}')"
  if [ -z "${interface}" ]; then
    echo "no default route on node ${EDULLM_BLOCK_NODE}, so nothing can reach the others" >&2
    exit 1
  fi
  fabric_arguments+=(--env "NCCL_SOCKET_IFNAME=${interface}")
  fabric_arguments+=(--env "GLOO_SOCKET_IFNAME=${interface}")
  # Off explicitly. Without a device NCCL still probes for one, spends its initialisation
  # timeout doing it, and reports the delay as a bootstrap problem rather than as a missing
  # fabric.
  fabric_arguments+=(--env NCCL_IB_DISABLE=1)
fi

# ---------------------------------------------------------------------------------------
# THE LAUNCHER, CHECKED INSIDE THE IMAGE BEFORE IT IS RUN IN ANGER.
# ---------------------------------------------------------------------------------------
#
# The image is pre-pulled, so this costs about a second and turns "every node exited 127" into
# a sentence naming the reason. A launcher that is not on PATH is a plausible state: the
# training image is built from a bare Python base and what puts `torchrun` in `/usr/local/bin`
# is the torch wheel's console script, which a build that installed torch differently would not
# have.
launcher="$(printf '%s' "${LAUNCH_BASE64}" | base64 --decode | awk '{print $1; exit}')"
if ! docker run --rm --entrypoint sh "${EDULLM_BLOCK_IMAGE}" -c "command -v ${launcher}" \
  > /dev/null 2>&1; then
  echo "${launcher} is not on PATH inside ${EDULLM_BLOCK_IMAGE}" >&2
  exit 1
fi

# Base64 rather than the command itself, because it arrives through a JSON document inside a
# shell prelude inside a Systems Manager parameter, and a training command carrying a quote, a
# dollar sign or a pipe has three layers to survive. One decode at the point of use is the only
# arrangement where none of them can re-interpret it.
launch="$(printf '%s' "${LAUNCH_BASE64}" | base64 --decode)"

wandb_key="$(aws secretsmanager get-secret-value \
  --region "${EDULLM_BLOCK_IMAGE_REGION}" \
  --secret-id "${EDULLM_BLOCK_WANDB_SECRET_ID}" \
  --query SecretString --output text 2> /dev/null || true)"

# ---------------------------------------------------------------------------------------
# THE CONTAINER. Three things about it differ from the single-node one and all three are
# required rather than tuning.
# ---------------------------------------------------------------------------------------
#
# `--network host` is the first and is not optional. The c10d rendezvous binds a listener on
# the elected node and every other node dials it at that machine's private address; on the
# default bridge network the listener is inside a namespace nothing outside the machine can
# reach, and what that looks like is seven nodes timing out against a store that is running.
#
# `--ipc=host` and the two ulimits are AWS's own NCCL recipe and are the same three the
# single-node path carries. The default 64 MiB of /dev/shm is where a multi-GPU run dies
# several minutes in complaining about a bootstrap timeout that names nothing, and `memlock`
# unlimited is what lets the fabric pin the buffers it registers.
#
# `EDULLM_CHECKPOINT_DIR` is the same string on every node and points at the elected node's
# prefix rather than at each machine's own. A distributed checkpoint is one directory written
# by all of the ranks together, so a per-node prefix would produce as many partial checkpoints
# as there are machines and no whole one -- and pointing it at the elected node means the drain
# report, which reads a run's checkpoints under the node prefix it finds the run directory on,
# goes on finding them.
#
# The Weights and Biases identifiers are the same on every node for the same kind of reason and
# not for the same reason. OLMo-core writes to W&B from global rank zero only, so one run id
# across the fleet produces one chart rather than eight, and it is the id the launch report
# prints a link to. `WANDB_RUN_GROUP` is set as well so that a fleet where somebody has turned
# per-rank logging on still collapses into one group instead of eight unrelated runs.
docker run --detach \
  --name "edullm-${RUN_NAME}" \
  --gpus all \
  --network host \
  --ipc=host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  "${fabric_arguments[@]}" \
  --volume "${TREE}/repo:/work" \
  --volume "${TREE}/log:/work/log" \
  --workdir /work \
  --env "EDULLM_RUN_ID=${RUN_NAME}" \
  --env "EDULLM_COMMIT_SHA=${commit}" \
  --env "EDULLM_OUTPUT_BUCKET=${EDULLM_BLOCK_OUTPUTS_BUCKET}" \
  --env "EDULLM_OUTPUT_PREFIX=${OUTPUT_PREFIX}/" \
  --env "EDULLM_CHECKPOINT_DIR=${OUTPUT_PREFIX}/checkpoints/" \
  --env "EDULLM_DATA_BUCKET=${EDULLM_BLOCK_DATA_BUCKET}" \
  --env "AWS_DEFAULT_REGION=${EDULLM_BLOCK_REGION}" \
  --env "AWS_REGION=${EDULLM_BLOCK_REGION}" \
  --env "WANDB_API_KEY=${wandb_key}" \
  --env "WANDB_ENTITY=eduLLM" \
  --env "WANDB_PROJECT=${WANDB_PROJECT}" \
  --env "EDULLM_WANDB_PROJECT=${WANDB_PROJECT}" \
  --env "WANDB_RUN_GROUP=${RUN_NAME}" \
  --env "WANDB_RUN_ID=${RUN_NAME}" \
  --env "WANDB_NAME=${RUN_NAME}" \
  --env NCCL_DEBUG=INFO \
  --env NCCL_DEBUG_SUBSYS=INIT,NET,ENV \
  --env TORCH_NCCL_ASYNC_ERROR_HANDLING=1 \
  --env NCCL_ASYNC_ERROR_HANDLING=1 \
  --env TORCHELASTIC_ERROR_FILE=/work/log/torchelastic-error.json \
  "${EDULLM_BLOCK_IMAGE}" \
  bash -lc "set -o pipefail; ${launch} 2>&1 | tee -a /work/log/train.log"

# Tab-separated, which is the convention `block_fleet.REMOTE_READING_SCRIPT` set and is here
# for its reason: emitting JSON from shell means quoting by hand or depending on `jq`, and `jq`
# is not on every image this AMI family has shipped.
printf 'node\t%s\n' "${EDULLM_BLOCK_NODE}"
printf 'commit\t%s\n' "${commit}"
printf 'fabric\t%s\n' "${fabric}"
printf 'container\tedullm-%s\n' "${RUN_NAME}"
printf 'rendezvous\t%s\n' "${RENDEZVOUS_HOST}"
printf 'world_size\t%s\n' "${WORLD_SIZE}"
printf 'log\t%s\n' "${TREE}/log/train.log"
