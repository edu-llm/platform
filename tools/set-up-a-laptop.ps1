# Set one Windows laptop up for the eduLLM platform, checking rather than assuming at every
# step and printing what it found.
#
# This is the other half of tools/set-up-a-laptop.sh. They check the same things in the same
# order, and tests/test_setup_script.py fails when they stop doing that.
#
#   powershell -ExecutionPolicy Bypass -File .\set-up-a-laptop.ps1
#   powershell -ExecutionPolicy Bypass -File .\set-up-a-laptop.ps1 -Lane
#   powershell -ExecutionPolicy Bypass -File .\set-up-a-laptop.ps1 -Check
#
# Off a laptop with no platform checkout, which is every researcher's:
#
#   irm https://raw.githubusercontent.com/edu-llm/platform/main/tools/set-up-a-laptop.ps1 -OutFile set-up-a-laptop.ps1
#   powershell -ExecutionPolicy Bypass -File .\set-up-a-laptop.ps1
#
# `-ExecutionPolicy Bypass` IS NOT OPTIONAL AND IS NOT A SHORTCUT. The default policy on
# Windows client is Restricted, which runs no script file at all, and a downloaded file also
# carries a zone marker that makes RemoteSigned refuse it even where the policy is looser. So
# a person who types `.\set-up-a-laptop.ps1` gets a red block of text about running scripts
# being disabled, which reads as this file being broken. The flag applies to that one
# invocation and changes no setting on the machine. `Unblock-File .\set-up-a-laptop.ps1`
# clears the zone marker if you would rather do it that way.
#
# WHY THIS IS A SECOND FILE AND NOT A BRANCH INSIDE THE BASH ONE. There is no bash on a
# native Windows laptop to branch inside of, and a laptop nobody has set up yet is exactly
# the laptop with no Git Bash and no WSL. The two single-file spellings are both worse. One
# PowerShell script for both platforms needs `pwsh` on macOS, which is an install to perform
# before the install script runs. One Python script for both needs an interpreter, and the
# interpreter is what step `uv` exists to obtain. On Windows a bare `python` opens the
# Microsoft Store rather than running anything, which is the class of failure this whole file
# is written to avoid.
#
# THIS FILE HAS NOT BEEN RUN. It was written on macOS, where there is no PowerShell to run it
# with, so what is asserted about it is that it names the same steps in the same order as the
# bash one and that its text says the true things. Treat the first Windows person as the
# test, and say where it stopped. `guides/day-one.md` records that no Windows machine has
# finished this yet, which is still true.

[CmdletBinding()]
param(
    # Also set up `edullm run`, `edullm shell` and `edullm stop`. Most people never need this.
    [switch]$Lane,
    # Report on every step and change nothing.
    [switch]$Check
)

# Native commands write to stderr for ordinary progress, and under a Stop preference
# PowerShell turns that into a terminating error. Every check below reads an exit code
# instead, so the preference is pinned rather than inherited from whatever profile is loaded.
$ErrorActionPreference = 'Continue'

$script:Blocked = $false
$script:Warned = $false

$InstallLine = 'uv tool install --force git+https://github.com/edu-llm/platform'
$FormerNameRemoval = 'uv tool uninstall edullm-platform'
$PlatformRepository = 'edu-llm/platform'
$Broker = 'sb-aws-creds'
$SessionPlugin = 'session-manager-plugin'

# The one place Windows keeps the Session Manager plugin, which the installer adds to the
# machine PATH and does not hand to the shell that ran it.
$PluginInstallDirectory = Join-Path $env:ProgramFiles 'Amazon\SessionManagerPlugin\bin'

function Write-Ok { param([string]$Text) Write-Host ('  ok        ' + $Text) }
function Write-Did { param([string]$Text) Write-Host ('  did       ' + $Text) }
function Write-Note { param([string]$Text) Write-Host ('  note      ' + $Text) }
function Write-Would { param([string]$Text) Write-Host ('  would     ' + $Text) }

function Write-Warn {
    param([string]$Text)
    Write-Host ('  warning   ' + $Text)
    $script:Warned = $true
}

function Write-Blocked {
    param([string]$Text)
    Write-Host ('  blocked   ' + $Text)
    $script:Blocked = $true
}

function Get-FirstLine {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '' }
    return ($Text -split "`r?`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1).Trim()
}

