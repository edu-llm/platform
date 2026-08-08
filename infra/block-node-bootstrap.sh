#!/usr/bin/env bash
# What one capacity block node does to itself before anybody is given it.
#
# THIS FILE IS NOT RUN FROM THIS REPOSITORY AND NOTHING HERE EXECUTES IT. It is the tail of the
# user-data blob `.github/workflows/block-launch-fleet.yml` builds: that workflow prints a
# handful of `NAME=value` lines and then concatenates this file after them, so every setting
# below arrives as an ordinary shell variable already assigned in the same script. A second
# shebang halfway through a file is a comment, which is why this one can keep its own and stay
# a file `bash -n` and a reader can both take on their own.
#
# WHY A SEPARATE FILE RATHER THAN A HEREDOC IN THE WORKFLOW. User-data is the one thing here
# nobody can watch fail. It runs once, unattended, on a machine that has already been paid for,
# and the only evidence of a mistake is a node that never becomes ready -- so it wants to be
# the most readable artifact in the change rather than the least. Inlined it would be a
# 300-line quoted string inside YAML, where a stray backtick is a syntax error nothing checks
# until 11:30 UTC on Saturday.
#
# WHAT IT REFUSES TO PROCEED PAST, AND WHY THOSE AND NOT OTHERS. A wrong GPU count means the
# reservation handed back something other than what was bought, or the driver did not come up,
# and every hour after that is a full-rate hour spent training on nothing. That is fatal here.
# A missing local NVMe is not: the node still trains, it just trains against the root volume,
# and refusing would take a working machine out of a fleet of eight for a reason somebody can
# read off `edullm-node status` and work around. The rule is that anything invisible from the
# outside is fatal and anything a person can see and route around is recorded instead.
#
# EVERY FAILURE LANDS IN THE SAME PLACE, WHICH IS THE POINT OF THE TRAP. `cloud-init` writes
# its own log and nobody reads it; what the launch workflow polls for is one file. A node that
# died at step three writes the reason into that file rather than leaving it absent, so the
# difference between "still booting" and "will never be ready" is readable four minutes in
# instead of at the end of a timeout.

set -euo pipefail

# THE SETTINGS THE WORKFLOW PREPENDS. `:?` rather than a default on every one of them: a
# default here is a value that looks right on a node nobody configured, and the two that would
# hurt most -- the bucket the checkpoints go to and the image the training runs in -- are
# exactly the two a plausible default would silently get wrong.
readonly RESERVATION_ID="${EDULLM_BLOCK_RESERVATION:?the launch workflow must prepend EDULLM_BLOCK_RESERVATION}"
readonly NODE_NUMBER="${EDULLM_BLOCK_NODE:?the launch workflow must prepend EDULLM_BLOCK_NODE}"
readonly OUTPUTS_BUCKET="${EDULLM_BLOCK_OUTPUTS_BUCKET:?the launch workflow must prepend EDULLM_BLOCK_OUTPUTS_BUCKET}"
readonly DATA_BUCKET="${EDULLM_BLOCK_DATA_BUCKET:?the launch workflow must prepend EDULLM_BLOCK_DATA_BUCKET}"
readonly TRAINING_IMAGE="${EDULLM_BLOCK_IMAGE:?the launch workflow must prepend EDULLM_BLOCK_IMAGE}"
readonly IMAGE_REGION="${EDULLM_BLOCK_IMAGE_REGION:?the launch workflow must prepend EDULLM_BLOCK_IMAGE_REGION}"
readonly BLOCK_REGION="${EDULLM_BLOCK_REGION:?the launch workflow must prepend EDULLM_BLOCK_REGION}"
readonly EXPECTED_GPUS="${EDULLM_BLOCK_EXPECTED_GPUS:?the launch workflow must prepend EDULLM_BLOCK_EXPECTED_GPUS}"
readonly WANDB_SECRET_ID="${EDULLM_BLOCK_WANDB_SECRET_ID:?the launch workflow must prepend EDULLM_BLOCK_WANDB_SECRET_ID}"
readonly LOG_SYNC_SECONDS="${EDULLM_BLOCK_LOG_SYNC_SECONDS:?the launch workflow must prepend EDULLM_BLOCK_LOG_SYNC_SECONDS}"

readonly STATE_DIRECTORY=/var/lib/edullm
readonly SETTINGS_FILE=/etc/edullm-block.env
readonly READY_FILE="${STATE_DIRECTORY}/ready.json"
readonly BOOTSTRAP_LOG="${STATE_DIRECTORY}/bootstrap.log"
readonly SCRATCH=/scratch

