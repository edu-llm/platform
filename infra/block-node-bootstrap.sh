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

# THE THREE THE DRAIN READS, AND THE FIRST OF THEM IS THE ONE NOBODY MAY GUESS. The launch
# workflow reads `EndDate` off the reservation itself and prepends it here, so a node knows
# when it is going to be taken away without holding a grant to ask, without a date written
# into a file, and without a second block's fleet inheriting the first one's deadline. The
# other two come from `edullm_platform.block_drain`, which is where the reasoning for each
# number is, and are prepended for the same reason: one place decides, everywhere else is told.
readonly ENDS_AT="${EDULLM_BLOCK_ENDS_AT:?the launch workflow must prepend EDULLM_BLOCK_ENDS_AT}"
readonly RECLAIM_MINUTES="${EDULLM_BLOCK_RECLAIM_MINUTES:?the launch workflow must prepend EDULLM_BLOCK_RECLAIM_MINUTES}"
readonly DRAIN_FROM_MINUTES="${EDULLM_BLOCK_DRAIN_FROM_MINUTES:?the launch workflow must prepend EDULLM_BLOCK_DRAIN_FROM_MINUTES}"

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
#
# EVERY FAILURE IN HERE FALLS BACK RATHER THAN ABORTING, WHICH IS WHY IT IS A FUNCTION AND
# NOT A RUN OF STATEMENTS. Called as an `if` condition, `set -e` is suspended inside it, so
# each step carries its own `|| return 1` and a failure lands in the fallback below instead of
# in the trap. `mdadm` in particular is not guaranteed to be on this image family, and losing
# a whole node out of eight because a package was not preinstalled is exactly the trade the
# header refuses to make.
SCRATCH_DEVICE=root-volume
prepare_scratch() {
  local devices=("$@")
  local target="${devices[0]}"
  if [ "${#devices[@]}" -gt 1 ]; then
    if command -v mdadm > /dev/null 2>&1; then
      mdadm --create --verbose /dev/md0 --level=0 \
        --raid-devices="${#devices[@]}" "${devices[@]}" || return 1
      target=/dev/md0
    else
      echo "mdadm is absent; using one of ${#devices[@]} local devices rather than all" >&2
    fi
  fi
  mkfs.ext4 -F -m 0 "${target}" || return 1
  # No fstab entry. The array is rebuilt from nothing on every boot and this fleet is never
  # rebooted, so a line there would only ever be read on the one boot where it names a device
  # that no longer exists, and hold the machine at an emergency prompt nobody can reach.
  mount -o discard,noatime "${target}" "${SCRATCH}" || return 1
  SCRATCH_DEVICE="${target}"
}

mkdir -p "${SCRATCH}"
mapfile -t instance_store < <(
  lsblk --nodeps --noheadings --output NAME,MODEL |
    awk '/Instance Storage/ {print "/dev/" $1}'
)

if [ "${#instance_store[@]}" -eq 0 ]; then
  echo "no instance store device was found; /scratch is on the root volume" >&2
elif ! prepare_scratch "${instance_store[@]}"; then
  echo "the local NVMe could not be prepared; /scratch is on the root volume" >&2
  SCRATCH_DEVICE=root-volume
fi
scratch_device="${SCRATCH_DEVICE}"
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
EDULLM_BLOCK_ENDS_AT=${ENDS_AT}
EDULLM_BLOCK_RECLAIM_MINUTES=${RECLAIM_MINUTES}
EDULLM_BLOCK_DRAIN_FROM_MINUTES=${DRAIN_FROM_MINUTES}
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

