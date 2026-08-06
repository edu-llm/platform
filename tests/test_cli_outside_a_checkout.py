"""The CLI run the way it is actually installed: from somewhere that is not this repository.

**THIS IS THE FILE THAT WOULD HAVE CAUGHT IT, AND ITS ABSENCE IS WHY NOTHING DID.**
``edullm run`` and ``edullm shell`` shipped on 2026-08-05 reading
``config/reports/working-tier.yaml`` relative to the working directory. Both raised
``FileNotFoundError`` for every person either verb was written for, because either verb is
used from a research repository and never from a platform checkout. 207 test modules were
green over them, and they were green for one reason: pytest starts in the repository root,
so a relative path to ``config/`` always resolved and the defect had no way to appear.

So the condition this file supplies is the one the suite could not produce. The process
stands in a temporary directory with no platform checkout anywhere above it, and the
reviewed configuration is reachable through exactly one route, which is the resolver. That
is what an install is: the files are in the wheel, the working directory belongs to somebody
else's repository, and the only thing joining the two is
:func:`~edullm_platform.config.find_config_directory`.

**THE CONFIGURATION IS A COPY AND THE COPY IS EDITED, WHICH IS THE PART THAT MAKES THIS
PROOF RATHER THAN THEATRE.** A test that runs from a temporary directory and still reads
this repository's ``config/`` by some other route has reproduced the original bug in the
test harness: it would pass with the relative path restored. So the fixture copies
``config/`` somewhere else and changes two numbers in the copy, and the cases below assert
that the numbers the CLI puts in an AWS call are the *edited* ones. Nothing but a read of
the copy can produce them.

**IT IS NOT THE WHOLE ANSWER AND THE OTHER PART IS GENERAL.** ``tests/cli_support.py``'s
``invoke`` now chdirs into the temporary directory it was already handing to ``main``, so
every CLI case in this suite runs outside a checkout by default. This file is what remains
after that: the cases that need the configuration to come from somewhere other than
``--config-dir`` pointed at this repository.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path

import pytest

from edullm_platform.cli.lane import AWS_BROKER, SESSION_PLUGIN, WorkingTierSettings
from edullm_platform.cli.main import (
    EXIT_OK,
    EXIT_UNREACHABLE,
    EXIT_UNUSABLE,
    LOCAL_NOTEBOOK_PORT,
    main,
)
from edullm_platform.reviewed_configuration import (
    CONFIG_DIRECTORY_VARIABLE,
    SENTINEL_FILE,
    ConfigFile,
    load_config_file,
)
from tests.cli_support import (
    ONE_BROKER_PROFILE,
    FakeRunner,
    failed,
    git_answers,
    lane_answers,
    write_spec,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Two numbers changed in the copy, chosen because each one reaches a different AWS call and
#: neither is anywhere near what the committed file says. A verb reading the repository's own
#: ``config/`` by any route produces the committed values instead and fails the assertion.
EDITED_ROOT_VOLUME_GIB = 137
EDITED_NOTEBOOK_PORT = 9137


class Installed:
    """Where the configuration is and where the person is standing, which are not the same."""

    def __init__(self, configuration: Path, working: Path) -> None:
        self.configuration = configuration
        self.working = working

    def settings(self) -> WorkingTierSettings:
        return load_config_file(
            ConfigFile.WORKING_TIER, WorkingTierSettings, directory=self.configuration
        )


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Installed:
    """An install: configuration in one place, the working directory in another.

    ``EDULLM_CONFIG_DIR`` stands in for the copy inside a wheel, which is the third of the
    four sources and the one an ordinary install takes. The suite cannot take that one --
    ``edullm_platform/_config`` is placed by ``force-include`` at wheel build time and an
    editable install has none -- and the variable reaches the same code by the same
    function, so what is exercised here is the resolution rather than a simulation of it.
    The route no test can reach is covered outside the suite, by installing the built wheel
    and running both verbs from a directory that is not this repository.
    """
    configuration = tmp_path / "an-install" / "_config"
    shutil.copytree(PROJECT_ROOT / "config", configuration)
    _edit_the_copy(configuration)

    working = tmp_path / "somebodys-research-repository"
    working.mkdir()

    monkeypatch.setenv(CONFIG_DIRECTORY_VARIABLE, str(configuration))
    monkeypatch.chdir(working)
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "_no-gh-config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "_no-config-home"))
    monkeypatch.setenv("EDULLM_GITHUB_LOGIN", "caiiris")
    tools = tmp_path / "_tools"
    tools.mkdir()
    # BOTH LOCAL PREREQUISITES, because the lane checks for both before it makes a call and this
    # fixture is about what an install outside a checkout resolves rather than about either wall.
    for name in (SESSION_PLUGIN, AWS_BROKER):
        stub = tools / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    # POINTED AT THIS DIRECTORY FOR THE REASON ``cli_support.invoke`` DOES IT: unset, the profile
    # resolution reads the developer's own ``~/.aws/config``, and whether these cases pass would
    # depend on whether that laptop has run the broker's second step.
    aws_config = tmp_path / "_aws-config"
    aws_config.write_text(ONE_BROKER_PROFILE, encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws_config))
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    return Installed(configuration=configuration, working=working)


def _edit_the_copy(configuration: Path) -> None:
    settings = configuration / ConfigFile.WORKING_TIER.value
    lines = []
    for line in settings.read_text(encoding="utf-8").splitlines():
        if line.startswith("root_volume_gib:"):
            line = f"root_volume_gib: {EDITED_ROOT_VOLUME_GIB}"
        elif line.startswith("notebook_port:"):
            line = f"notebook_port: {EDITED_NOTEBOOK_PORT}"
        lines.append(line)
    settings.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_verb(argv: list[str], *, runner: FakeRunner, cwd: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, runner=runner, out=out, err=err, cwd=cwd)
    return code, out.getvalue(), err.getvalue()


def a_laptop(working: Path, **overrides: object) -> FakeRunner:
    return FakeRunner(
        {
            **git_answers(working, repository="somebodys-personal-scratchpad"),
            **lane_answers(**overrides),
        }
    )


def test_no_platform_checkout_is_anywhere_above_the_working_directory(
    installed: Installed,
) -> None:
    """THE NON-VACUITY GUARD, AND IT IS THE FIRST TEST IN THE FILE FOR A REASON.

    Every case below asserts that a verb worked with no checkout under it. If a checkout were
    under it, the walk-up in :func:`~edullm_platform.config.find_config_directory` would find
    one, the relative paths that shipped would resolve again, and each case would pass while
    proving nothing -- which is exactly the failure this whole file exists to correct, rebuilt
    one level up.

    The copy is checked too. A copy placed inside this repository would be found by the
    walk-up from somewhere else and would make the edited numbers below reachable by an
    accident rather than through the resolver.
    """
    for directory in (installed.working, *installed.working.parents):
        assert not (directory / "config" / SENTINEL_FILE).is_file(), (
            f"{directory} is a platform checkout, so the working directory below it is not "
            "outside one and nothing in this file is testing what it claims to"
        )
    assert not installed.configuration.is_relative_to(PROJECT_ROOT)
    assert Path.cwd() == installed.working


def test_run_starts_a_machine_with_the_disk_the_configuration_it_found_asks_for(
    installed: Installed,
) -> None:
    """**THE CASE. Mutation: read the working tier by a path against the working directory.**

    That is what shipped, and under this fixture it raises ``FileNotFoundError`` rather than
    launching, because there is no ``config/`` below the person and none above them either.

    The size is asserted against the copy rather than against a number written here, so the
    assertion holds a read rather than a value: 137 GiB is in the copy and nowhere else, so a
    launch carrying it read the copy, and a launch carrying the committed 200 read this
    repository through some route this test did not intend.
    """
    runner = a_laptop(installed.working)

    code, out, err = run_verb(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=installed.working,
    )

    assert code == EXIT_OK, out + err
    launched = runner.ran("aws", "ec2", "run-instances")
    assert launched, "no machine was launched, so nothing read the working tier at all"
    assert f"VolumeSize={installed.settings().root_volume_gib}" in " ".join(launched[0])
    assert f"VolumeSize={EDITED_ROOT_VOLUME_GIB}" in " ".join(launched[0])


def test_shell_forwards_the_notebook_port_the_configuration_it_found_names(
    installed: Installed,
) -> None:
    """The second verb, and it reads the same file twice as far as anybody could tell.

    ``edullm shell --notebook`` used to load the working tier a second time for the port,
    from a second resolution of a directory that had already been resolved. It carries the
    session's copy now, and this holds the port that reaches Systems Manager to the file the
    session was built from.
    """
    runner = a_laptop(installed.working)

    code, out, err = run_verb(
        ["shell", "--project", "mixlaw", "--compute", "gpu-1xt4", "--notebook"],
        runner=runner,
        cwd=installed.working,
    )

    assert code == EXIT_OK, out + err
    forwarded = runner.ran("aws", "ssm", "start-session")
    assert forwarded, "no session was opened, so nothing read the working tier at all"
    parameters = json.loads(forwarded[-1][-1])
    assert parameters["portNumber"] == [str(installed.settings().notebook_port)]
    assert parameters["portNumber"] == [str(EDITED_NOTEBOOK_PORT)]
    assert parameters["localPortNumber"] == [str(LOCAL_NOTEBOOK_PORT)]


@pytest.mark.parametrize("verb", ["run", "shell"])
def test_a_lane_verb_with_no_aws_session_refuses_rather_than_raising(
    installed: Installed, verb: str
) -> None:
    """The stopping point a person without a session should reach, and it is not a traceback.

    Mutation: let the ``FileNotFoundError`` back in. This is the exact shape of the bug
    report -- somebody outside a platform checkout typing a lane verb -- and the difference
    between the two outcomes is whether they are told what to do. A refusal names the command
    that gets them a session; a traceback names a file in a directory they have never heard
    of and were never going to have.

    Both settings files are read before this point, deliberately, so reaching the refusal is
    itself the evidence that the configuration resolved.
    """
    runner = a_laptop(installed.working)
    runner._answers[("aws", "sts", "get-caller-identity")] = failed(
        "Unable to locate credentials. You can configure credentials by running "
        '"aws configure".'
    )

    code, out, err = run_verb(
        [verb, "--project", "mixlaw", "--compute", "gpu-1xt4", *(["--", "true"] if verb == "run" else [])],
        runner=runner,
        cwd=installed.working,
    )

    assert code == EXIT_UNREACHABLE, out + err
    assert "sb-aws-creds login" in err
    assert "Traceback" not in err
    assert "FileNotFoundError" not in err


def test_an_installation_missing_a_settings_file_says_so_and_starts_nothing(
    installed: Installed,
) -> None:
    """Mutation: delete the working tier from the copy.

    THE OTHER HALF OF THE PROOF ABOVE. The edited numbers show that the copy is read; this
    shows that it is the *only* thing read, because removing it from the copy stops the verb
    rather than sending it to this repository's own. A harness where both were reachable
    would pass the earlier cases and fail this one.

    Exit 2 rather than a traceback, because an installation that cannot find its own numbers
    is unusable rather than refused, and the two are different things a person does different
    things about.
    """
    (installed.configuration / ConfigFile.WORKING_TIER.value).unlink()
    runner = a_laptop(installed.working)

    code, out, err = run_verb(
        ["run", "--project", "mixlaw", "--compute", "gpu-1xt4", "--", "python", "-V"],
        runner=runner,
        cwd=installed.working,
    )

    assert code == EXIT_UNUSABLE, out + err
    assert "working-tier.yaml" in err
    assert not runner.ran("aws", "ec2", "run-instances")


def test_check_prices_a_submission_from_outside_a_checkout_too(installed: Installed) -> None:
    """The control, and the reason it is worth a case of its own.

    ``check`` has worked from an install since the day it shipped, because it takes the
    reviewed configuration off ``ReviewedConfiguration`` rather than naming files. So this
    passes before the fix and after it, and what it proves is that the fixture is an
    ordinary install rather than a hostile one: a harness in which nothing could work would
    make every case above pass for the wrong reason.
    """
    write_spec(installed.working)
    runner = FakeRunner(git_answers(installed.working))

    code, out, err = run_verb(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment", "--team", "scratch"],
        runner=runner,
        cwd=installed.working,
    )

    assert code == EXIT_OK, out + err
    assert str(installed.configuration) in out