# Where everything this node produces goes, and it is derived once here so that the log sync,
# the checkpoint variable and the URI the run workflow prints cannot disagree about it. The
# reservation is in the path because two blocks in one month is two fleets, and a node number
# repeats across them.
readonly S3_PREFIX="block/${RESERVATION_ID}/node-${NODE_NUMBER}"

mkdir -p "${STATE_DIRECTORY}"
exec > >(tee -a "${BOOTSTRAP_LOG}") 2>&1
echo "edullm block node ${NODE_NUMBER} bootstrap starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# The last line of every failed bootstrap, whatever failed. `$BASH_COMMAND` names the command
# rather than the step, which is the difference between "step 3 failed" and knowing that the
# ECR login was refused.
failed() {
  local status=$?
  printf '{"node":%s,"failed_at":"%s","exit_status":%s,"command":"%s"}\n' \
    "${NODE_NUMBER}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${status}" "${BASH_COMMAND//\"/}" \
    > "${STATE_DIRECTORY}/bootstrap-failed.json"
  echo "BOOTSTRAP FAILED: ${BASH_COMMAND} exited ${status}" >&2
}
trap failed ERR

# ---------------------------------------------------------------------------------------
# THE CARDS. First, and fatal, because everything after it is arrangement and this is the
# thing that was bought.
# ---------------------------------------------------------------------------------------
#
# `nvidia-smi -L` rather than a utilisation query: this asks how many devices the driver
# enumerates, which is the number that has to be eight. A utilisation query on a card the
# driver has not attached answers nothing rather than answering zero.
#
# The retry is not defensive padding. The NVIDIA kernel module loads asynchronously on a
# fresh Nitro boot and `nvidia-smi` answers "couldn't communicate with the NVIDIA driver"
# for the first several seconds of a machine that is completely healthy. Failing on the
# first read would terminate a good node out of a fleet of eight.
observed_gpus=0
driver_version=unknown
for _attempt in $(seq 1 30); do
  if nvidia-smi -L > "${STATE_DIRECTORY}/nvidia-smi.txt" 2>/dev/null; then
    observed_gpus="$(grep -c '^GPU ' "${STATE_DIRECTORY}/nvidia-smi.txt" || true)"
    driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)"
    break
  fi
  sleep 10
done

if [ "${observed_gpus}" -ne "${EXPECTED_GPUS}" ]; then
  echo "the driver enumerates ${observed_gpus} devices and the instance type has ${EXPECTED_GPUS}" >&2
  false
fi
echo "driver ${driver_version}, ${observed_gpus} devices"

# ---------------------------------------------------------------------------------------
# THE SCRATCH FILESYSTEM. Recorded rather than fatal; see the header for the line between
# the two.
# ---------------------------------------------------------------------------------------
#
# INSTANCE STORE IS IDENTIFIED BY MODEL AND NOT BY DEVICE NAME. On Nitro every disk is an
# NVMe namespace and the root EBS volume is `/dev/nvme0n1` on some launches and not on
# others, so a rule like "everything but nvme0n1" formats the root volume on the launch
# where the ordering came out the other way. `lsblk` reports the controller model, and
# instance store answers "Amazon EC2 NVMe Instance Storage" where EBS answers "Amazon
# Elastic Block Store". That string is the only reliable discriminator here.
#
# RAID0 ACROSS ALL OF THEM RATHER THAN ONE MOUNT PER DISK. A p5.48xlarge carries eight
# local NVMe devices. Mounting one gives a researcher an eighth of the disk they were told
# they had, and mounting eight gives them a decision to make about which one to write to at
# the moment they least want one. Striping is the failure mode nobody minds: instance store
# is already destroyed by a stop and by the end of the window, so the "one disk dies and
# takes the array" objection is about data that was never durable in the first place.
scratch_device=root-volume
mapfile -t instance_store < <(
  lsblk --nodeps --noheadings --output NAME,MODEL |
    awk '/Instance Storage/ {print "/dev/" $1}'
)

if [ "${#instance_store[@]}" -eq 1 ]; then
  scratch_device="${instance_store[0]}"
  mkfs.ext4 -F -m 0 "${scratch_device}"