# THE CLAIM `run` TOOK, GIVEN BACK WHEN NOTHING ENDS UP RUNNING. THIS IS THE OTHER HALF OF
# TAKING IT BEFORE THE CLONE.
#
# Writing the claim first is what closes the window two people dispatching seconds apart would
# otherwise both walk through, and everything after that point can still fail: a branch deleted
# since the workflow resolved it, a repository this node cannot reach, a tree carrying no
# `.edullm/run.yaml` -- which is every registered repository except OLMo-core, so it is the
# first thing a new one meets rather than an unlucky one. Under `set -e` each of those left the
# claim behind, and a node claimed for a run that does not exist reads as busy to the whole
# fleet and to `block-run.yml` until somebody who has never heard of `release` finds it.
#
# ONLY A CLAIM NAMING THIS RUN, AND ONLY WHILE NO CONTAINER IS UP, which is the same rule
# `tools/block_run_distributed.py` rolls back by. A container that started owns the machine
# whatever this shell went on to exit, and a node somebody else claimed in the seconds since is
# theirs. What this deliberately does not do is put back a claim `--force` overwrote: forcing
# already means taking a machine off somebody, and the node then reads as carrying unclaimed
# work, which `block-run.yml` refuses on by name.
UNSTARTED=""
give_the_claim_back() {
  local status=$?
  if [ "${status}" -ne 0 ] && [ -n "${UNSTARTED}" ] &&
    [ -z "$(container_of "${UNSTARTED}")" ] && [ "$(claim_field run)" = "${UNSTARTED}" ]; then
    rm -f "${CLAIM}"
    echo "edullm-node: nothing started, so node ${EDULLM_BLOCK_NODE} is not held" >&2
  fi
}

known_verb() {
  case "${1:-}" in
    status | claim | release | run | logs | drain) return 0 ;;
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
  # Tolerated rather than checked. This is a diagnostic command and the disk line is the
  # least important thing it prints, so a `df` that answers differently must not be the
  # reason `edullm-node status` exits non-zero on somebody trying to find out what is wrong.
  df -h "${SCRATCH}" | tail -n 1 || true
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
  UNSTARTED="${name}"
  trap give_the_claim_back EXIT

  # CLONED FRESH EVERY TIME RATHER THAN PULLED INTO WHAT IS THERE. A directory left by an
  # earlier run of the same name is a tree at an unknown commit with unknown local edits,
  # and the commit this records would then be a claim about the branch rather than about
  # what is going to run. Deleting it is cheap; being wrong about the commit is not.
  local tree="${SCRATCH}/${name}"
  rm -rf "${tree}"
  mkdir -p "${tree}/log"
  # GIT IS TOLD NOT TO ASK FOR A PASSWORD, AND THE REFUSAL SAYS WHY THERE IS NOWHERE TO TYPE
  # ONE. This node holds no GitHub credential, which works because the repositories this lane
  # runs are public and stops dead the moment one is not -- seconds after the claim was taken,
  # inside a Systems Manager invocation that has no terminal. Git's own message is about
  # failing to read a username, which names neither the design nor the way out of it.
  if ! GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "${branch}" \
    "https://github.com/${repository}.git" "${tree}/repo"; then
    echo "edullm-node: this node holds no GitHub credential, so a private repository cannot" >&2
    echo "be cloned here at all. On a public one the branch is gone or GitHub refused." >&2
    die "could not clone ${repository}@${branch}"
  fi
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

