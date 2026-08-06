"""``edullm studio``: what it prices, what it starts, what it stops and what it refuses.

**THE CASES ARE ORDERED BY WHAT THEY COST TO GET WRONG.** The expensive mistakes here are
starting a second app beside one already running, and reporting a stop that did not happen, so
those two come first. The refusals follow, then the argv the verb builds, which is where the
tags and the deep link live.

Nothing here reaches AWS. ``lane_answers`` and ``studio_answers`` describe the account and
``FakeRunner`` refuses any call a fixture did not declare, which is what makes a new call the
verb learns to make a failure by name rather than a network read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNREACHABLE
from edullm_platform.cli.studio import (
    APP_NAME,
    APP_TYPE,
    IMAGE_ACCOUNT_PARAMETER,
    PERSON_TAG_KEY,
    STUDIO_NAME_LIMIT,
    SURFACE_TAG_KEY,
    SURFACE_TAG_VALUE,
    RunningApp,
    StudioRequest,
    StudioSettings,
    create_app_argv,
    create_space_argv,
    create_user_profile_argv,
    delete_app_argv,
    image_account_argv,
    image_arn_for,
    landing_uri,
    load_studio_settings,
    presigned_url_argv,
    price_said,
    running_app,
    shape_for,
    studio_name_for,
    studio_refusals,
    studio_tags,
)
from edullm_platform.researcher_lane import GOVERNANCE_TAG_KEYS, PROJECT_TAG_KEY
from tests.cli_support import (
    CONFIG_DIR,
    STUDIO_IMAGE_ACCOUNT,
    STUDIO_URL,
    FakeRunner,
    failed,
    git_answers,
    invoke,
    lane_answers,
    ok,
    studio_answers,
)

#: The person ``lane_answers`` federates as, and therefore the space every case below opens.
THE_PERSON = "caiiris"


def a_studio(tmp_path: Path, **overrides: object) -> FakeRunner:
    """A laptop holding a session, against a domain that exists."""
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers(**overrides))  # type: ignore[arg-type]
    return FakeRunner(answers)


def settings() -> StudioSettings:
    """The rate card this repository ships, which is the one the verb reads."""
    return load_studio_settings(CONFIG_DIR)


def a_request(project: str = "mixlaw", person: str = THE_PERSON) -> StudioRequest:
    return StudioRequest(person=person, studio_name=studio_name_for(person), project=project)


# ---------------------------------------------------------------------------------------
# the two that cost money to get wrong
# ---------------------------------------------------------------------------------------


def test_an_app_already_running_is_answered_with_its_link_and_never_a_second_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ONE THAT MATTERS HERE. Mutation: read "start or resume" as "start".

    Studio permits more than one app on a space, so this mistake is available, it is silent,
    and it doubles somebody's hourly rate under their own name with nothing in the console
    saying which of the two anybody is looking at.
    """
    runner = a_studio(tmp_path, app_status="InService")

    code, out, _ = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert not runner.ran("aws", "sagemaker", "create-app"), (
        "a second app was started beside one already running"
    )
    assert "already running" in out
    assert STUDIO_URL in out


