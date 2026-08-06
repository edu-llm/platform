#!/usr/bin/env bash
#
# Set one macOS or Linux laptop up for the eduLLM platform, checking rather than assuming
# at every step and printing what it found.
#
# WHY THIS IS A SCRIPT AND NOT A PAGE IN guides/. A page is read by somebody sitting alone
# who then reports that they followed it. This is run beside them, it prints what it found
# at each step, and the transcript is the evidence. `guides/day-one.md` is still the thing
# to read; this is the thing to run.
#
#   bash tools/set-up-a-laptop.sh              the submission track, which is everybody
#   bash tools/set-up-a-laptop.sh --lane       that, and then the lane track
#   bash tools/set-up-a-laptop.sh --check      look at everything and change nothing
#
# Off a laptop with no platform checkout, which is every researcher's:
#
#   curl -fsSLO https://raw.githubusercontent.com/edu-llm/platform/main/tools/set-up-a-laptop.sh
#   bash set-up-a-laptop.sh
#
# TWO SCRIPTS AND NOT ONE WITH BRANCHES, AND tools/set-up-a-laptop.ps1 IS THE OTHER HALF.
# Aryan is on native Windows in PowerShell, where there is no bash to branch inside of, and
# a laptop that has never been set up is exactly the laptop with no Git Bash and no WSL on
# it. The two other single-file spellings were considered and are worse. A PowerShell script
# for both needs `pwsh` on macOS, which is an install to do before the install script runs.
# A Python script for both needs an interpreter, and the interpreter is the thing step `uv`
# exists to obtain -- on Windows a bare `python` opens the Microsoft Store rather than
# running anything, which is the failure this whole file is written to avoid.
#
# What keeps the two from drifting is `tests/test_setup_script.py`, which reads the ordered
# step list out of both files and fails when they stop naming the same steps in the same
# order. The order is the part that was wrong until this morning, so the order is the part
# a test holds.
#
# BASH 3.2, WHICH IS WHAT macOS SHIPS. /bin/bash is 3.2.57 on a current macOS and that is
# the bash `#!/usr/bin/env bash` finds on a laptop nobody has customised. So there are no
# associative arrays here, no `mapfile`, no `${var,,}` and no namerefs, all of which are
# bash 4 and all of which would fail on the machines this is for rather than on the machine
# it was written on.
#
# WHAT THIS DOES NOT DO. It installs nothing that cannot be installed. `sb-aws-creds` is
# private, is on no registry, and is built out of a repository this roster cannot read, so
# the lane track checks for it and stops. Frank hands the tarball over in person with a
# checksum; there is no line to add here that would work.

set -uo pipefail

# THE ORDER, WHICH IS THE WHOLE POINT OF THE FILE, AND IT DRIVES EXECUTION RATHER THAN
# DESCRIBING IT. Each id below names a `step_<id>` function with the hyphens turned into
# underscores, and the loop at the bottom walks these arrays. A step added to a function and
# not to an array does not run; a step in an array with no function is a hard error.

# THE SUBMISSION TRACK, AND IT IS ALL MOST PEOPLE NEED. `check`, `submit`, `status`, `logs`
# and `cancel` drive git and gh and hold no cloud credential: the AWS credential lives
# inside a workflow whose trust policy pins it to one file on main, so a laptop cannot get
# one and does not need one. Somebody with no AWS access at all runs every verb in this
# track and produces runs anybody can cite.
SUBMISSION_STEPS=(
  uv
  git
  gh
  gh-login
  clear-the-former-name
  install
  on-the-path
  version
  reaches-github
)