# ---------------------------------------------------------------------------------------
# THE DRAIN. Everything on this machine that is not already in S3, put in S3, and then
# counted rather than assumed.
# ---------------------------------------------------------------------------------------
#
# WHAT IS ACTUALLY AT RISK, BECAUSE IT IS NOT THE CHECKPOINTS. An OLMo-core run here writes
# its checkpoints to an s3:// save folder directly and the log sync carries train.log up
# every minute, so those two are the parts of a run that are already safe. What is on
# /scratch and nowhere else is the tree that was cloned, the resolved .edullm/run.yaml, the
# commit that produced it, and whatever a researcher wrote beside their code -- plus the
# entirety of any run whose command did not follow the convention. /scratch is a RAID0 over
# local NVMe. It does not survive a stop and it certainly does not survive AWS reclaiming
# the instance, so at 11:00 UTC on the Tuesday all of it is simply gone.
#
# THE COUNT IS TAKEN BEFORE THE SYNC AND THE LISTING AFTER IT, AND THAT ORDER IS LOAD
# BEARING. A training run writes files while this is copying. Counting the disk afterwards
# would count files created during the sync, report them as missing from S3, and hand
# somebody a shortfall on every healthy node with a live run on it -- which is the reading
# that teaches people to ignore the report. Counting first means anything created mid-sync
# lands on the remote side of the comparison, where it is harmless.
#
# WHY A COUNT AT ALL RATHER THAN THE EXIT STATUS OF `aws s3 sync`. The sync reports success
# for a partial copy in the cases that matter here: a file rotated out from under it, a
# permissions refusal on one path, a throttle it retried past its own limit. Those all leave
# a zero exit and a prefix that is missing objects, and the whole reason this runs is that
# nobody gets a second chance to find out.
#
# .git IS THE ONE THING DELIBERATELY LEFT BEHIND. A depth-one clone's object store is the
# largest thing in a run directory and is the only thing in it that is reconstructible: the
# repository, the branch and the commit are recorded beside the files, and the files
# themselves are copied. History is not worth the minutes at the deadline.
#
# THE PATTERN IS `*/.git/*` AND NOT `repo/.git/*` BECAUSE THE TWO SIDES MATCH AGAINST
# DIFFERENT STRINGS. `aws s3 sync` evaluates a filter against the source path it walked,
# which for a local directory is the absolute one, and `find -path` does the same -- so a
# pattern anchored at the run directory matches on neither, silently, and copies the object
# store it was written to skip. A leading `*` costs nothing here and catches a `.git` a
# researcher cloned somewhere else under their own tree as well.
flush_run() {
  local run="$1"
  local source="${SCRATCH}/${run}"
  local destination="s3://${EDULLM_BLOCK_OUTPUTS_BUCKET}/${EDULLM_BLOCK_S3_PREFIX}/${run}/scratch"
  local held status=ok expected observed
  held="$(claim_field run)"

  # Written into the run directory so that it is synced with everything else, which makes the
  # S3 prefix self-describing to somebody reading it a week later with no access to this
  # machine and no memory of who was on which node.
  if [ "${held}" = "${run}" ]; then
    printf '{"run":"%s","node":%s,"block":"%s","drained_at":"%s","who":"%s","repository":"%s","branch":"%s","commit":"%s"}\n' \
      "${run}" "${EDULLM_BLOCK_NODE}" "${EDULLM_BLOCK_RESERVATION}" "$(now)" \
      "$(claim_field who)" "$(claim_field repository)" "$(claim_field branch)" \
      "$(claim_field commit)" > "${source}/edullm-drain.json" || true
  else
    printf '{"run":"%s","node":%s,"block":"%s","drained_at":"%s","who":""}\n' \
      "${run}" "${EDULLM_BLOCK_NODE}" "${EDULLM_BLOCK_RESERVATION}" "$(now)" \
      > "${source}/edullm-drain.json" || true
  fi

  expected="$(find "${source}" -type f -not -path '*/.git/*' 2> /dev/null |
    grep --count . || true)"
  aws s3 sync "${source}" "${destination}/" \
    --region "${EDULLM_BLOCK_REGION}" \
    --exclude '*/.git/*' \
    --only-show-errors || status=failed
  observed="$(aws s3 ls "${destination}/" --recursive --region "${EDULLM_BLOCK_REGION}" \
    2> /dev/null | grep --count . || true)"
  if [ "${status}" = ok ] && [ "${observed:-0}" -lt "${expected:-0}" ]; then
    status=short
  fi
  printf 'run\t%s\t%s\t%s\t%s\n' "${run}" "${expected:-0}" "${observed:-0}" "${status}"
}