elif [ "${#instance_store[@]}" -gt 1 ]; then
  scratch_device=/dev/md0
  mdadm --create --verbose "${scratch_device}" --level=0 --raid-devices="${#instance_store[@]}" \
    "${instance_store[@]}"
  mkfs.ext4 -F -m 0 "${scratch_device}"
fi

mkdir -p "${SCRATCH}"
if [ "${scratch_device}" != "root-volume" ]; then
  # `nofail` and no fstab entry. The array is rebuilt from nothing on every boot and this
  # fleet is never rebooted, so an fstab line would only ever be read on the one boot where
  # it names a device that no longer exists and holds the machine at the emergency prompt.
  mount -o discard,noatime "${scratch_device}" "${SCRATCH}"
else
  echo "no instance store device was found; /scratch is on the root volume" >&2
fi
# 1777 like /tmp, and for the same reason. Everybody who reaches this machine reaches it as
# `ssm-user` or as root, both of which already hold passwordless sudo, so a stricter mode
# here protects nothing and costs a sudo prompt in the middle of every helper.
chmod 1777 "${SCRATCH}"

# ---------------------------------------------------------------------------------------
# THE SETTINGS FILE. Written before the image pull because the helper below sources it and
# the pull is the first thing that uses one.
# ---------------------------------------------------------------------------------------
cat > "${SETTINGS_FILE}" <<SETTINGS
EDULLM_BLOCK_RESERVATION=${RESERVATION_ID}
EDULLM_BLOCK_NODE=${NODE_NUMBER}
EDULLM_BLOCK_OUTPUTS_BUCKET=${OUTPUTS_BUCKET}
EDULLM_BLOCK_DATA_BUCKET=${DATA_BUCKET}
EDULLM_BLOCK_IMAGE=${TRAINING_IMAGE}
EDULLM_BLOCK_IMAGE_REGION=${IMAGE_REGION}
EDULLM_BLOCK_REGION=${BLOCK_REGION}
EDULLM_BLOCK_WANDB_SECRET_ID=${WANDB_SECRET_ID}
EDULLM_BLOCK_LOG_SYNC_SECONDS=${LOG_SYNC_SECONDS}
EDULLM_BLOCK_S3_PREFIX=${S3_PREFIX}
EDULLM_BLOCK_SCRATCH=${SCRATCH}
EDULLM_BLOCK_STATE=${STATE_DIRECTORY}
SETTINGS
chmod 0644 "${SETTINGS_FILE}"

# ---------------------------------------------------------------------------------------
# THE IMAGE, PULLED ONCE NOW SO THAT NOBODY PAYS FOR IT LATER.
# ---------------------------------------------------------------------------------------
#
# A cold pull of the training image is minutes, and the whole design of this lane is that a
# researcher dispatches a workflow and their run starts. Paying the pull at boot spends it
# once across the fleet, in a window nobody is waiting in, instead of once per run in the
# window everybody is.
#
# THE REGISTRY IS IN us-east-1 AND THE FLEET IS IN us-east-2, WHICH IS FINE AND IS THE ONE
# CROSS-REGION DEPENDENCY HERE. ECR authorisation is per-region, so the login has to name
# the registry's region rather than the instance's; a pull authorised against us-east-2
# fails with "no basic auth credentials" against a repository that plainly exists. Data
# transfer between the two regions is charged, once, for one image.
registry="${TRAINING_IMAGE%%/*}"
aws ecr get-login-password --region "${IMAGE_REGION}" |
  docker login --username AWS --password-stdin "${registry}"
docker pull "${TRAINING_IMAGE}"
image_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "${TRAINING_IMAGE}")"

# ---------------------------------------------------------------------------------------
# THE HELPER. Quoted heredoc throughout: every `$` below belongs to the helper at the time
# somebody runs it, not to this script at the time the node boots.
# ---------------------------------------------------------------------------------------
cat > /usr/local/bin/edullm-node <<'HELPER'
#!/usr/bin/env bash
# What a person or a workflow does to one capacity block node.
#
# THE CLAIM FILE IS THE LOCK AND THE SPREADSHEET IS THE MINUTES. A Google Sheet records who
# intends to use a node; it cannot stop two people starting a run on the same eight cards
# ninety seconds apart, and on a block that collision costs both runs rather than one. So
# `run` refuses a node somebody else holds, and it is the same refusal whether the second
# person arrived through the workflow or through a shell.
#
# RELEASE DOES NOT KILL ANYTHING BY DEFAULT. Releasing a node whose container is still
# training is the one way to make the lock lie, so it refuses while a container is up and
# `--force` is the sentence somebody has to type when they mean it.
set -euo pipefail