# THE LANE TRACK, WHICH IS ONLY FOR `run`, `shell` AND `stop`. Those three start a machine
# of your own and are the exploration route rather than the submission path: nothing they do
# is checked, priced, approved or written to a lineage record, so what comes off them is a
# thing you saw rather than a result anybody can cite.
#
# THE BROKER IS FIRST AND THE PLUGIN IS LAST, AND THAT ORDER IS THE CORRECTION. Told about
# the plugin first, a newcomer does a real download and a sudo, clears that wall, and then
# meets the broker, which they cannot install at all. Every minute of the plugin install was
# spent before learning it was unnecessary. The identity check sits between them because it
# is the proof that the four steps above it worked, and there is no reason to install
# anything after learning they did not.
LANE_STEPS=(
  broker
  broker-login
  broker-profiles
  aws-profile
  identity
  session-plugin
)

INSTALL_LINE="uv tool install --force git+https://github.com/edu-llm/platform"
FORMER_NAME_REMOVAL="uv tool uninstall edullm-platform"
PLATFORM_REPOSITORY="edu-llm/platform"
BROKER="sb-aws-creds"
SESSION_PLUGIN="session-manager-plugin"

CHECK_ONLY=0
WANT_LANE=0
BLOCKED=0
WARNED=0

EXIT_READY=0
EXIT_BLOCKED=1
EXIT_UNUSABLE=2

usage() {
  cat <<'USAGE'
Set this laptop up for the eduLLM platform.

  --lane     also set up `edullm run`, `edullm shell` and `edullm stop`, which need an
             AWS session. Most people never need this.
  --check    report on every step and change nothing. Nothing is installed, nothing is
             uninstalled, and no browser is opened.
  --help     this.

Exit codes: 0 ready, 1 something needs a person, 2 this script could not be driven.
USAGE
}

say() { printf '%s\n' "$*"; }
ok() { printf '  ok        %s\n' "$*"; }
did() { printf '  did       %s\n' "$*"; }
note() { printf '  note      %s\n' "$*"; }
warn() {
  printf '  warning   %s\n' "$*"
  WARNED=1
}
blocked() {
  printf '  blocked   %s\n' "$*"
  BLOCKED=1
}
would() { printf '  would     %s\n' "$*"; }

# The first line of something a tool printed, trimmed, for putting on a report line. Tools
# answer `--version` with one line and with five depending on the tool, and a report that
# pastes five lines into the middle of a checklist is a report nobody reads.
first_line() {
  printf '%s' "$1" | sed -e 's/[[:space:]]*$//' | head -n 1
}

# WHY THIS EXISTS RATHER THAN `command -v`. `command -v` answers about the first match and
# says nothing about the rest, and one of the failures this script is written for is a
# second thing called `edullm` earlier on PATH than the one uv just installed. `type -a`
# names all of them in the order the shell would pick them, which is the only way to see it.
#
# Only lines resolving to an absolute path are kept, so a shell function, an alias or a
# builtin of the same name is not mistaken for an executable.
#
# `hash -r` FIRST, AND DEDUPLICATED AFTER, BOTH BECAUSE THE FIRST DRAFT GOT THIS WRONG. This
# shell has already run the command it is now asking about, so bash has a remembered location
# for it and `type -a` prints that remembered entry *and* the PATH lookup -- the same file,
# twice, which read as two installs shadowing each other and reported a healthy laptop as
# broken. Clearing the table also matters for a different reason: the install step ran in
# this same shell, so a remembered location from before it would be the stale one.
all_on_path() {
  hash -r 2>/dev/null || true
  type -a -- "$1" 2>/dev/null | sed -n 's|^[^ ]* is \(/.*\)$|\1|p' | awk '!seen[$0]++'
}

# -1, 0 or 1 for two dotted numeric versions, the way `sort -V` would order them. Written out
# rather than shelled out to, because `sort -V` is GNU and this runs on macOS.
version_compare() {
  awk -v left="$1" -v right="$2" '
    BEGIN {
      parts = split(left, a, ".")
      others = split(right, b, ".")
      longest = (parts > others) ? parts : others
      for (i = 1; i <= longest; i++) {
        one = (i <= parts) ? a[i] + 0 : 0
        two = (i <= others) ? b[i] + 0 : 0
        if (one < two) { print -1; exit }
        if (one > two) { print 1; exit }
      }
      print 0
    }'
}