# ASK THE TRAINER TO STOP, WHICH IS NOT THE SAME AS STOPPING THE CONTAINER.
#
# `docker stop` sends SIGTERM to PID 1, and PID 1 in this container is the bash that owns the
# `cmd | tee` pipeline rather than the trainer. Non-interactive bash takes the default action
# on SIGTERM and dies -- whereupon the kernel SIGKILLs everything else in the namespace,
# which is the trainer, mid-write, with no chance to finish a checkpoint. That is the exact
# outcome this is trying to avoid, produced by the command that looks like it avoids it.
#
# So the signal is delivered inside the container, to every process except PID 1 and except
# the shells and the `tee` carrying the log. OLMo-core's Trainer installs a SIGTERM handler
# that cancels the run, and a cancelled run still runs `post_train`, where CheckpointerCallback
# saves synchronously if the current step is past the last saved one. That is the final
# checkpoint, and it is a whole one rather than a torn one.
#
# NOTHING CALLS THIS UNLESS SOMEBODY ASKS FOR IT. `release` refuses to abandon a claim while a
# container is up and makes `--force` the sentence you have to type; ending somebody's training
# run early is a larger act than that and gets the same treatment. The scheduled drain never
# passes --stop-runs.
stop_training() {
  docker exec "edullm-$1" bash -c '
    for entry in /proc/[0-9]*; do
      pid="${entry##*/}"
      if [ "${pid}" = 1 ]; then continue; fi
      read -r comm < "${entry}/comm" 2>/dev/null || continue
      case "${comm}" in tee|bash|sh|dash) continue ;; esac
      kill -TERM "${pid}" 2>/dev/null || true
    done' > /dev/null 2>&1 || return 1
}