def test_a_pending_app_counts_as_running_because_the_instance_is_already_allocated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: treat anything that is not ``InService`` as nothing.

    An app coming up is an instance already bought. Reporting it as absent starts a second
    one, which is the same failure as the case above arriving through a narrower door.
    """
    runner = a_studio(tmp_path, app_status="Pending")

    code, _, _ = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert not runner.ran("aws", "sagemaker", "create-app")


def test_a_stop_that_sagemaker_refused_is_never_reported_as_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE OTHER ONE THAT MATTERS. Mutation: exit 0 whatever ``delete-app`` answered.

    A stop reported as done and not done leaves somebody believing they are not being billed
    while they are, which is the exact belief this verb exists to make true.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers(app_status="InService"))
    answers[("aws", "sagemaker", "delete-app")] = failed("An error occurred (ThrottlingException)")
    runner = FakeRunner(answers)

    code, out, err = invoke(
        ["studio", "--stop"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    # Compared without the line breaks, because the message is wrapped for a terminal and a
    # test pinned to where it wraps would fail on a reworded sentence that says the same thing.
    assert "still running and still billing by the hour" in " ".join(err.split())
    assert out == ""


def test_stopping_deletes_the_app_and_says_the_volume_goes_on_costing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: say the charge has ended, full stop.

    ``delete-app`` is how Studio spells stop and it leaves the space's volume behind, so a
    message that stopped at "the charge has ended" would be false about the disk -- which is
    the charge somebody discovers a month later.
    """
    runner = a_studio(tmp_path, app_status="InService")

    code, out, _ = invoke(
        ["studio", "--stop"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert runner.ran("aws", "sagemaker", "delete-app")
    assert "hourly charge has ended" in out
    assert "a month" in out


def test_stopping_nothing_is_exit_zero_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: refuse on the second call.

    The state this verb exists to produce is already the state, and a cleanup command that
    cannot be run twice is one nobody puts in a script or a shell alias.
    """
    runner = a_studio(tmp_path)

    code, out, _ = invoke(
        ["studio", "--stop"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert not runner.ran("aws", "sagemaker", "delete-app")
    assert "no running app" in out


def test_stopping_needs_no_project_and_starting_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: require ``--project`` on both, or on neither.

    The space is the caller's own and there is one of them, so nothing about stopping it
    depends on what it was for. Starting is the call that spends money and the project tag is
    the only thing that will ever say whose budget it came out of.
    """
    stopping, _, _ = invoke(
        ["studio", "--stop"], runner=a_studio(tmp_path), cwd=tmp_path, monkeypatch=monkeypatch
    )
    starting, _, err = invoke(
        ["studio"], runner=a_studio(tmp_path), cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert stopping == EXIT_OK
    assert starting == EXIT_REFUSED
    assert "no_project" in err


# ---------------------------------------------------------------------------------------
# what it says before it spends anything
# ---------------------------------------------------------------------------------------


def test_the_rate_is_printed_before_the_app_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: price it afterwards, or not at all.

    ``check`` prices a submission before it is dispatched and this is the same promise on the
    exploration surface. Printed to stderr, so a caller reading the URL off stdout gets the
    URL and nothing else.
    """
    runner = a_studio(tmp_path)

    code, out, err = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert "an hour at list price" in err
    assert out.strip() == STUDIO_URL


def test_it_says_nothing_will_stop_this_for_you(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the sentence once idle shutdown is assumed to exist.

    The domain has no ``AppLifecycleManagement.IdleSettings`` on its default user settings, on
    the user profile or on any space, and the account holds no Studio lifecycle configuration.
    Measured, not assumed, and already paid for once: an app ran unattended across three
    nights on a GPU shape in August 2026. The day idle shutdown is turned on, this sentence
    becomes false and should be deleted rather than softened.
    """
    runner = a_studio(tmp_path)

    _, _, err = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert "Nothing will stop this for you" in err
    assert "no idle-shutdown setting" in err


def test_a_shape_nobody_priced_is_refused_before_any_credential_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: start it anyway, or refuse after the identity call.

    Starting something the verb cannot quote is the failure it exists to prevent. Refusing
    before ``sts:GetCallerIdentity`` matters separately: a misspelled shape answered with "log
    in first" is a refusal about the wrong thing.
    """
    runner = a_studio(tmp_path)

    code, _, err = invoke(
        ["studio", "--project", "mixlaw", "--instance-type", "ml.p5.48xlarge"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "shape_is_not_priced" in err
    assert not runner.ran("aws", "sts", "get-caller-identity")


def test_the_json_document_carries_no_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: put the sign-in URL in the machine-readable form.

    It is a bearer credential with a five-minute life, and a document is precisely the thing
    somebody redirects into a file and pastes into an issue when asking what went wrong.
    """
    runner = a_studio(tmp_path)

    code, out, _ = invoke(
        ["studio", "--project", "mixlaw", "--json"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    document = json.loads(out)

    assert code == EXIT_OK
    assert STUDIO_URL not in out
    assert document["verb"] == "studio"
    assert document["space"] == THE_PERSON
    assert document["project"] == "mixlaw"
    assert document["idle_shutdown"] is False
    assert document["refused"] is False


# ---------------------------------------------------------------------------------------
# setting somebody up, which is every first invocation
# ---------------------------------------------------------------------------------------


def test_a_first_invocation_makes_the_profile_and_the_space_before_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: create the app first, or make somebody ask for a space by hand.

    Neither create allocates an instance, so the free half happens first and a person whose
    only knowledge is the verb is set up by it.
    """
    runner = a_studio(tmp_path, profile_exists=False, space_exists=False)

    code, _, _ = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    order = [
        " ".join(call[:3])
        for call in runner.calls
        if call[:2] == ("aws", "sagemaker") or call[:2] == ["aws", "sagemaker"]
    ]

    assert code == EXIT_OK
    made = [name for name in order if "create" in name]
    assert made.index("aws sagemaker create-user-profile") < made.index(
        "aws sagemaker create-space"
    )
    assert made.index("aws sagemaker create-space") < made.index("aws sagemaker create-app")


def test_a_returning_person_creates_nothing_but_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: create-and-forgive rather than describe-then-create.

    Attempting a create every time and swallowing ``ResourceInUse`` would make a genuine
    collision -- two people resolving to one Studio name -- indistinguishable from the
    ordinary path.
    """
    runner = a_studio(tmp_path)

    invoke(["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert not runner.ran("aws", "sagemaker", "create-user-profile")
    assert not runner.ran("aws", "sagemaker", "create-space")
    assert runner.ran("aws", "sagemaker", "create-app")


# ---------------------------------------------------------------------------------------
# the name, which is the seam between this surface and the lane
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        ("frank.gonzalez", "frank-gonzalez"),
        ("amy.lin", "amy-lin"),
        ("caiiris", "caiiris"),
        ("a_b.c", "a-b-c"),
        (".leading", "leading"),
        ("trailing.", "trailing"),
        ("...", ""),
        ("", ""),
    ],
)
def test_a_person_becomes_a_name_sagemaker_will_take(person: str, expected: str) -> None:
    """Mutation: hand the working tier's person string straight to SageMaker.

    The broker mints ``broker-frank.gonzalez-<epoch>``, so the person the lane derives is
    ``frank.gonzalez``, and the service refuses it: ``Member must satisfy regular expression
    pattern: [a-zA-Z0-9](-*[a-zA-Z0-9]){0,62}``. That was measured against the live API rather
    than read out of a document, and it is the whole reason this function exists.
    """
    assert studio_name_for(person) == expected


def test_a_long_name_is_cut_to_what_sagemaker_takes_and_still_ends_on_a_character() -> None:
    """Mutation: truncate and stop, which can leave a trailing dash the service refuses."""
    name = studio_name_for("a" * 62 + ".b" * 20)

    assert len(name) <= STUDIO_NAME_LIMIT
    assert not name.endswith("-")
    assert not name.startswith("-")


def test_a_name_with_nothing_left_in_it_is_refused_rather_than_invented() -> None:
    """Mutation: fall back to a default name, which would put two people in one space."""
    refusals = studio_refusals(a_request(person="..."))

    assert [refusal.code for refusal in refusals] == ["studio_name_is_unusable"]


def test_a_session_already_inside_the_lane_is_refused() -> None:
    """Mutation: guess a person. ``sts:GetCallerIdentity`` does not return a source identity,
    so a lane session carries no person at all and any name chosen here is somebody else's."""
    refusals = studio_refusals(StudioRequest(person="", studio_name="", project="mixlaw"))

    assert [refusal.code for refusal in refusals] == ["cannot_tell_who_you_are"]


# ---------------------------------------------------------------------------------------
# the tags, which are the whole reason the spend is ever attributable
# ---------------------------------------------------------------------------------------


def test_every_created_thing_carries_the_person_and_the_project() -> None:
    """THE TAGGING ONE. Mutation: tag the space and not the app, or neither.

    The app is what Cost Explorer bills and what CloudTrail records, and a tag added after the
    fact does not retroactively attribute the hours before it. Untagged, Studio is one
    undifferentiated SageMaker line, which is the hazard in pointing thirty-five people at it.
    """
    request = a_request()
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, None)
    assert shape is not None
    image = image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT)
    created = (
        create_user_profile_argv(settings=loaded, request=request),
        create_space_argv(settings=loaded, request=request, shape=shape, image_arn=image),
        create_app_argv(settings=loaded, request=request, shape=shape, image_arn=image),
    )

    for argv in created:
        assert f"Key={PROJECT_TAG_KEY},Value=mixlaw" in argv
        assert f"Key={PERSON_TAG_KEY},Value={THE_PERSON}" in argv
        assert f"Key={SURFACE_TAG_KEY},Value={SURFACE_TAG_VALUE}" in argv


def test_the_project_key_is_the_one_the_lane_machines_already_carry() -> None:
    """Mutation: spell it ``project``.

    Cost Explorer groups by an exact key, so a lowercase one would put Studio hours in a second
    group beside the lane's and a reader summing a project's spend would find half of it.
    """
    assert PROJECT_TAG_KEY in studio_tags(a_request())
    assert PROJECT_TAG_KEY == "Project"


def test_no_expiry_tag_is_written_because_nothing_would_honour_it() -> None:
    """Mutation: write an ``ExpiresAt``, by analogy with the lane.

    ``infra/expiry-janitor.yaml`` sweeps EC2 instances and has no SageMaker arm, so an expiry
    on a Studio app is a promise nothing here keeps -- worse than no tag, because the next
    reader finds it and concludes something is watching.
    """
    written = set(studio_tags(a_request()))

    assert not written & set(GOVERNANCE_TAG_KEYS) - {PROJECT_TAG_KEY}


# ---------------------------------------------------------------------------------------
# the calls themselves
# ---------------------------------------------------------------------------------------


def test_the_space_is_private_and_owned_by_the_person() -> None:
    """Mutation: drop either half. Neither alone makes a space one person's.

    ``SharingType=Private`` with ``OwnerUserProfileName`` is Studio's own scoping and is what
    both spaces that predate this verb already carry.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, None)
    assert shape is not None
    argv = create_space_argv(
        settings=loaded,
        request=a_request(),
        shape=shape,
        image_arn=image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT),
    )

    assert "SharingType=Private" in argv
    assert f"OwnerUserProfileName={THE_PERSON}" in argv


def test_the_deep_link_is_the_form_the_service_accepts() -> None:
    """Mutation: use ``app:JupyterLab:<space>``, which the documentation's own list suggests.

    The API refuses it -- ``Provided app type JupyterLab is invalid for provided url type app
    for personal apps`` -- and accepts the ``studio::`` form, whose issued token comes back
    carrying ``landingUriDeepLink: /jupyterlab/<space>``. Both were measured against the live
    service on 2026-08-06.
    """
    request = a_request()

    assert landing_uri(request) == f"studio::/jupyterlab/{THE_PERSON}"
    assert "--landing-uri" in presigned_url_argv(settings=settings(), request=request)


def test_the_app_is_created_with_an_image_because_the_service_demands_one() -> None:
    """Mutation: leave the image to a default.

    ``CreateApp`` answers ``SageMaker Image ARN is required for App with type [JupyterLab]``,
    which this account's own trail records somebody discovering the hard way.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, "ml.g4dn.xlarge")
    assert shape is not None
    image = image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT)
    argv = create_app_argv(settings=loaded, request=a_request(), shape=shape, image_arn=image)

    assert f"InstanceType={shape.instance_type},SageMakerImageArn={image}" in argv
    assert APP_TYPE in argv
    assert APP_NAME in argv


def test_the_image_account_is_read_from_aws_and_never_written_down() -> None:
    """Mutation: put Amazon's image account in the rate card.

    Twelve digits anywhere in the tracked tree is refused by ``tests/test_evidence.py``, which
    does not try to judge whose account an id belongs to and should not have to. The public
    SSM parameter is regional and AWS's to move, so reading it keeps the ARN correct as well as
    keeping the literal out -- the same two reasons ``edullm run`` reads its AMI that way.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, "ml.g4dn.xlarge")
    assert shape is not None

    assert IMAGE_ACCOUNT_PARAMETER in image_account_argv()
    assert image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT) == (
        f"arn:aws:sagemaker:{loaded.region}:{STUDIO_IMAGE_ACCOUNT}:image/{shape.image_name}"
    )
    assert all("arn:" not in shape.image_name for shape in loaded.shapes)


def test_an_unreadable_image_account_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: guess the account, or carry on with an empty segment.

    A wrong account segment produces a ``ValidationException`` naming an ARN nobody wrote,
    which is a worse thing to hand somebody than a sentence saying the lookup failed. Nothing
    is created either, which is why the lookup happens before the profile and the space.
    """
    runner = a_studio(tmp_path, profile_exists=False, space_exists=False, image_account=None)

    code, _, err = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert "image_account_unreadable" in err
    assert not runner.ran("aws", "sagemaker", "create-user-profile")
    assert not runner.ran("aws", "sagemaker", "create-space")
    assert not runner.ran("aws", "sagemaker", "create-app")


def test_stopping_names_the_app_and_never_the_space() -> None:
    """Mutation: reach for ``delete-space``, which is what "delete" suggests and would take
    the volume and every file on it with it."""
    argv = delete_app_argv(settings=settings(), request=a_request())

    assert argv[:3] == ("aws", "sagemaker", "delete-app")
    assert "delete-space" not in argv


# ---------------------------------------------------------------------------------------
# reading what the account said
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "billing"),
    [("InService", True), ("Pending", True), ("Deleted", False), ("Failed", False)],
)
def test_an_app_is_billing_only_where_an_instance_is_allocated(status: str, billing: bool) -> None:
    assert RunningApp(status=status, instance_type=None).is_billing is billing