. /etc/edullm-block.env

STATE="${EDULLM_BLOCK_STATE}"
CLAIM="${STATE}/claim.json"
SCRATCH="${EDULLM_BLOCK_SCRATCH}"

# THE CHARACTER SET IS A SAFETY CONTROL RATHER THAN TIDINESS. A run name becomes a directory
# under /scratch, a docker container name, and a segment of an S3 key, and it is written into
# a JSON document by printf rather than by a serializer. A name carrying a quote produces a
# claim file nothing can parse; one carrying a slash produces a container name docker
# refuses after the clone has already happened.
readonly SAFE_NAME='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
readonly SAFE_REF='^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'

die() {
  echo "edullm-node: $*" >&2
  exit 1
}

now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

claim_field() {
  # One field out of the claim, without a JSON parser, because jq is not on every image this
  # AMI family ships and a missing jq at 03:00 is a status command that reports nothing.
  # The writer below emits one flat object with no nesting and a validated character set, so
  # the grammar this has to read is one this expression covers completely.
  [ -f "${CLAIM}" ] || return 0
  sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" "${CLAIM}"
}

container_of() {
  docker ps --quiet --filter "name=^edullm-$1$" 2>/dev/null
}

busy_gpus() {
  nvidia-smi --query-compute-apps=gpu_uuid --format=csv,noheader 2>/dev/null |
    sort --unique | grep --count . || true
}

total_gpus() {
  nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | grep --count . || true
}

write_claim() {
  printf '{"run":"%s","who":"%s","repository":"%s","branch":"%s","commit":"%s","started_at":"%s"}\n' \
    "$1" "$2" "$3" "$4" "$5" "$(now)" > "${CLAIM}"
}

command_status() {
  case "${1:-}" in
    status | claim | release | run | logs) return 0 ;;
    *) return 1 ;;
  esac
}

do_status() {
  local run who started busy total container
  run="$(claim_field run)"
  who="$(claim_field who)"
  started="$(claim_field started_at)"
  busy="$(busy_gpus)"
  total="$(total_gpus)"
  container=""
  [ -n "${run}" ] && container="$(container_of "${run}")"

  if [ "${1:-}" = "--json" ]; then
    printf '{"node":%s,"gpus_busy":%s,"gpus_total":%s,"run":"%s","who":"%s","started_at":"%s","container":"%s"}\n' \
      "${EDULLM_BLOCK_NODE}" "${busy}" "${total}" "${run}" "${who}" "${started}" \
      "$([ -n "${container}" ] && echo running || echo none)"
    return 0
  fi

  echo "node       ${EDULLM_BLOCK_NODE}"
  echo "block      ${EDULLM_BLOCK_RESERVATION}"
  echo "gpus       ${busy}/${total} busy"
  if [ -z "${run}" ]; then
    echo "claim      none"
  else
    echo "claim      ${who} / ${run} since ${started}"
    echo "branch     $(claim_field repository) @ $(claim_field branch) ($(claim_field commit))"
    echo "container  $([ -n "${container}" ] && echo running || echo "not running")"
    echo "logs       ${SCRATCH}/${run}/log"
  fi
  df --human-readable --output=source,size,used,avail,target "${SCRATCH}" | tail -n 1
}

do_claim() {
  local name="${1:-}" who="${2:-unknown}"
  [[ "${name}" =~ ${SAFE_NAME} ]] || die "a run name must match ${SAFE_NAME}"
  local held
  held="$(claim_field run)"
  if [ -n "${held}" ] && [ "${held}" != "${name}" ]; then
    die "node ${EDULLM_BLOCK_NODE} is held by $(claim_field who) for ${held} since $(claim_field started_at)"
  fi
  write_claim "${name}" "${who}" "" "" ""
  echo "node ${EDULLM_BLOCK_NODE} claimed by ${who} for ${name}"
}

do_release() {
  local run force="${1:-}"
  run="$(claim_field run)"
  [ -n "${run}" ] || { echo "node ${EDULLM_BLOCK_NODE} was not claimed"; return 0; }
  if [ -n "$(container_of "${run}")" ] && [ "${force}" != "--force" ]; then
    die "${run} is still running here; stop it or pass --force if you mean to abandon the claim"
  fi
  rm -f "${CLAIM}"
  echo "node ${EDULLM_BLOCK_NODE} released"
}