function Get-AllOnPath {
    param([string]$Name)
    $found = Get-Command $Name -All -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $found) { return @() }
    return @($found | ForEach-Object { $_.Source } | Select-Object -Unique)
}

function Get-OnPath {
    param([string]$Name)
    $all = Get-AllOnPath -Name $Name
    if ($all.Count -eq 0) { return $null }
    return $all[0]
}

function Invoke-Capturing {
    param([string]$File, [string[]]$Arguments)
    $output = & $File @Arguments 2>&1 | Out-String
    return [pscustomobject]@{ Output = $output; ExitCode = $LASTEXITCODE }
}

function Get-AwsConfigPath {
    if (-not [string]::IsNullOrWhiteSpace($env:AWS_CONFIG_FILE)) { return $env:AWS_CONFIG_FILE }
    return (Join-Path $env:USERPROFILE '.aws\config')
}

# Every profile in an ~/.aws/config whose credentials the broker mints, in file order.
#
# THE `credential_process` LINE IS THE DISCRIMINATOR AND THE ROLE ARN IS NOT, BECAUSE THE
# ROLE ARN IS NOT IN THE FILE. `install-profiles` prints the ARN it bound to its own stdout
# and then writes three lines per profile: the section header, a credential_process and a
# region. Nothing keyed on `role/Intern-*` would match anything the broker has ever written.
#
# `[default]` is excluded on purpose. The AWS CLI spells it bare rather than as
# `[profile default]`, and it is a profile a person did not pick, so this must not pick it
# either. That is the rule the CLI itself applies in pull request 401.
#
# A quoted program is one token. An npm global install under `C:\Program Files\...` is the
# ordinary case here, and splitting that on spaces gives a first token of `C:\Program`, which
# matches nothing and drops a profile the person really has.
function Get-BrokerProfiles {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $section = $null
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[(.+)\]') {
            $header = $Matches[1].Trim()
            if ($header -match '^profile\s+(.+)$') { $section = $Matches[1].Trim() } else { $section = $null }
            continue
        }
        if ($null -eq $section) { continue }
        if ($trimmed -notmatch '^credential_process\s*=\s*(.+)$') { continue }
        $value = $Matches[1].Trim()
        if ($value -match '^"([^"]*)"' -or $value -match "^'([^']*)'") {
            $program = $Matches[1]
        }
        else {
            $program = ($value -split '\s+')[0]
        }
        # Parenthesised rather than chained. `-replace` and `-eq` sit at one precedence level
        # and this is a file nobody here can run, so the one line whose meaning depends on
        # remembering that correctly is the one line to spell out.
        $base = [System.IO.Path]::GetFileName($program.Replace('/', '\'))
        $base = ($base.ToLowerInvariant() -replace '\.exe$', '')
        if ($base -eq $Broker) { $found.Add($section) }
    }
    return @($found)
}

# ---------------------------------------------------------------------------------------
# the order, which is the whole point of the file
# ---------------------------------------------------------------------------------------
#
# These two dictionaries drive execution rather than describing it, and the ids are the same
# ids `tools/set-up-a-laptop.sh` uses, in the same order.