on_path() { command -v -- "$1" 2>/dev/null || true; }

# ---------------------------------------------------------------------------------------
# the submission track
# ---------------------------------------------------------------------------------------

step_uv() {
  local where
  where="$(on_path uv)"
  if [ -z "${where}" ]; then
    blocked "uv is not on PATH, and it is what installs the tool and the Python under it."
    note "Install it, then open a new terminal and run this again:"
    note "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 1
  fi
  ok "uv at ${where}, $(first_line "$(uv --version 2>&1)")"
  return 0
}

step_git() {
  local where
  where="$(on_path git)"
  if [ -z "${where}" ]; then
    blocked "git is not on PATH. On macOS, \`xcode-select --install\` is the whole of it."
    return 1
  fi
  ok "git at ${where}, $(first_line "$(git --version 2>&1)")"
  return 0
}

step_gh() {
  local where
  where="$(on_path gh)"
  if [ -z "${where}" ]; then
    blocked "gh is not on PATH. Every verb in the submission track drives it."
    note "Install it from https://cli.github.com, then run this again."
    return 1
  fi
  ok "gh at ${where}, $(first_line "$(gh --version 2>&1)")"
  return 0
}

step_gh_login() {
  local answer status
  # Both streams. gh has written this to stderr and to stdout in different versions, and a
  # capture of one of them reports an authenticated laptop as having said nothing.
  answer="$(gh auth status 2>&1)"
  status=$?
  if [ ${status} -ne 0 ]; then
    blocked "gh is installed and not logged in, so nothing in the submission track can ask"
    blocked "GitHub anything. Run \`gh auth login\`, then run this again."
    note "gh said: $(first_line "${answer}")"
    return 1
  fi
  ok "gh is logged in as $(printf '%s' "${answer}" | sed -n 's/.*account \([^ ]*\).*/\1/p' | head -n 1)"
  return 0
}

step_clear_the_former_name() {
  # THE ORDER MATTERS AND THIS IS THE HALF THAT HAS TO GO FIRST. The distribution was called
  # `edullm-platform` until 4.2.2 this morning and the console script has always been
  # `edullm`, so anybody who installed before then is filed under a name they have never
  # typed. Installing the new name does not replace that entry: uv keeps both, and the two
  # own the same `edullm` executable. Clearing the old entry afterwards deletes that file
  # without noticing the new install still points at it, which leaves `uv tool list`
  # reporting a healthy `edullm` and `command not found` in the shell.
  #
  # So: uninstall, then install. Never the other way round.
  local answer status
  if [ "${CHECK_ONLY}" -eq 1 ]; then
    if uv tool list 2>&1 | grep -q '^edullm-platform '; then
      would "run \`${FORMER_NAME_REMOVAL}\`, which this laptop needs: uv has an install"
      would "filed under the former name."
    else
      ok "no install under the former name, so there is nothing to clear."
    fi
    return 0
  fi
  answer="$(uv tool uninstall edullm-platform 2>&1)"
  status=$?
  if [ ${status} -eq 0 ]; then
    did "\`${FORMER_NAME_REMOVAL}\` removed an install made before the rename."
    return 0
  fi
  # A laptop that never carried the old name answers ``error: `edullm-platform` is not
  # installed`` and exits 2. That is the expected answer on most of these machines and
  # there is nothing to act on, so it is reported and not treated as a failure.
  if printf '%s' "${answer}" | grep -q 'not installed'; then
    ok "nothing was filed under the former name, which is uv answering \"not installed\"."
    return 0
  fi
  warn "\`${FORMER_NAME_REMOVAL}\` exited ${status}: $(first_line "${answer}")"
  return 0
}