do_run() {
  local name="" repository="edu-llm/OLMo-core" branch="" override="" who="unknown" force=""
  local project="capacity-block"
  while [ $# -gt 0 ]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --repository) repository="$2"; shift 2 ;;
      --branch) branch="$2"; shift 2 ;;
      --command) override="$2"; shift 2 ;;
      --who) who="$2"; shift 2 ;;
      --wandb-project) project="$2"; shift 2 ;;
      --force) force=--force; shift ;;
      *) die "unknown argument $1" ;;
    esac
  done
  [[ "${name}" =~ ${SAFE_NAME} ]] || die "--name must match ${SAFE_NAME}"
  [[ "${branch}" =~ ${SAFE_REF} ]] || die "--branch must match ${SAFE_REF}"
  [[ "${repository}" =~ ${SAFE_REF} ]] || die "--repository must match ${SAFE_REF}"

  local held
  held="$(claim_field run)"
  if [ -n "${held}" ] && [ "${held}" != "${name}" ] && [ "${force}" != "--force" ]; then
    die "node ${EDULLM_BLOCK_NODE} is held by $(claim_field who) for ${held} since $(claim_field started_at)"
  fi
  if [ -n "$(container_of "${name}")" ]; then
    die "${name} is already running on node ${EDULLM_BLOCK_NODE}"
  fi

  # THE CLAIM IS TAKEN BEFORE THE CLONE AND REWRITTEN AFTER IT. A clone of OLMo-core is tens
  # of seconds, and two people dispatching within that window both read an unheld node and
  # both proceed. Writing the claim first closes the window at the cost of a claim carrying
  # no commit for as long as the clone takes, which reads as what it is on `status`.
  write_claim "${name}" "${who}" "${repository}" "${branch}" ""

  # CLONED FRESH EVERY TIME RATHER THAN PULLED INTO WHAT IS THERE. A directory left by an
  # earlier run of the same name is a tree at an unknown commit with unknown local edits,
  # and the commit this records would then be a claim about the branch rather than about
  # what is going to run. Deleting it is cheap; being wrong about the commit is not.
  local tree="${SCRATCH}/${name}"
  rm -rf "${tree}"
  mkdir -p "${tree}/log"
  git clone --depth 1 --branch "${branch}" "https://github.com/${repository}.git" "${tree}/repo"
  local commit
  commit="$(git -C "${tree}/repo" rev-parse HEAD)"

  # THE COMMAND COMES OUT OF THE BRANCH UNLESS SOMEBODY OVERRODE IT, which is the same rule
  # the submission path follows: `.edullm/run.yaml` is a property of the code rather than of
  # the person submitting it. Read with PyYAML through the distribution python rather than
  # with a regex, because a folded scalar -- which is how every long training command in
  # this project is written -- is several lines in the file and one line in the value.
  local command="${override}"
  if [ -z "${command}" ]; then
    [ -f "${tree}/repo/.edullm/run.yaml" ] ||
      die "${repository}@${branch} carries no .edullm/run.yaml, so pass --command"
    command="$(python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))["command"])' \
      "${tree}/repo/.edullm/run.yaml")"
  fi
  [ -n "${command}" ] || die "the command resolved to nothing"

  local wandb_key
  wandb_key="$(aws secretsmanager get-secret-value \
    --region "${EDULLM_BLOCK_IMAGE_REGION}" \
    --secret-id "${EDULLM_BLOCK_WANDB_SECRET_ID}" \
    --query SecretString --output text 2>/dev/null || true)"

  local prefix="s3://${EDULLM_BLOCK_OUTPUTS_BUCKET}/${EDULLM_BLOCK_S3_PREFIX}/${name}"
  write_claim "${name}" "${who}" "${repository}" "${branch}" "${commit}"

  # `--ipc=host` and the two ulimits are the NCCL recipe AWS publishes for its own deep
  # learning containers. The default 64 MiB of /dev/shm is where a multi-GPU run dies
  # several minutes in with a message about a bootstrap timeout that names nothing.
  #
  # THE OUTPUT GOES THROUGH tee RATHER THAN A PLAIN REDIRECT so that both readers work:
  # `docker logs` for somebody sitting on the machine, and the file for the sync that
  # carries it to S3 for everybody who is not. `set -o pipefail` inside is what keeps the
  # exit status the trainer's rather than tee's.
  docker run --detach \
    --name "edullm-${name}" \
    --gpus all \
    --ipc=host \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    --volume "${tree}/repo:/work" \
    --volume "${tree}/log:/work/log" \
    --workdir /work \
    --env "EDULLM_RUN_ID=${name}" \
    --env "EDULLM_COMMIT_SHA=${commit}" \
    --env "EDULLM_OUTPUT_BUCKET=${EDULLM_BLOCK_OUTPUTS_BUCKET}" \
    --env "EDULLM_OUTPUT_PREFIX=${prefix}/" \
    --env "EDULLM_CHECKPOINT_DIR=${prefix}/checkpoints/" \
    --env "EDULLM_DATA_BUCKET=${EDULLM_BLOCK_DATA_BUCKET}" \
    --env "AWS_DEFAULT_REGION=${EDULLM_BLOCK_REGION}" \
    --env "AWS_REGION=${EDULLM_BLOCK_REGION}" \
    --env "WANDB_API_KEY=${wandb_key}" \
    --env "WANDB_ENTITY=eduLLM" \
    --env "WANDB_PROJECT=${project}" \
    --env "EDULLM_WANDB_PROJECT=${project}" \
    --env "WANDB_RUN_ID=${name}" \
    --env "WANDB_NAME=${name}" \
    "${EDULLM_BLOCK_IMAGE}" \
    bash -lc "set -o pipefail; ${command} 2>&1 | tee -a /work/log/train.log"

  echo "run        ${name}"
  echo "node       ${EDULLM_BLOCK_NODE}"
  echo "commit     ${commit}"
  echo "container  edullm-${name}"
  echo "logs       ${prefix}/log/train.log"
}