do_drain() {
  local stop_runs=no directory run held usable ends
  while [ $# -gt 0 ]; do
    case "$1" in
      --stop-runs) stop_runs=yes; shift ;;
      *) die "unknown argument $1" ;;
    esac
  done

  ends="$(date -u -d "${EDULLM_BLOCK_ENDS_AT}" +%s)" ||
    die "EDULLM_BLOCK_ENDS_AT is ${EDULLM_BLOCK_ENDS_AT}, which date(1) will not read"
  usable=$((ends - EDULLM_BLOCK_RECLAIM_MINUTES * 60 - $(date -u +%s)))
  held="$(claim_field run)"

  printf 'node\t%s\n' "${EDULLM_BLOCK_NODE}"
  printf 'usable_seconds\t%s\n' "${usable}"
  printf 'claim\t%s\t%s\n' "$(claim_field who)" "${held}"
  if [ -n "${held}" ] && [ -n "$(container_of "${held}")" ]; then
    printf 'container\trunning\n'
  else
    printf 'container\tnone\n'
  fi

  if [ "${stop_runs}" = yes ] && [ -n "${held}" ] && [ -n "$(container_of "${held}")" ]; then
    if stop_training "${held}"; then
      # Bounded, because the flush behind it is the thing with the deadline. A model whose
      # final save takes longer than this is one whose checkpoint was never going to land in
      # the time left, and holding the drain open for it costs the files that would have.
      for _attempt in $(seq 1 30); do
        [ -n "$(container_of "${held}")" ] || break
        sleep 10
      done
      printf 'stopped\t%s\n' "${held}"
    fi
  fi

  for directory in "${SCRATCH}"/*/; do
    [ -d "${directory}" ] || continue
    run="$(basename "${directory}")"
    flush_run "${run}"
  done
  printf 'drained_at\t%s\n' "$(now)"
}

known_verb "${1:-}" || die "usage: edullm-node status|claim|release|run|logs|drain"
verb="$1"
shift
case "${verb}" in
  status) do_status "$@" ;;
  claim) do_claim "$@" ;;
  release) do_release "$@" ;;
  run) do_run "$@" ;;
  logs) do_logs "$@" ;;
  drain) do_drain "$@" ;;
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
      --region "${EDULLM_BLOCK_REGION}" \
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
# ---------------------------------------------------------------------------------------
# THE DRAIN TIMER, WHICH IS ON THE NODE AND NOT IN GITHUB ACTIONS. THIS IS THE DECISION.
# ---------------------------------------------------------------------------------------
#
# A scheduled GitHub Actions workflow is delivered late. Not occasionally and not by seconds:
# under load the queue for scheduled runs is minutes deep and GitHub commits to no bound at
# all, so "runs at 10:50" means "runs at some point after 10:50". Everywhere else on this
# platform that is fine, because the deadline on the other side is a person. Here the deadline
# is AWS terminating eight machines against a wall clock, the data that has not reached S3 by
# then does not exist afterwards, and the window is not refundable and does not repeat.
#
# So the flush runs here: a systemd timer on the machine holding the data, needing nothing
# from GitHub, nothing from Systems Manager, nothing from a credential vending path and
# nobody being awake. It keeps its schedule while the repository is rate-limited, while the
# Actions service is degraded, and at four in the morning on a Sunday.
#
# WHAT GITHUB IS FOR IS THE HALF A NODE CANNOT DO. `.github/workflows/block-drain.yml` reads
# every node at once and prints who is holding what and what is still outstanding into a job
# summary, because roughly fifteen of the thirty-five people here hold no AWS role and a
# warning that only exists on a maintainer's terminal is addressed to the half of the team
# that was already fine. That report tolerates being ten minutes late. The flush does not, and
# the two are separate for exactly that reason. `tools/block_drain.py` is the same report from
# a laptop for the case where GitHub is the thing that is broken.
#
# EVERY TICK FLUSHES ONCE THE WINDOW IS CLOSING, RATHER THAN FIRING AT NAMED HORIZONS. A
# horizon table needs state to stay idempotent, and state is what goes wrong on the one run
# nobody watches. `aws s3 sync` is already incremental, so repeating it costs a listing over a
# prefix that has not changed and buys the property that matters: by the last tick almost
# everything is already up, and the copy that has to finish in the final minutes is small.
# Outside that window it still flushes hourly, because a node lost to a hardware fault on the
# Sunday loses the same disk for the same reason.
cat > /usr/local/bin/edullm-block-drain-tick <<'TICK'
#!/usr/bin/env bash
# Not `set -e`, the same as the log sync and for the same reason: one failed tick must not be
# able to stop the timer, because a stopped timer is silent and the next thing that reads this
# node is AWS terminating it.
set -uo pipefail
. /etc/edullm-block.env

marker=/var/lib/edullm/last-flush
usable=$(($(date -u -d "${EDULLM_BLOCK_ENDS_AT}" +%s) \
  - EDULLM_BLOCK_RECLAIM_MINUTES * 60 - $(date -u +%s)))

due=no
if [ "${usable}" -le $((EDULLM_BLOCK_DRAIN_FROM_MINUTES * 60)) ]; then
  due=yes
elif [ ! -f "${marker}" ] || [ -z "$(find "${marker}" -newermt '-60 minutes' 2> /dev/null)" ]; then
  due=yes
fi
[ "${due}" = yes ] || exit 0

{
  printf -- '--- %s usable_seconds=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${usable}"
  edullm-node drain
} >> /var/lib/edullm/drain.log 2>&1
touch "${marker}"
TICK
chmod 0755 /usr/local/bin/edullm-block-drain-tick

cat > /etc/systemd/system/edullm-block-drain.service <<'UNIT'
[Unit]
Description=Copy everything on this capacity block node that is not already in S3
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/edullm-block-drain-tick
UNIT

cat > /etc/systemd/system/edullm-block-drain.timer <<'UNIT'
[Unit]
Description=Run the capacity block drain often enough that the last one has little left to do

[Timer]
OnBootSec=10min
OnUnitActiveSec=5min
# systemd batches timers within their accuracy window to let a machine sleep, which is the
# wrong trade on a node that is about to be taken away at a fixed minute.
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now edullm-block-log-sync.service
systemctl enable --now edullm-block-drain.timer

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