# THE SUBMISSION TRACK, AND IT IS ALL MOST PEOPLE NEED. `check`, `submit`, `status`, `logs`
# and `cancel` drive git and gh and hold no cloud credential. The AWS credential lives inside
# a workflow whose trust policy pins it to one file on main, so a laptop cannot obtain one and
# does not need one. Somebody with no AWS access at all runs every verb in this track and
# produces runs anybody can cite.
$SubmissionSteps = [ordered]@{

    'uv' = {
        $where = Get-OnPath 'uv'
        if ($null -eq $where) {
            Write-Blocked 'uv is not on PATH, and it is what installs the tool and the Python under it.'
            Write-Note 'Install it, then open a new PowerShell window and run this again:'
            Write-Note '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
            return $false
        }
        Write-Ok ('uv at ' + $where + ', ' + (Get-FirstLine (Invoke-Capturing 'uv' @('--version')).Output))
        return $true
    }

    'git' = {
        $where = Get-OnPath 'git'
        if ($null -eq $where) {
            Write-Blocked 'git is not on PATH. Install Git for Windows from https://git-scm.com/download/win.'
            return $false
        }
        Write-Ok ('git at ' + $where + ', ' + (Get-FirstLine (Invoke-Capturing 'git' @('--version')).Output))
        return $true
    }

    'gh' = {
        $where = Get-OnPath 'gh'
        if ($null -eq $where) {
            Write-Blocked 'gh is not on PATH. Every verb in the submission track drives it.'
            Write-Note 'Install it from https://cli.github.com, open a new window, and run this again.'
            return $false
        }
        Write-Ok ('gh at ' + $where + ', ' + (Get-FirstLine (Invoke-Capturing 'gh' @('--version')).Output))
        return $true
    }

    'gh-login' = {
        $answer = Invoke-Capturing 'gh' @('auth', 'status')
        if ($answer.ExitCode -ne 0) {
            Write-Blocked 'gh is installed and not logged in, so nothing in the submission track can ask'
            Write-Blocked 'GitHub anything. Run `gh auth login`, then run this again.'
            Write-Note ('gh said: ' + (Get-FirstLine $answer.Output))
            return $false
        }
        $account = ''
        if ($answer.Output -match 'account\s+(\S+)') { $account = $Matches[1] }
        Write-Ok ('gh is logged in as ' + $account)
        return $true
    }

    # THE ORDER MATTERS AND THIS IS THE HALF THAT HAS TO GO FIRST. The distribution was called
    # `edullm-platform` until 4.2.2 this morning and the console script has always been
    # `edullm`, so anybody who installed before then is filed under a name they have never
    # typed. Installing the new name does not replace that entry. uv keeps both, and the two
    # own the same `edullm` executable, so clearing the old entry afterwards deletes that file
    # without noticing the new install still points at it. That leaves `uv tool list`
    # reporting a healthy `edullm` and nothing runnable on PATH.
    'clear-the-former-name' = {
        if ($Check) {
            $listed = (Invoke-Capturing 'uv' @('tool', 'list')).Output
            if ($listed -match '(?m)^edullm-platform\s') {
                Write-Would ('run `' + $FormerNameRemoval + '`, which this laptop needs: uv has an')
                Write-Would 'install filed under the former name.'
            }
            else {
                Write-Ok 'no install under the former name, so there is nothing to clear.'
            }
            return $true
        }
        $answer = Invoke-Capturing 'uv' @('tool', 'uninstall', 'edullm-platform')
        if ($answer.ExitCode -eq 0) {
            Write-Did ('`' + $FormerNameRemoval + '` removed an install made before the rename.')
            return $true
        }
        # A laptop that never carried the old name answers "`edullm-platform` is not
        # installed" and exits 2. That is the expected answer on most of these machines.
        if ($answer.Output -match 'not installed') {
            Write-Ok 'nothing was filed under the former name, which is uv answering "not installed".'
            return $true
        }
        Write-Warn ('`' + $FormerNameRemoval + '` exited ' + $answer.ExitCode + ': ' + (Get-FirstLine $answer.Output))
        return $true
    }

    'install' = {
        if ($Check) {
            Write-Would ('run `' + $InstallLine + '`.')
        }
        else {
            Write-Host ('  running   ' + $InstallLine)
            $answer = Invoke-Capturing 'uv' @('tool', 'install', '--force', ('git+https://github.com/' + $PlatformRepository))
            Write-Host $answer.Output
            if ($answer.ExitCode -ne 0) {
                Write-Blocked 'the install failed. If it stopped on a path being too long, set UV_CACHE_DIR'
                Write-Blocked 'to something short such as C:\uv, open a new window, and try again.'
                return $false
            }
            Write-Did 'installed.'
        }
        # The phrase uv actually prints is kept whole on one line rather than wrapped across
        # two. `test_nothing_recommends_the_upgrade_command_that_does_not_work` reads these
        # files for it, and a line break inside it reads to that check as a file naming
        # `uv tool upgrade` with no warning attached.
        Write-Note 'Re-running that same line later is the upgrade and the repair, because --force makes'
        Write-Note 'it idempotent. `uv tool upgrade` is not the same thing. For an install pinned at a'
        Write-Note 'release tag, which is the line every release note hands out, uv answers'
        Write-Note '"Nothing to upgrade" and exits 0 however far behind that install has fallen.'
        return $true
    }

    # THE STEP THAT CATCHES A HEALTHY `uv tool list` WITH NOTHING ON PATH. Two faults look
    # identical to somebody typing `edullm` and being told there is no such command, and they
    # have different remedies. The executable is missing, which the install line repairs, or
    # something else called `edullm` is earlier on PATH, which it does not.
    'on-the-path' = {
        $binDirectory = (Get-FirstLine (Invoke-Capturing 'uv' @('tool', 'dir', '--bin')).Output)
        $matched = Get-AllOnPath 'edullm'
        if ($matched.Count -eq 0) {
            Write-Blocked '`edullm` is not on PATH.'
            $expected = if ($binDirectory) { Join-Path $binDirectory 'edullm.exe' } else { $null }
            if ($expected -and (Test-Path -LiteralPath $expected)) {
                Write-Blocked ('It is at ' + $expected + ', so the file exists and this window cannot')
                Write-Blocked 'see it. Run `uv tool update-shell`, then open a new PowerShell window. A'
                Write-Blocked 'window opened before a PATH change never receives it.'
            }
            else {
                Write-Blocked ('uv reports its binaries live in ' + $binDirectory + ' and there is no')
                Write-Blocked ('edullm in it. Re-run `' + $InstallLine + '`.')
            }
            return $false
        }
        $first = $matched[0]
        if ($binDirectory -and ([System.IO.Path]::GetDirectoryName($first) -ne $binDirectory)) {
            Write-Blocked ('the first `edullm` on PATH is ' + $first + ', and uv installed one in')
            Write-Blocked ($binDirectory + '. This window will run the first. Fix PATH or remove the other.')
            return $false
        }
        Write-Ok ('edullm at ' + $first)
        if ($matched.Count -gt 1) {
            Write-Warn ('there is more than one edullm on PATH. The first one wins and is the right one.')
            Write-Warn ('The others are worth removing: ' + ($matched -join ' '))
        }
        return $true
    }

    # Staleness warns and never refuses, which is what the tool itself does at submit time and
    # for the reason recorded there. A release is cut most days, so being behind is the normal
    # state of every install rather than an exceptional one, and a gate on the normal state is
    # a gate everybody learns to skip.
    'version' = {
        $answer = Invoke-Capturing 'edullm' @('--version')
        if ($answer.ExitCode -ne 0) {
            Write-Blocked ('`edullm --version` exited ' + $answer.ExitCode + ': ' + (Get-FirstLine $answer.Output))
            return $false
        }
        $printed = Get-FirstLine $answer.Output
        Write-Ok $printed
        $latest = (Get-FirstLine (Invoke-Capturing 'gh' @('api', ('repos/' + $PlatformRepository + '/releases/latest'), '--jq', '.tag_name')).Output)
        if ((-not ($printed -match 'edullm\s+([0-9][0-9.]*)')) -or [string]::IsNullOrWhiteSpace($latest)) {
            Write-Note 'could not compare that against the current release, which is not a problem. The'
            Write-Note 'probe is allowed to fail and this step never refuses on it.'
            return $true
        }
        $installed = [version]$Matches[1]
        $current = [version]($latest.TrimStart('v'))
        if ($installed -eq $current) {
            Write-Ok ('that is ' + $latest + ', the current release.')
            return $true
        }
        # AHEAD IS THE ORDINARY STATE OF A FRESH INSTALL AND MUST NOT READ AS BEHIND. The
        # install line names the bare URL, so it resolves the default branch, and main carries
        # its version bump from the moment the pull request earning it merges until the tag is
        # cut. Measured on macOS on 2026-08-06: a fresh install answered 4.2.3 against a latest
        # of v4.2.2, and the first draft of this step called it stale and sent the person round
        # a loop that cannot end.
        if ($installed -gt $current) {
            Write-Ok ('that is ahead of ' + $latest + ', the latest release, which is what a fresh install')
            Write-Ok 'off the default branch looks like between a merge and its tag. Nothing to do.'
            return $true
        }
        Write-Warn ($latest + ' is the current release and this is ' + $installed + ', so this install is behind.')
        Write-Warn 'It still submits and nothing here refuses on it. The reviewed configuration travels'
        Write-Warn 'inside the install, so a behind one prices and refuses against older rules.'
        Write-Warn 'Re-run the install line above to move it.'
        return $true
    }

    # THE ONE END-TO-END PROOF OF THE WHOLE TRACK, AND IT COSTS NOTHING. `status` with no run
    # id asks gh who you are and asks GitHub what you have submitted. It dispatches no
    # workflow, so it burns no runner minute. If this answers, the tool runs, gh holds a
    # credential, GitHub accepts it, and this person can submit.
    'reaches-github' = {
        $answer = Invoke-Capturing 'edullm' @('status', '--json')
        if ($answer.ExitCode -ne 0) {
            Write-Blocked ('`edullm status --json` exited ' + $answer.ExitCode + ', so the chain from this')
            Write-Blocked ('laptop to GitHub is not closed: ' + (Get-FirstLine $answer.Output))
            return $false
        }
        if ($answer.Output -notmatch '"format_version"') {
            Write-Blocked '`edullm status --json` exited 0 and printed something that is not the document'
            Write-Blocked ('it promises: ' + (Get-FirstLine $answer.Output))
            return $false
        }
        Write-Ok '`edullm status --json` answered from GitHub. This laptop can submit.'
        return $true
    }
}