do_logs() {
  local run="${1:-$(claim_field run)}"
  [ -n "${run}" ] || die "nothing is claimed here and no run was named"
  tail --follow=name --lines=200 "${SCRATCH}/${run}/log/train.log"
}

command_status "${1:-}" || die "usage: edullm-node status|claim|release|run|logs"
verb="$1"
shift
case "${verb}" in
  status) do_status "$@" ;;
  claim) do_claim "$@" ;;
  release) do_release "$@" ;;
  run) do_run "$@" ;;
  logs) do_logs "$@" ;;
esac
HELPER
chmod 0755 /usr/local/bin/edullm-node

# ---------------------------------------------------------------------------------------
# THE LOG SYNC. A minute of lag against being able to read a run from a laptop that holds no
# AWS credential of its own.
# ---------------------------------------------------------------------------------------
#
# NOT `set -e`, DELIBERATELY, WHICH IS THE OPPOSITE OF EVERY OTHER SCRIPT HERE. One sync
# failing -- a file rotated out from under it, a throttle, a run directory deleted mid-copy
# -- must not stop the loop, because the loop stopping is silent and takes every other run
# on the node with it.
cat > /usr/local/bin/edullm-block-log-sync <<'SYNC'
#!/usr/bin/env bash
set -uo pipefail
. /etc/edullm-block.env
while true; do
  for directory in "${EDULLM_BLOCK_SCRATCH}"/*/log; do
    [ -d "${directory}" ] || continue
    run="$(basename "$(dirname "${directory}")")"
    aws s3 sync "${directory}" \
      "s3://${EDULLM_BLOCK_OUTPUTS_BUCKET}/${EDULLM_BLOCK_S3_PREFIX}/${run}/log/" \
      --only-show-errors || true
  done
  sleep "${EDULLM_BLOCK_LOG_SYNC_SECONDS}"
done
SYNC
chmod 0755 /usr/local/bin/edullm-block-log-sync

cat > /etc/systemd/system/edullm-block-log-sync.service <<'UNIT'
[Unit]
Description=Copy every run log on this capacity block node to S3
After=network-online.target

[Service]
ExecStart=/usr/local/bin/edullm-block-log-sync
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now edullm-block-log-sync.service

# ---------------------------------------------------------------------------------------
# THE SENTINEL. Written last and only on the path where everything above worked, which is
# what makes its presence mean something to the launch workflow.
# ---------------------------------------------------------------------------------------
printf '{"node":%s,"block":"%s","ready_at":"%s","driver":"%s","gpus":%s,"image":"%s","scratch_device":"%s"}\n' \
  "${NODE_NUMBER}" "${RESERVATION_ID}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "${driver_version}" "${observed_gpus}" "${image_digest}" "${scratch_device}" \
  > "${READY_FILE}"
chmod 0644 "${READY_FILE}"
echo "node ${NODE_NUMBER} is ready"