step_install() {
  if [ "${CHECK_ONLY}" -eq 1 ]; then
    would "run \`${INSTALL_LINE}\`."
  else
    say "  running   ${INSTALL_LINE}"
    if ! uv tool install --force "git+https://github.com/${PLATFORM_REPOSITORY}"; then
      blocked "the install failed. If it stopped on a path being too long, point UV_CACHE_DIR"
      blocked "at something short, such as C:\\uv or /tmp/uv, and try again."
      return 1
    fi
    did "installed."
  fi
  note "Re-running that same line later is the upgrade and the repair, because --force makes"
  note "it idempotent. \`uv tool upgrade\` is not the same thing: for an install pinned at a"
  note "release tag, which is the line every release note hands out, it answers \"Nothing to"
  note "upgrade\" and exits 0 however far behind that install has fallen."
  return 0
}

step_on_the_path() {
  # THE STEP THAT CATCHES A HEALTHY `uv tool list` WITH NOTHING ON PATH. Two different
  # faults present identically to somebody typing `edullm` and being told there is no such
  # command, and they have different remedies: the executable is missing, which the install
  # line repairs, or something else called `edullm` is earlier on PATH, which it does not.
  local bin_dir matches first
  bin_dir="$(uv tool dir --bin 2>/dev/null)"
  matches="$(all_on_path edullm)"
  if [ -z "${matches}" ]; then
    blocked "\`edullm\` is not on PATH."
    if [ -n "${bin_dir}" ] && [ -x "${bin_dir}/edullm" ]; then
      blocked "It is at ${bin_dir}/edullm, so the file exists and your shell cannot see it."
      blocked "Add that directory to PATH -- \`uv tool update-shell\` writes the line -- then"
      blocked "open a new terminal."
    else
      blocked "uv reports its binaries live in ${bin_dir:-a directory it would not name}, and"
      blocked "there is no edullm in it. Re-run \`${INSTALL_LINE}\`."
    fi
    return 1
  fi
  first="$(printf '%s\n' "${matches}" | head -n 1)"
  if [ -n "${bin_dir}" ] && [ "$(dirname "${first}")" != "${bin_dir}" ]; then
    blocked "the first \`edullm\` on PATH is ${first}, and uv installed one at"
    blocked "${bin_dir}/edullm. Your shell will run the first. Fix PATH or remove the other."
    return 1
  fi
  ok "edullm at ${first}"
  if [ "$(printf '%s\n' "${matches}" | grep -c .)" -gt 1 ]; then
    warn "there is more than one edullm on PATH. The first one wins, and it is the right"
    warn "one, but the others are worth removing: $(printf '%s' "${matches}" | tr '\n' ' ')"
  fi
  return 0
}