@pytest.mark.parametrize("body", ["", "not json", "[]", "{}", '{"Status": 3}'])
def test_an_unreadable_describe_is_no_app_rather_than_a_traceback(body: str) -> None:
    """Mutation: index into the body. A traceback in front of a researcher is the one thing
    this binary promises not to produce, and an empty body is the ordinary first invocation."""
    assert running_app(body) is None


def test_the_price_names_both_charges_and_says_which_one_stops() -> None:
    """Mutation: quote the hourly rate alone.

    They stop at different times, and conflating them is the misunderstanding the verb exists
    to prevent: the volume is the persistent disk that is the reason to prefer Studio, and it
    is billed whether or not anybody is signed in.
    """
    shape = shape_for(settings(), None)
    assert shape is not None
    said = price_said(shape, settings())

    assert "an hour at list price" in said
    assert "a month whether or not the app is running" in said
    assert "--stop" in said
    assert "The volume charge does not stop." in said


def test_the_reviewed_rate_card_prices_the_default_and_holds_no_duplicate() -> None:
    """Mutation: name a default nothing prices, or price one instance type twice.

    Both are the same failure from two sides -- a verb that cannot quote what it is about to
    start -- and both are refused when the file loads rather than one line before an app.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    priced = [shape.instance_type for shape in loaded.shapes]

    assert loaded.default_instance_type in priced
    assert len(set(priced)) == len(priced)
    assert shape_for(loaded, None) is not None
    assert shape_for(loaded, "ml.nonesuch.xlarge") is None


def test_no_aws_session_is_unreachable_rather_than_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report a missing credential as a refusal.

    Exit 1 means something has to change about the request and retrying it unchanged reaches
    the same place. A laptop that has not logged in has to retry, and the message names the
    one command that does it.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers())
    answers[("aws", "sts", "get-caller-identity")] = failed("Unable to locate credentials")
    runner = FakeRunner(answers)

    code, _, err = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert "sb-aws-creds login" in err


def test_a_url_that_could_not_be_minted_says_the_app_may_be_billing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report the URL failure and stop.

    This is the one failure here that can leave an instance running with no way in, so a
    message that did not name ``--stop`` would leave somebody paying for a machine they cannot
    reach and cannot see.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers())
    answers[("aws", "sagemaker", "create-presigned-domain-url")] = failed("AccessDenied")
    runner = FakeRunner(answers)

    code, _, err = invoke(
        ["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert "edullm studio --stop" in err


def test_nothing_here_starts_an_ec2_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: route this through ``_lane_session``, which starts a machine where it finds
    none. This verb reaches SageMaker and a browser, and a laptop with no Session Manager
    plugin is most of why Studio is the exploration surface at all."""
    runner = a_studio(tmp_path)

    invoke(
        ["studio", "--project", "mixlaw"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        plugin=False,
    )

    assert not runner.ran("aws", "ec2", "run-instances")
    assert not runner.ran("aws", "ssm", "start-session")


def test_the_domain_is_read_from_reviewed_configuration_and_not_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: hard-code the domain id.

    Nothing under ``infra/`` deploys this domain, so the id is a recorded fact rather than a
    stack output, and the one place it is recorded is the file the verb reads.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    runner = a_studio(tmp_path)

    invoke(["studio", "--project", "mixlaw"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    reached = [call for call in runner.calls if call[:2] == ("aws", "sagemaker")]

    assert reached
    for call in reached:
        assert loaded.domain_id in call


def test_the_fixture_declares_every_call_this_verb_makes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture's own tripwire. Mutation: teach the verb a call and not the fixture.

    ``FakeRunner`` raises on a call nobody declared, so this passes by driving both paths
    rather than by asserting anything about them -- which is exactly what it is for.
    """
    invoke(
        ["studio", "--project", "mixlaw"],
        runner=a_studio(tmp_path, profile_exists=False, space_exists=False),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    invoke(
        ["studio", "--stop"],
        runner=a_studio(tmp_path, app_status="InService"),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )


def test_a_second_shape_is_priced_differently_from_the_lane_for_the_same_silicon() -> None:
    """Mutation: reuse ``config/workload-catalog.yaml``'s rate for a Studio shape.

    The catalog prices ``g4dn.xlarge`` as an EC2 instance and Studio bills ``ml.g4dn.xlarge``
    at its own rate for the same card. One number for both would under-quote every Studio
    hour, which is why this rate card is keyed on the string AWS bills against.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    studio_gpu = shape_for(loaded, "ml.g4dn.xlarge")
    assert studio_gpu is not None

    assert studio_gpu.instance_type.startswith("ml.")
    assert all(shape.instance_type.startswith("ml.") for shape in loaded.shapes)
    assert ok  # the import is load-bearing for the fixtures above