# THE LANE TRACK, WHICH IS ONLY FOR `run`, `shell` AND `stop`. Those three start a machine of
# your own and are the exploration route rather than the submission path. Nothing they do is
# checked, priced, approved or written to a lineage record, so what comes off them is a thing
# you saw rather than a result anybody can cite.
#
# THE BROKER IS FIRST AND THE PLUGIN IS LAST, AND THAT ORDER IS THE CORRECTION. Told about the
# plugin first, a newcomer performs a real download and an administrator prompt, clears that
# wall, and then meets the broker, which they cannot install at all. Every minute of the
# plugin install was spent before learning it was unnecessary. The identity check sits between
# them because it is the proof that the four steps above it worked, and there is no reason to
# install anything after learning they did not.
$LaneSteps = [ordered]@{

    # THE FIRST WALL, AND THE ONLY ONE WITH NO INSTALL LINE UNDER IT. Every human AWS
    # credential in this organization comes from this binary. The sandbox issues no long-lived
    # keys and refuses the calls that would create one. It is marked private in its own
    # package.json so it has never been publishable, `npm view sb-aws-creds` answers 404, and
    # it is built out of a private repository this roster is not a member of, so a clone
    # answers 404 as well. Every working copy in the organization was passed hand to hand.
    #
    # Printing an install command here would be worse than printing nothing. It would send
    # people to a 404 and cost each of them the afternoon this check exists to save.
    'broker' = {
        $where = Get-OnPath $Broker
        if ($null -eq $where) {
            Write-Blocked ($Broker + ' is not on PATH, and it is the first of the things a lane session')
            Write-Blocked 'needs. Nothing below this point can work without it and nothing is billing.'
            Write-Blocked 'It cannot be installed from here: the package is private, it is on no registry,'
            Write-Blocked 'and it is built out of a repository this roster cannot read. Ask Frank for the'
            Write-Blocked 'tarball, and check its checksum with him before installing it, or run'
            Write-Blocked '`edullm ask --kind access-request` and say you need the AWS credential broker.'
            Write-Blocked 'The README inside that tarball says to install it with pipx. Ignore that line.'
            Write-Blocked 'It is a Node package and pipx cannot install it.'
            return $false
        }
        Write-Ok ($Broker + ' at ' + $where)
        return $true
    }

    # This also mints the person's Intern-* role, through the broker's self-provision route,
    # so it is the step that turns somebody with no AWS identity into somebody with one. It
    # opens a browser and waits for an approval, which is why -Check does not run it.
    'broker-login' = {
        if ($Check) {
            Write-Would ('run `' + $Broker + ' login`, which opens a browser.')
            return $true
        }
        Write-Host ('  running   ' + $Broker + ' login')
        $answer = Invoke-Capturing $Broker @('login')
        Write-Host $answer.Output
        if ($answer.ExitCode -ne 0) {
            Write-Blocked ('`' + $Broker + ' login` did not complete. Nothing was started and nothing is billing.')
            return $false
        }
        Write-Did 'logged in. That also mints your Intern-* role if you did not have one.'
        Write-Note 'That "Logged in" line is not the end of it. It puts a token in your credential'
        Write-Note 'store and writes no AWS profile, so the next step is what any AWS tool reads.'
        return $true
    }

    # THE SECOND OF THE BROKER'S TWO STEPS, AND THE GAP BETWEEN THEM IS A REAL PLACE PEOPLE
    # STOP. `login` puts a refresh token in the credential store and writes nothing to
    # ~/.aws/config. This is what writes the profile. Somebody who ran the first and not the
    # second holds a working credential that no AWS client on the machine can find, and the
    # message they get says AWS does not know who they are, one command after being told they
    # were logged in.
    'broker-profiles' = {
        if ($Check) {
            Write-Would ('run `' + $Broker + ' install-profiles`.')
            return $true
        }
        Write-Host ('  running   ' + $Broker + ' install-profiles')
        $answer = Invoke-Capturing $Broker @('install-profiles')
        Write-Host $answer.Output
        if ($answer.ExitCode -ne 0) {
            Write-Blocked ('`' + $Broker + ' install-profiles` failed, so ' + (Get-AwsConfigPath) + ' has no')
            Write-Blocked 'profile and no AWS client here can find the credential login just obtained.'
            return $false
        }
        Write-Did ('wrote the managed profile block into ' + (Get-AwsConfigPath) + '.')
        return $true
    }

    # THE FIFTEEN MINUTES THIS STEP EXISTS FOR. An unset AWS_PROFILE produces a credentials
    # error identical to never having logged in, so somebody who has done everything right
    # goes back and does it all again. It cost two people about a quarter of an hour each on
    # 2026-08-06.
    #
    # THIS STEP GOES AWAY, AND IT IS WRITTEN THAT WAY ON PURPOSE. Pull request 401 makes the
    # lane verbs resolve the profile themselves out of ~/.aws/config, with exactly the rule
    # below. A profile you exported is honoured untouched, one broker profile is used and said
    # out loud, none refuses `no_broker_profile`, and more than one refuses
    # `aws_profile_is_ambiguous` rather than guessing which account to spend. Once that merges
    # nobody has to set this, and nobody should be taught a line they will later be told to
    # drop. Until then, set it.
    'aws-profile' = {
        $path = Get-AwsConfigPath
        # A blank one is not a choice. `AWS_PROFILE=` is what an unset variable looks like to
        # the AWS CLI, which falls back to `default`, so honouring an empty string would leave
        # this resolving nothing while the CLI resolved something else.
        if (-not [string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
            Write-Ok ('AWS_PROFILE is set to ' + $env:AWS_PROFILE + ', and that is honoured untouched.')
            Write-Ok 'Nothing here second-guesses a profile you chose out loud.'
            return $true
        }
        $profiles = Get-BrokerProfiles -Path $path
        if ($profiles.Count -eq 0) {
            Write-Blocked ('AWS_PROFILE is not set and ' + $path + ' has no profile from ' + $Broker + ',')
            Write-Blocked ('so there is no credential to reach the lane with. Nothing is billing. Run `' + $Broker)
            Write-Blocked ('login` and then `' + $Broker + ' install-profiles`, in that order, and run this again.')
            return $false
        }
        if ($profiles.Count -gt 1) {
            Write-Blocked ('AWS_PROFILE is not set and ' + $path + ' has ' + $profiles.Count + ' profiles from')
            Write-Blocked ($Broker + ', so which one to spend is yours to say: ' + ($profiles -join ', '))
            Write-Blocked 'Set it to one of those with `setx AWS_PROFILE <name>` and open a new window.'
            return $false
        }
        $only = $profiles[0]
        Write-Ok ('one profile from ' + $Broker + ' in ' + $path + ': ' + $only)
        Write-Note 'AWS_PROFILE is not set. This sets it for this window and for future ones:'
        Write-Note ('  $env:AWS_PROFILE = "' + $only + '"')
        Write-Note ('  setx AWS_PROFILE "' + $only + '"')
        Write-Note ('`setx` writes it for windows opened after this one and does not reach this one,')
        Write-Note 'which is why both lines are here rather than one.'
        Write-Note ('This is temporary. Pull request 401 makes the lane verbs read ' + $path + ' and pick')
        Write-Note 'that same profile, saying which one before anything starts billing. Once it merges'
        Write-Note 'the variable is unnecessary and can come out.'
        $env:AWS_PROFILE = $only
        return $true
    }

    # WHAT SEPARATES "NOT LOGGED IN" FROM "LOGGED IN AND AWS_PROFILE UNSET", WHICH IS THE WHOLE
    # OF WHY IT IS HERE. Those two produce the same message from every AWS tool, and this is
    # the one call that tells them apart. It is read-only, it starts nothing, it bills nothing.
    'identity' = {
        if ($null -eq (Get-OnPath 'aws')) {
            Write-Blocked 'the AWS CLI is not on PATH. Install it from the AWS documentation for the AWS'
            Write-Blocked 'CLI, open a new window, and run this again.'
            return $false
        }
        $answer = Invoke-Capturing 'aws' @('sts', 'get-caller-identity', '--query', 'Arn', '--output', 'text')
        if ($answer.ExitCode -ne 0) {
            Write-Blocked 'the AWS CLI could not name who you are, so the lane will refuse. Nothing is'
            Write-Blocked ('billing. It said: ' + (Get-FirstLine $answer.Output))
            Write-Blocked 'If that mentions a profile or a credential, the two broker steps above are what'
            Write-Blocked ('fix it. If it mentions the ' + $Broker + ' command, the broker is not on PATH in')
            Write-Blocked 'the window the AWS CLI ran under.'
            return $false
        }
        Write-Ok ('AWS answers to ' + (Get-FirstLine $answer.Output))
        return $true
    }

    # LAST, AND THAT IS THE CORRECTION. This is the one thing in the lane track a person can
    # install for themselves, and it is worth nothing without everything above it. Somebody who
    # cannot get the broker should never have spent a download and an administrator prompt on
    # it.
    'session-plugin' = {
        $where = Get-OnPath $SessionPlugin
        if ($null -ne $where) {
            Write-Ok ($SessionPlugin + ' at ' + $where)
            return $true
        }
        # INSTALLED AND NOT ON PATH IS THE WINDOWS CASE, AND IT READS AS A FAILED INSTALL. The
        # installer adds its directory to the machine PATH, and Windows does not hand the new
        # entry to a window that was already open. So the person installs it, runs the same
        # command, gets the same refusal, and concludes the install did not work.
        $installed = Join-Path $PluginInstallDirectory 'session-manager-plugin.exe'
        if (Test-Path -LiteralPath $installed) {
            Write-Blocked ($SessionPlugin + ' is installed at ' + $installed + ' and this window')
            Write-Blocked 'cannot see it, because Windows does not give a PATH change to a window that was'
            Write-Blocked 'already open. The install worked. Close this window, open a new PowerShell'
            Write-Blocked 'window, and run this again.'
            return $false
        }
        Write-Blocked ($SessionPlugin + ' is not on PATH. A lane session is a Systems Manager session')
        Write-Blocked 'rather than SSH, which is why there is no key to hold and no port open on the'
        Write-Blocked 'machine, and the plugin is the piece of that which runs here. Install it from the'
        Write-Blocked 'AWS documentation for the Session Manager plugin, then close this window and open'
        Write-Blocked 'a new one before running this again.'
        return $false
    }
}

function Invoke-Track {
    param([string]$Title, [System.Collections.Specialized.OrderedDictionary]$Steps)
    Write-Host ''
    Write-Host $Title
    foreach ($name in $Steps.Keys) {
        Write-Host $name
        $carried = & $Steps[$name]
        if (-not $carried) {
            Write-Host ''
            Write-Host ('Stopped at ' + $name + '. Nothing after it was attempted, because every step below')
            Write-Host 'it depends on this one. Fix what it named and run this again.'
            return $false
        }
    }
    return $true
}

Write-Host 'eduLLM laptop setup, on Windows.'
if ($Check) {
    Write-Host 'Checking only. Nothing will be installed, uninstalled or logged in to.'
}

if (-not (Invoke-Track -Title 'The submission track. This is everybody, and it needs no AWS access.' -Steps $SubmissionSteps)) {
    exit 1
}

if ($Lane) {
    if (-not (Invoke-Track -Title 'The lane track, for edullm run, shell and stop only.' -Steps $LaneSteps)) {
        exit 1
    }
}
else {
    Write-Host ''
    Write-Host 'The lane track was not asked for and most people never need it. Pass -Lane if you want'
    Write-Host '`edullm run`, `edullm shell` or `edullm stop`, which start a machine of your own and'
    Write-Host 'need an AWS session. Everything you can cite comes off check and submit, which are done.'
}

Write-Host ''
if ($script:Blocked) {
    Write-Host 'Not ready. Something above is blocked.'
    exit 1
}
if ($script:Warned) {
    Write-Host 'Ready, with warnings above worth reading.'
}
else {
    Write-Host 'Ready. Next: guides/day-one.md, which is a real first job that prints a number.'
}
exit 0