step_version() {
  # Staleness warns and never refuses, which is what the tool itself does at submit time
  # and for the reason recorded there: a release is cut most days, so being behind is the
  # normal state of every install rather than an exceptional one, and a gate on the normal
  # state is a gate everybody learns to skip.
  local printed installed latest status verdict
  printed="$(edullm --version 2>&1)"
  status=$?
  if [ ${status} -ne 0 ]; then
    blocked "\`edullm --version\` exited ${status}: $(first_line "${printed}")"
    return 1
  fi
  ok "${printed}"
  installed="$(printf '%s' "${printed}" | sed -n 's/^edullm \([0-9][0-9.]*\).*/\1/p')"
  latest="$(gh api "repos/${PLATFORM_REPOSITORY}/releases/latest" --jq .tag_name 2>/dev/null)"
  if [ -z "${latest}" ] || [ -z "${installed}" ]; then
    note "could not compare that against the current release, which is not a problem: the"
    note "probe is allowed to fail and this step never refuses on it."
    return 0
  fi
  verdict="$(version_compare "${installed}" "${latest#v}")"
  if [ "${verdict}" = "0" ]; then
    ok "that is ${latest}, the current release."
    return 0
  fi
  # AHEAD IS THE ORDINARY STATE OF A FRESH INSTALL AND MUST NOT READ AS BEHIND. The install
  # line above names the bare URL, so it resolves the default branch; main carries its
  # version bump from the moment the pull request earning it merges until release-tag.yml
  # cuts the tag. A newly installed laptop is therefore routinely a patch ahead of
  # releases/latest, and telling that person to upgrade sends them round a loop that cannot
  # end. Measured here on 2026-08-06: a fresh install answered 4.2.3 against a latest of
  # v4.2.2, and the first draft of this step called it stale.
  if [ "${verdict}" = "1" ]; then
    ok "that is ahead of ${latest}, the latest release, which is what a fresh install off"
    ok "the default branch looks like between a merge and its tag. Nothing to do."
    return 0
  fi
  warn "${latest} is the current release and this is ${installed}, so this install is behind."
  warn "It still submits and nothing here refuses on it. The reviewed configuration travels"
  warn "inside the install, so a behind one prices and refuses against older rules."
  warn "Re-run the install line above to move it."
  return 0
}

step_reaches_github() {
  # THE ONE END-TO-END PROOF OF THE WHOLE TRACK, AND IT COSTS NOTHING. `status` with no run
  # id asks gh who you are and asks GitHub what you have submitted. It dispatches no
  # workflow, so it burns no runner minute and can be run in a loop. If this answers, the
  # tool runs, gh holds a credential, GitHub accepts it, and the person can submit.
  local answer status
  answer="$(edullm status --json 2>&1)"
  status=$?
  if [ ${status} -ne 0 ]; then
    blocked "\`edullm status --json\` exited ${status}, so the chain from this laptop to"
    blocked "GitHub is not closed: $(first_line "${answer}")"
    return 1
  fi
  if ! printf '%s' "${answer}" | grep -q '"format_version"'; then
    blocked "\`edullm status --json\` exited 0 and printed something that is not the"
    blocked "document it promises: $(first_line "${answer}")"
    return 1
  fi
  ok "\`edullm status --json\` answered from GitHub. This laptop can submit."
  return 0
}

# ---------------------------------------------------------------------------------------
# the lane track
# ---------------------------------------------------------------------------------------

step_broker() {
  # THE FIRST WALL, AND THE ONLY ONE WITH NO INSTALL LINE UNDER IT. Every human AWS
  # credential in this organization comes from this binary: the sandbox issues no long-lived
  # keys and refuses the calls that would create one. It is marked private in its own
  # package.json so it has never been publishable and `npm view sb-aws-creds` answers 404,
  # and it is built out of a private repository this roster is not a member of, so a clone
  # answers 404 too. Every working copy in the organization was passed hand to hand.
  #
  # Printing an install command here would be worse than printing nothing: it would send
  # people to a 404 and cost each of them the afternoon this check exists to save.
  local where
  where="$(on_path "${BROKER}")"
  if [ -z "${where}" ]; then
    blocked "${BROKER} is not on PATH, and it is the first of the things a lane session"
    blocked "needs. Nothing below this point can work without it and nothing is billing."
    blocked "It cannot be installed from here: the package is private, it is on no registry,"
    blocked "and it is built out of a repository this roster cannot read. Ask Frank for the"
    blocked "tarball -- he has it and will check the checksum with you -- or run"
    blocked "\`edullm ask --kind access-request\` and say you need the AWS credential broker."
    return 1
  fi
  ok "${BROKER} at ${where}"
  return 0
}

step_broker_login() {
  # This also mints the person's Intern-* role, through the broker's self-provision route,
  # so it is the step that turns somebody with no AWS identity into somebody with one. It
  # opens a browser and waits for an approval, which is why --check does not run it.
  if [ "${CHECK_ONLY}" -eq 1 ]; then
    would "run \`${BROKER} login\`, which opens a browser."
    return 0
  fi
  say "  running   ${BROKER} login"
  if ! "${BROKER}" login; then
    blocked "\`${BROKER} login\` did not complete. Nothing was started and nothing is billing."
    return 1
  fi
  did "logged in. That also mints your Intern-* role if you did not have one."
  return 0
}

step_broker_profiles() {
  # THE SECOND OF THE BROKER'S TWO STEPS, AND THE GAP BETWEEN THEM IS A REAL PLACE PEOPLE
  # STOP. `login` puts a refresh token in the keychain and writes nothing to ~/.aws/config.
  # This is what writes the profile, and somebody who ran the first and not the second has
  # a working credential that no AWS client on the machine can find.
  if [ "${CHECK_ONLY}" -eq 1 ]; then
    would "run \`${BROKER} install-profiles\`."
    return 0
  fi
  say "  running   ${BROKER} install-profiles"
  if ! "${BROKER}" install-profiles; then
    blocked "\`${BROKER} install-profiles\` failed, so ~/.aws/config has no profile and no"
    blocked "AWS client on this laptop can find the credential login just obtained."
    return 1
  fi
  did "wrote the managed profile block into ${AWS_CONFIG_FILE:-${HOME}/.aws/config}."
  return 0
}

# Every profile in an ~/.aws/config whose credentials the broker mints, one per line.
#
# THE `credential_process` LINE IS THE DISCRIMINATOR AND THE ROLE ARN IS NOT, BECAUSE THE
# ROLE ARN IS NOT IN THE FILE. `install-profiles` prints the ARN it bound to its own stdout
# and then writes three lines per profile: the section header, a credential_process and a
# region. Nothing keyed on `role/Intern-*` would match anything the broker has ever written.
#
# `[default]` is excluded deliberately. The AWS CLI spells it bare rather than as
# `[profile default]`, and it is a profile a person did not pick, so this must not pick it
# either. That is the same rule the CLI itself applies in PR #401.
broker_profiles() {
  local config
  config="${AWS_CONFIG_FILE:-${HOME}/.aws/config}"
  [ -r "${config}" ] || return 0
  awk '
    /^[[:space:]]*\[/ {
      section = $0
      sub(/^[[:space:]]*\[[[:space:]]*/, "", section)
      sub(/[[:space:]]*\].*$/, "", section)
      if (section ~ /^profile[[:space:]]+/) {
        sub(/^profile[[:space:]]+/, "", section)
        current = section
      } else {
        current = ""
      }
      next
    }
    current != "" && /^[[:space:]]*credential_process[[:space:]]*=/ {
      value = $0
      sub(/^[^=]*=[[:space:]]*/, "", value)
      # THE PROGRAM AND NOT A SUBSTRING, AND A QUOTED PATH IS ONE TOKEN. Stripping the
      # quotes and then splitting on spaces was the first draft and it dropped a real
      # profile: an npm global install under `C:\Program Files\...`, or a macOS home with
      # a space in it, splits into a first token of `C:\Program` and matches nothing. A
      # bare substring test would go the other way and match a wrapper script that merely
      # mentions the broker.
      quote = substr(value, 1, 1)
      if (quote == "\"" || quote == "'"'"'") {
        closing = index(substr(value, 2), quote)
        program = (closing > 0) ? substr(value, 2, closing - 1) : substr(value, 2)
      } else {
        split(value, words, /[[:space:]]+/)
        program = words[1]
      }
      parts = split(program, segments, /[\/\\]/)
      base = tolower(segments[parts])
      sub(/\.exe$/, "", base)
      if (base == "sb-aws-creds") print current
    }
  ' "${config}"
}

step_aws_profile() {
  # THE FIFTEEN MINUTES THIS STEP EXISTS FOR. An unset AWS_PROFILE produces a credentials
  # error identical to never having logged in, so somebody who has done everything right
  # goes back and does it all again. It cost Frank and a second tester about a quarter of an
  # hour each today.
  #
  # THIS STEP GOES AWAY, AND IT IS WRITTEN THAT WAY ON PURPOSE. PR #401 makes the lane
  # verbs resolve the profile themselves out of ~/.aws/config, with exactly the rule below:
  # a profile you exported is honoured untouched, one broker profile is used and said out
  # loud, none refuses `no_broker_profile`, and more than one refuses
  # `aws_profile_is_ambiguous` rather than guessing which account to spend. Once that
  # merges, exporting AWS_PROFILE stops being something anybody has to do, and nobody should
  # be taught a line they will later be told to remove. Until then, export it.
  local config declared found count
  config="${AWS_CONFIG_FILE:-${HOME}/.aws/config}"
  declared="${AWS_PROFILE:-}"
  # A blank one is not a choice. `AWS_PROFILE=` is what an unset variable looks like to the
  # AWS CLI, which falls back to `default`, so honouring the empty string as a declaration
  # would leave this resolving nothing while the CLI resolved something else.
  if [ -n "$(printf '%s' "${declared}" | tr -d '[:space:]')" ]; then
    ok "AWS_PROFILE is set to ${declared}, and that is honoured untouched. Nothing here"
    ok "second-guesses a profile you chose out loud."
    return 0
  fi
  found="$(broker_profiles)"
  count="$(printf '%s\n' "${found}" | grep -c . )"
  if [ "${count}" -eq 0 ]; then
    blocked "AWS_PROFILE is not set and ${config} has no profile from ${BROKER}, so there is"
    blocked "no credential to reach the lane with. Nothing is billing. Run \`${BROKER} login\`"
    blocked "and then \`${BROKER} install-profiles\`, in that order, and run this again."
    return 1
  fi
  if [ "${count}" -gt 1 ]; then
    blocked "AWS_PROFILE is not set and ${config} has ${count} profiles from ${BROKER}, so"
    blocked "which one to spend is yours to say: $(printf '%s' "${found}" | tr '\n' ' ')"
    blocked "Add \`export AWS_PROFILE=<one of those>\` to your shell profile and run this again."
    return 1
  fi
  ok "one profile from ${BROKER} in ${config}: ${found}"
  note "AWS_PROFILE is not set. Add this to ~/.zshrc or ~/.bashrc and open a new terminal:"
  note "  export AWS_PROFILE=${found}"
  note "This line is temporary. PR #401 makes the lane verbs read ${config} themselves and"
  note "pick that same profile, saying which one they used before anything starts billing."
  note "Once it merges, the export is unnecessary and can come out."
  export AWS_PROFILE="${found}"
  return 0
}

step_identity() {
  # WHAT SEPARATES "NOT LOGGED IN" FROM "LOGGED IN AND AWS_PROFILE UNSET", WHICH IS THE
  # WHOLE OF WHY IT IS HERE. Those two produce the same message from every AWS tool, and
  # this is the one call that tells them apart. It is read-only, it starts nothing, and it
  # bills nothing.
  local answer status
  answer="$(aws sts get-caller-identity --query Arn --output text 2>&1)"
  status=$?
  if [ ${status} -ne 0 ]; then
    blocked "the AWS CLI could not name who you are, so the lane will refuse. Nothing is"
    blocked "billing. It said: $(first_line "${answer}")"
    blocked "If that mentions a profile or a credential, the two broker steps above are what"
    blocked "fix it. If it mentions the ${BROKER} command, the broker is not on this PATH in"
    blocked "the shell the AWS CLI ran under."
    return 1
  fi
  ok "AWS answers to ${answer}"
  return 0
}

step_session_plugin() {
  # LAST, AND THAT IS THE CORRECTION. This is the one thing in the lane track a person can
  # install for themselves, and it is worth nothing without everything above it. Somebody
  # who cannot get the broker should never have spent a download and a sudo on this.
  local where
  where="$(on_path "${SESSION_PLUGIN}")"
  if [ -n "${where}" ]; then
    ok "${SESSION_PLUGIN} at ${where}"
    return 0
  fi
  # Installed but not on PATH is a real state on macOS too: the package puts the binary
  # under /usr/local/sessionmanagerplugin and symlinks it, and a laptop whose /usr/local/bin
  # is not on PATH has the plugin and cannot run it.
  if [ -x /usr/local/sessionmanagerplugin/bin/session-manager-plugin ]; then
    blocked "${SESSION_PLUGIN} is installed at /usr/local/sessionmanagerplugin/bin and is not"
    blocked "on PATH, so a lane session will refuse \`session_plugin_missing\` on a laptop"
    blocked "that has it. Add /usr/local/bin to PATH, or symlink the binary into it."
    return 1
  fi
  blocked "${SESSION_PLUGIN} is not on PATH. A lane session is a Systems Manager session"
  blocked "rather than SSH, which is why there is no key to hold and no port open on the"
  blocked "machine, and the plugin is the piece of that which runs here. Install it from"
  blocked "the AWS documentation for the Session Manager plugin, open a new terminal, and"
  blocked "run this again."
  return 1
}

# ---------------------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------------------

run_track() {
  local title step function status
  title="$1"
  shift
  say ""
  say "${title}"
  for step in "$@"; do
    function="step_${step//-/_}"
    if ! type "${function}" >/dev/null 2>&1; then
      say "this script names a step it does not implement: ${step}" >&2
      exit "${EXIT_UNUSABLE}"
    fi
    say "${step}"
    "${function}"
    status=$?
    if [ ${status} -ne 0 ]; then
      say ""
      say "Stopped at ${step}. Nothing after it was attempted, because every step below it"
      say "depends on this one. Fix what it named and run this again."
      return 1
    fi
  done
  return 0
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --lane) WANT_LANE=1 ;;
      --check) CHECK_ONLY=1 ;;
      -h | --help)
        usage
        return "${EXIT_READY}"
        ;;
      *)
        say "unknown argument: $1" >&2
        usage >&2
        return "${EXIT_UNUSABLE}"
        ;;
    esac
    shift
  done

  say "eduLLM laptop setup, on $(uname -s)."
  if [ "${CHECK_ONLY}" -eq 1 ]; then
    say "Checking only. Nothing will be installed, uninstalled or logged in to."
  fi

  if ! run_track "The submission track. This is everybody, and it needs no AWS access." \
    "${SUBMISSION_STEPS[@]}"; then
    return "${EXIT_BLOCKED}"
  fi

  if [ "${WANT_LANE}" -eq 1 ]; then
    if ! run_track "The lane track, for edullm run, shell and stop only." \
      "${LANE_STEPS[@]}"; then
      return "${EXIT_BLOCKED}"
    fi
  else
    say ""
    say "The lane track was not asked for and most people never need it. Pass --lane if you"
    say "want \`edullm run\`, \`edullm shell\` or \`edullm stop\`, which start a machine of"
    say "your own and need an AWS session. Everything you can cite comes off check and"
    say "submit, which are done."
  fi

  say ""
  if [ "${BLOCKED}" -ne 0 ]; then
    say "Not ready. Something above is blocked."
    return "${EXIT_BLOCKED}"
  fi
  if [ "${WARNED}" -ne 0 ]; then
    say "Ready, with warnings above worth reading."
  else
    say "Ready. Next: guides/day-one.md, which is a real first job that prints a number."
  fi
  return "${EXIT_READY}"
}

# RUN WHEN RUN, AND DEFINE WITHOUT RUNNING WHEN SOURCED. `tests/test_setup_script.py` drives
# `broker_profiles` and `version_compare` one at a time against fixtures, and those are the
# two pieces of real logic in here rather than a call to somebody else's tool. Without this
# guard, sourcing the file to reach them would start installing things.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
