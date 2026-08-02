import json
from typing import Any

from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    IAM_ROOT,
    INFRA_ROOT,
    OIDC_PROVIDER,
    PROJECT_ROOT,
    iam_roles,
    load_template,
    resource_of_type,
    statement_actions,
    walk_strings,
)

ADMISSION_ROLE_PATH = IAM_ROOT / "admission-role.yaml"
SERVICE_ROLES_PATH = IAM_ROOT / "admission-service-roles.yaml"
DEPLOYER_PATH = IAM_ROOT / "infra-deployer-role.yaml"
LINEAGE_PATH = INFRA_ROOT / "lineage-bucket.yaml"
ARTIFACTS_PATH = INFRA_ROOT / "artifacts-bucket.yaml"
STATE_MACHINE_PATH = INFRA_ROOT / "admission-state-machine.yaml"
PHASE2_TEMPLATE_PATHS = (
    ADMISSION_ROLE_PATH,
    SERVICE_ROLES_PATH,
    LINEAGE_PATH,
    ARTIFACTS_PATH,
    STATE_MACHINE_PATH,
)

ADMISSION_ROLE_NAME = "sbsandbox-intern-edullm-admission"
STATES_ROLE_NAME = "sbsandbox-intern-edullm-admission-states"
LAMBDA_ROLE_NAME = "sbsandbox-intern-edullm-admission-lambda"
LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"
ARTIFACTS_BUCKET = "sbsandbox-intern-edullm-artifacts"
VALIDATOR_FUNCTION = "sbsandbox-intern-edullm-admission-validator"
STATE_MACHINE_NAME = "sbsandbox-intern-edullm-admission"
LOG_GROUP_NAME = "/aws/vendedlogs/states/sbsandbox-intern-edullm-admission"

SUBJECT_PREFIX = "repo:edu-llm@306859726/platform@1311508598"
APPROVAL_ENVIRONMENTS = (
    "run-approval-automatic",
    "run-approval-lead",
    "run-approval-admin",
)
EXPECTED_SUBJECTS = [f"{SUBJECT_PREFIX}:environment:{name}" for name in APPROVAL_ENVIRONMENTS]
SUBMIT_WORKFLOW = ".github/workflows/submit-run.yml"
SUBMIT_WORKFLOW_REF = f"edu-llm/platform/{SUBMIT_WORKFLOW}@refs/heads/main"
PHASE1_DEPLOY_WORKFLOW_REF = (
    "edu-llm/platform/.github/workflows/deploy-phase1-ecr.yml@refs/heads/main"
)
PHASE2_DEPLOY_WORKFLOW = ".github/workflows/deploy-phase2-admission.yml"
PHASE2_DEPLOY_WORKFLOW_REF = f"edu-llm/platform/{PHASE2_DEPLOY_WORKFLOW}@refs/heads/main"
PHASE3_DEPLOY_WORKFLOW_REF = (
    "edu-llm/platform/.github/workflows/deploy-phase3-batch.yml@refs/heads/main"
)

STATE_MACHINE_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}:"
        f"stateMachine:{STATE_MACHINE_NAME}"
    )
}
EXECUTION_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}:"
        f"execution:{STATE_MACHINE_NAME}:*"
    )
}
LINEAGE_OBJECTS_ARN = {"Fn::Sub": f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/*"}
STATES_ROLE_ARN = {
    "Fn::Sub": f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:role/{STATES_ROLE_NAME}"
}
LAMBDA_ROLE_ARN = {
    "Fn::Sub": f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:role/{LAMBDA_ROLE_NAME}"
}

# Every one of these is listed in the CloudWatch Logs service authorization reference with
# no resource type, so "*" is the narrowest grant that exists for them.
RESOURCELESS_LOGS_ACTIONS = {
    "logs:CreateLogDelivery",
    "logs:GetLogDelivery",
    "logs:UpdateLogDelivery",
    "logs:DeleteLogDelivery",
    "logs:ListLogDeliveries",
    "logs:PutResourcePolicy",
    "logs:DescribeResourcePolicies",
    "logs:DescribeLogGroups",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
}
PUBLIC_ACCESS_FULLY_BLOCKED = {
    "BlockPublicAcls": True,
    "BlockPublicPolicy": True,
    "IgnorePublicAcls": True,
    "RestrictPublicBuckets": True,
}
AES256_ENCRYPTION = {
    "ServerSideEncryptionConfiguration": [
        {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
    ]
}
CONDITIONAL_WRITE_PARAMETERS = {"ChecksumAlgorithm": "SHA256", "IfNoneMatch": "*"}


def role_named(path: object, name: str) -> dict[str, Any]:
    assert isinstance(path, type(ADMISSION_ROLE_PATH))
    matching = [role for role in iam_roles(load_template(path)) if role["RoleName"] == name]
    assert len(matching) == 1, f"expected exactly one role named {name}"
    return matching[0]


def policy_statements(role: dict[str, Any], policy_name: str) -> list[dict[str, Any]]:
    matching = [policy for policy in role["Policies"] if policy["PolicyName"] == policy_name]
    assert len(matching) == 1, f"expected exactly one inline policy named {policy_name}"
    document = matching[0]["PolicyDocument"]
    assert document["Version"] == "2012-10-17"
    statements = document["Statement"]
    assert isinstance(statements, list)
    return statements


def all_actions(statements: list[dict[str, Any]]) -> list[str]:
    return [action for statement in statements for action in statement_actions(statement)]


def resource_arns(resource: object) -> list[str]:
    """The ARN strings a statement's Resource names, with the Fn::Sub wrappers unwrapped."""
    if isinstance(resource, str):
        return [resource]
    if isinstance(resource, list):
        return [arn for item in resource for arn in resource_arns(item)]
    assert isinstance(resource, dict)
    assert list(resource) == ["Fn::Sub"], f"unexpected Resource shape: {resource}"
    return [resource["Fn::Sub"]]


def arn_segments(arn: str) -> list[str]:
    """Split an ARN into its six segments without the pseudo-parameter colons splitting it."""
    normalized = (
        arn.replace("${AWS::Partition}", "PARTITION")
        .replace("${AWS::Region}", "REGION")
        .replace("${AWS::AccountId}", "ACCOUNT")
    )
    return normalized.split(":", 5)


def state_machine_definition() -> dict[str, Any]:
    template = load_template(STATE_MACHINE_PATH)
    _, machine = resource_of_type(template, "AWS::StepFunctions::StateMachine")
    definition = machine["Properties"]["DefinitionString"]["Fn::Sub"]
    assert isinstance(definition, str)
    parsed = json.loads(definition)
    assert isinstance(parsed, dict)
    return parsed


def test_admission_role_is_bounded_inline_and_session_capped() -> None:
    template = load_template(ADMISSION_ROLE_PATH)

    resources = template["Resources"]
    assert len(resources) == 1
    logical_id, role = resource_of_type(template, "AWS::IAM::Role")
    assert list(resources) == [logical_id]

    properties = role["Properties"]
    assert properties["RoleName"] == ADMISSION_ROLE_NAME
    assert properties["PermissionsBoundary"] == BOUNDARY
    assert properties["MaxSessionDuration"] <= 3600

    policies = properties["Policies"]
    assert len(policies) == 1
    assert policies[0]["PolicyName"] == "start-admission-only"


def test_admission_role_trusts_exactly_the_two_protected_environment_subjects() -> None:
    # GitHub only puts `:environment:<name>` in the subject for a job that declared that
    # environment and cleared its protection rules, so the subject is the approval
    # evidence. The two names are enumerated under one StringEquals key, which is an OR
    # across the array while the other four keys stay ANDed.
    trust = role_named(ADMISSION_ROLE_PATH, ADMISSION_ROLE_NAME)["AssumeRolePolicyDocument"]

    assert trust["Version"] == "2012-10-17"
    assert len(trust["Statement"]) == 1
    assert trust["Statement"][0] == {
        "Effect": "Allow",
        "Principal": {"Federated": OIDC_PROVIDER},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
            "StringEquals": {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                "token.actions.githubusercontent.com:job_workflow_ref": SUBMIT_WORKFLOW_REF,
                "token.actions.githubusercontent.com:repository_owner_id": "306859726",
                "token.actions.githubusercontent.com:repository_id": "1311508598",
                "token.actions.githubusercontent.com:sub": EXPECTED_SUBJECTS,
            }
        },
    }


def test_admission_subject_condition_is_a_three_element_array_of_environment_subjects() -> None:
    # Three since the automatic class landed, and the count is asserted rather than derived
    # from APPROVAL_ENVIRONMENTS so that adding a fourth is a deliberate edit here. Each name
    # in this array is an environment that may reach AWS; a name added without somebody
    # changing this number is a gate nobody decided to trust.
    condition = role_named(ADMISSION_ROLE_PATH, ADMISSION_ROLE_NAME)["AssumeRolePolicyDocument"][
        "Statement"
    ][0]["Condition"]
    subjects = condition["StringEquals"]["token.actions.githubusercontent.com:sub"]

    assert isinstance(subjects, list)
    assert len(subjects) == 3
    assert subjects == EXPECTED_SUBJECTS
    assert [subject.rsplit(":", 1)[1] for subject in subjects] == list(APPROVAL_ENVIRONMENTS)
    assert all(subject.startswith(f"{SUBJECT_PREFIX}:environment:") for subject in subjects)


def test_admission_trust_policy_uses_no_stringlike_and_no_wildcard_anywhere() -> None:
    # A wildcard on the environment segment would be a hole rather than a convenience.
    # Anyone able to edit a workflow file can bring a GitHub environment into existence by
    # naming it in `environment:`; the auto-created environment has no protection rules,
    # so its subject would match a wildcard while having passed no gate at all.
    #
    # THAT ARGUMENT SURVIVED THE ARRIVAL OF A REVIEWER-LESS ENVIRONMENT, AND IT IS WORTH
    # SAYING WHY, BECAUSE THE TWO LOOK ALIKE AND ARE NOT. `run-approval-automatic` has no
    # reviewers on purpose, so it is tempting to read the enumeration above as having
    # conceded what this test defends. It has not. What the enumeration buys is that a
    # subject cannot be brought into existence by editing a workflow file: a wildcard
    # accepts any name an author invents, on an environment auto-created with no protection
    # rules and named in no reviewed diff. A third literal accepts one environment that was
    # created deliberately, is pinned to `main` by a deployment branch policy, has
    # can_admins_bypass false, and reaches this condition only through a change to
    # infra/iam/admission-role.yaml that somebody read.
    #
    # So the property is unchanged. Removing a reviewer is a policy decision recorded in
    # config/policy.yaml and enforced by classify_request; removing the enumeration would
    # be handing subject minting to anyone with write access. This test goes on refusing
    # the second.
    trust = role_named(ADMISSION_ROLE_PATH, ADMISSION_ROLE_NAME)["AssumeRolePolicyDocument"]
    condition = trust["Statement"][0]["Condition"]
    trust_strings = list(walk_strings(trust))

    assert set(condition) == {"StringEquals"}
    assert "StringLike" not in trust_strings
    assert trust_strings
    assert [value for value in trust_strings if "*" in value] == []


def test_admission_role_may_start_and_read_only_its_own_admission_executions() -> None:
    # StartExecution authorizes against the state machine ARN; DescribeExecution and
    # GetExecutionHistory authorize against the execution ARN, which is a different
    # resource type. Scoping the second pair to the state machine ARN denies both.
    statements = policy_statements(
        role_named(ADMISSION_ROLE_PATH, ADMISSION_ROLE_NAME), "start-admission-only"
    )

    assert len(statements) == 2
    start, read = statements
    assert start == {
        "Effect": "Allow",
        "Action": "states:StartExecution",
        "Resource": STATE_MACHINE_ARN,
    }
    assert read["Effect"] == "Allow"
    assert set(statement_actions(read)) == {
        "states:DescribeExecution",
        "states:GetExecutionHistory",
    }
    assert read["Resource"] == EXECUTION_ARN


def test_admission_role_can_neither_stop_an_execution_nor_pass_a_role_nor_reach_s3() -> None:
    # The caller asks for admission; it does not get to abort a decision that is being
    # recorded, name the role the workflow runs as, or write the lineage record itself.
    actions = all_actions(
        policy_statements(
            role_named(ADMISSION_ROLE_PATH, ADMISSION_ROLE_NAME), "start-admission-only"
        )
    )

    assert actions
    assert all(action.startswith("states:") for action in actions)
    assert "states:StopExecution" not in actions
    assert not [action for action in actions if action.lower().startswith(("iam:", "s3:"))]
    assert not any("*" in action for action in actions)


def test_admission_role_outputs_only_the_role_name_and_arn() -> None:
    template = load_template(ADMISSION_ROLE_PATH)
    logical_id, _ = resource_of_type(template, "AWS::IAM::Role")

    assert template["Outputs"] == {
        "RoleName": {"Value": {"Ref": logical_id}},
        "RoleArn": {"Value": {"Fn::GetAtt": [logical_id, "Arn"]}},
    }


def test_service_roles_are_bounded_and_trusted_only_by_their_own_aws_service() -> None:
    template = load_template(SERVICE_ROLES_PATH)
    roles = list(iam_roles(template))

    assert len(template["Resources"]) == 2
    assert [role["RoleName"] for role in roles] == [STATES_ROLE_NAME, LAMBDA_ROLE_NAME]
    for role, service in zip(roles, ("states.amazonaws.com", "lambda.amazonaws.com")):
        assert role["PermissionsBoundary"] == BOUNDARY
        assert role["MaxSessionDuration"] <= 3600
        assert role["AssumeRolePolicyDocument"]["Statement"] == [
            {
                "Effect": "Allow",
                "Principal": {"Service": service},
                "Action": "sts:AssumeRole",
            }
        ]


def test_states_role_invokes_the_validator_and_appends_lineage_and_nothing_else() -> None:
    # Eight statements now, not the three Phase 2 wrote. Three arrived with Phase 3 --
    # batch:SubmitJob on one queue and one job definition, batch:TagResource on the same
    # ARNs because Batch authorizes the tags that submission carries under a separate
    # action name, and ecr:DescribeImageScanFindings on one repository -- and are asserted
    # in tests/test_phase3_infrastructure.py, which also compares the queue name here
    # against the queue infra/batch-compute.yaml creates. Two arrived with the job
    # definition an accepted run registers for itself, batch:RegisterJobDefinition and the
    # iam:PassRole that call needs, and are asserted in
    # tests/test_phase5_infrastructure.py.
    #
    # The count is re-armed rather than relaxed, and it is the whole of "and nothing else"
    # in the name above: this test cannot know what a future statement would grant, so what
    # it holds is that a statement cannot arrive without somebody editing this line. What
    # the test still owns on its own is the Phase 2 claim: the S3 grant is PutObject on the
    # lineage bucket and nothing else, whatever else the role has since been given.
    statements = policy_statements(
        role_named(SERVICE_ROLES_PATH, STATES_ROLE_NAME), "run-admission-workflow"
    )
    invoke, write, _submit, _tag, _register, _pass_role, _describe_scan, _logs = statements

    assert len(statements) == 8
    assert invoke["Effect"] == "Allow"
    assert statement_actions(invoke) == ["lambda:InvokeFunction"]
    assert invoke["Resource"] == [
        {
            "Fn::Sub": (
                "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:"
                f"function:{VALIDATOR_FUNCTION}"
            )
        },
        {
            "Fn::Sub": (
                "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:"
                f"function:{VALIDATOR_FUNCTION}:*"
            )
        },
    ]
    assert write == {
        "Effect": "Allow",
        "Action": "s3:PutObject",
        "Resource": LINEAGE_OBJECTS_ARN,
    }

    s3_actions = [action for action in all_actions(statements) if action.startswith("s3:")]
    assert s3_actions == ["s3:PutObject"]


def test_states_role_uses_star_only_where_the_action_admits_no_resource_type() -> None:
    statements = policy_statements(
        role_named(SERVICE_ROLES_PATH, STATES_ROLE_NAME), "run-admission-workflow"
    )
    unscoped = [statement for statement in statements if statement["Resource"] == "*"]

    assert len(unscoped) == 1
    assert set(statement_actions(unscoped[0])) == RESOURCELESS_LOGS_ACTIONS
    assert all(action.startswith("logs:") for action in statement_actions(unscoped[0]))
    assert all(
        isinstance(statement["Resource"], (dict, list))
        for statement in statements
        if statement is not unscoped[0]
    )


def test_lambda_role_holds_no_s3_action_whatsoever() -> None:
    # The validator is the component that parses an untrusted manifest, and it is
    # deliberately the one that cannot write. The Lambda decides, the state machine
    # records, so a validator talked into a hostile decision still cannot put a byte into
    # the lineage store. The absence is the control, which is why it is asserted directly
    # rather than inferred from the statements that are present.
    role = role_named(SERVICE_ROLES_PATH, LAMBDA_ROLE_NAME)
    statements = policy_statements(role, "write-own-log-group")
    actions = all_actions(statements)

    assert actions
    assert all(action.startswith("logs:") for action in actions)
    assert not [action for action in actions if action.lower().startswith("s3:")]
    assert "s3" not in {value.split(":", 1)[0] for value in walk_strings(role["Policies"])}
    assert all(
        statement["Resource"]
        == {
            "Fn::Sub": (
                "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:"
                f"log-group:/aws/lambda/{VALIDATOR_FUNCTION}:*"
            )
        }
        for statement in statements
    )


def test_service_roles_template_outputs_both_role_names_and_arns() -> None:
    template = load_template(SERVICE_ROLES_PATH)
    states_id, lambda_id = list(template["Resources"])

    assert template["Outputs"] == {
        "StatesRoleName": {"Value": {"Ref": states_id}},
        "StatesRoleArn": {"Value": {"Fn::GetAtt": [states_id, "Arn"]}},
        "LambdaRoleName": {"Value": {"Ref": lambda_id}},
        "LambdaRoleArn": {"Value": {"Fn::GetAtt": [lambda_id, "Arn"]}},
    }


def test_lineage_bucket_is_object_locked_versioned_private_and_retained() -> None:
    # Object Lock has no update path in CloudFormation: it can only be turned on when the
    # bucket is created. Dropping this property and recreating the stack produces a
    # permanently mutable bucket, so the assertion guards a one-way door.
    template = load_template(LINEAGE_PATH)
    _, bucket = resource_of_type(template, "AWS::S3::Bucket")
    properties = bucket["Properties"]

    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["UpdateReplacePolicy"] == "Retain"
    assert properties["BucketName"] == LINEAGE_BUCKET
    assert properties["ObjectLockEnabled"] is True
    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert properties["PublicAccessBlockConfiguration"] == PUBLIC_ACCESS_FULLY_BLOCKED
    assert properties["BucketEncryption"] == AES256_ENCRYPTION


def test_lineage_bucket_sets_no_default_retention_so_records_stay_deletable() -> None:
    # The other half of the one-way door, and the half that is not one. Retention is
    # deliberately absent while this store holds the test submissions that build the
    # phase; the lock is on so the rule can be added later as a stack update rather than
    # a new bucket. Asserted rather than left implicit because a rule appearing here is a
    # decision about how long a mistake survives, not a tidy-up.
    template = load_template(LINEAGE_PATH)
    _, bucket = resource_of_type(template, "AWS::S3::Bucket")

    assert "ObjectLockConfiguration" not in bucket["Properties"]


def test_lineage_bucket_denies_every_write_that_is_not_conditional() -> None:
    # A Deny and not an Allow-with-condition. An Allow can be widened by any other Allow
    # that reaches the same object; an explicit Deny cannot be escaped by adding
    # permissions anywhere, so first-write-wins survives edits made elsewhere.
    template = load_template(LINEAGE_PATH)
    bucket_id, _ = resource_of_type(template, "AWS::S3::Bucket")
    _, policy = resource_of_type(template, "AWS::S3::BucketPolicy")
    document = policy["Properties"]["PolicyDocument"]

    assert policy["Properties"]["Bucket"] == {"Ref": bucket_id}
    assert document["Version"] == "2012-10-17"
    assert len(document["Statement"]) == 1
    statement = document["Statement"][0]
    assert statement["Effect"] == "Deny"
    assert statement["Principal"] == "*"
    assert statement_actions(statement) == ["s3:PutObject"]
    assert statement["Resource"] == LINEAGE_OBJECTS_ARN
    assert statement["Condition"] == {"Null": {"s3:if-none-match": "true"}}


def test_artifacts_bucket_is_a_separate_versioned_private_retained_bucket() -> None:
    # Separate from the lineage bucket on purpose: a deployment zip is meant to be
    # replaced and a lineage record is meant never to be, and one bucket cannot hold both
    # guarantees. Asserted here so a later merge of the two fails a test rather than
    # quietly weakening the lineage store.
    template = load_template(ARTIFACTS_PATH)
    _, bucket = resource_of_type(template, "AWS::S3::Bucket")
    properties = bucket["Properties"]
    strings = list(walk_strings(template))

    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["UpdateReplacePolicy"] == "Retain"
    assert properties["BucketName"] == ARTIFACTS_BUCKET
    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert properties["PublicAccessBlockConfiguration"] == PUBLIC_ACCESS_FULLY_BLOCKED
    assert properties["BucketEncryption"] == AES256_ENCRYPTION
    assert "ObjectLockEnabled" not in properties
    assert LINEAGE_BUCKET not in strings
    assert template["Outputs"] == {
        "BucketName": {"Value": {"Ref": "ArtifactsBucket"}},
        "BucketArn": {"Value": {"Fn::GetAtt": ["ArtifactsBucket", "Arn"]}},
    }


def test_execution_log_group_sits_under_the_shared_vendedlogs_prefix() -> None:
    # /aws/vendedlogs/states/ is what lets every state machine in the account share one
    # CloudWatch Logs resource policy. The account limit is ten per region and this is a
    # shared sandbox, so a group outside the prefix spends a tenth of somebody else's
    # budget and the failure lands on whoever deploys after it runs out.
    template = load_template(STATE_MACHINE_PATH)
    _, log_group = resource_of_type(template, "AWS::Logs::LogGroup")

    assert log_group["DeletionPolicy"] == "Retain"
    assert log_group["UpdateReplacePolicy"] == "Retain"
    assert log_group["Properties"]["LogGroupName"] == LOG_GROUP_NAME
    assert log_group["Properties"]["LogGroupName"].startswith("/aws/vendedlogs/states/")
    assert log_group["Properties"]["RetentionInDays"] >= 30


def test_validator_function_is_pinned_to_a_versioned_artifact_object() -> None:
    # Without S3ObjectVersion, re-uploading a new zip to the same key leaves this
    # resource byte-identical, the change set comes back empty, and a deploy that reports
    # success keeps running the old code.
    template = load_template(STATE_MACHINE_PATH)
    _, function = resource_of_type(template, "AWS::Lambda::Function")
    properties = function["Properties"]

    assert properties["FunctionName"] == VALIDATOR_FUNCTION
    assert properties["Runtime"] == "python3.12"
    assert properties["Handler"] == "edullm_platform.admission_handler.handler"
    assert properties["Role"] == LAMBDA_ROLE_ARN
    assert properties["Code"]["S3Bucket"] == ARTIFACTS_BUCKET
    assert properties["Code"]["S3Key"].endswith(".zip")
    assert properties["Code"]["S3ObjectVersion"]
    assert 0 < properties["Timeout"] <= 300
    assert properties["MemorySize"] >= 128


def test_state_machine_is_standard_and_logs_everything_with_execution_data() -> None:
    template = load_template(STATE_MACHINE_PATH)
    log_group_id, _ = resource_of_type(template, "AWS::Logs::LogGroup")
    _, machine = resource_of_type(template, "AWS::StepFunctions::StateMachine")
    properties = machine["Properties"]

    assert properties["StateMachineName"] == STATE_MACHINE_NAME
    assert properties["StateMachineType"] == "STANDARD"
    assert properties["RoleArn"] == STATES_ROLE_ARN
    assert properties["LoggingConfiguration"] == {
        "Level": "ALL",
        "IncludeExecutionData": True,
        "Destinations": [
            {"CloudWatchLogsLogGroup": {"LogGroupArn": {"Fn::GetAtt": [log_group_id, "Arn"]}}}
        ],
    }


def test_admission_definition_validates_then_records_intent_and_decision() -> None:
    # Phase 3 put ReadImageScan in front of the validator and a submission path behind the
    # Choice, so the state list is longer and StartAt moved. What this test still owns is the
    # Phase 2 spine: validate, write the intent, write the decision, then choose. The new
    # states are asserted in tests/test_phase3_infrastructure.py.
    definition = state_machine_definition()
    states = definition["States"]

    assert definition["StartAt"] == "ReadImageScan"
    assert list(states) == [
        "ReadImageScan",
        "ValidateAndDecide",
        "WriteIntent",
        "WriteDecision",
        "RecordConflict",
        "AdmissionAccepted",
        "ResolveExecutionTarget",
        # RegisterJobDefinition arrived after Phase 3, between resolving the target and
        # submitting against it, because the definition a run is executed on is registered
        # per submission and its revision ARN does not exist until Batch has answered.
        "RegisterJobDefinition",
        "SubmitToBatch",
        "RecordSubmissionFailure",
        "BindingIsFanOut",
        "RecordFanOutSize",
        "WriteBindingForFanOut",
        "WriteBindingForSingleContainer",
        "Submitted",
        "SubmissionFailed",
        "Rejected",
        "AdmissionConflict",
    ]
    assert states["ValidateAndDecide"]["Resource"] == "arn:${AWS::Partition}:states:::lambda:invoke"
    assert states["ValidateAndDecide"]["Parameters"]["FunctionName"] == (
        "${AdmissionValidatorFunction.Arn}"
    )
    assert states["ValidateAndDecide"]["Next"] == "WriteIntent"
    assert states["WriteIntent"]["Next"] == "WriteDecision"
    assert states["WriteDecision"]["Next"] == "AdmissionAccepted"


def test_every_lineage_write_is_conditional_and_lands_on_its_documented_key() -> None:
    states = state_machine_definition()["States"]
    # The two lineage keys come from the handler, which is the only component that knows
    # the run id and the prefix together; the conflict key is derived from the execution
    # name because it must still be writable when the handler's answer is what failed.
    expected_keys = {
        "WriteIntent": "$.admission.intent_key",
        "WriteDecision": "$.admission.decision_key",
        "RecordConflict": "States.Format('conflicts/{}.json', $$.Execution.Name)",
    }

    for name, key_expression in expected_keys.items():
        parameters = states[name]["Parameters"]
        assert states[name]["Resource"] == "arn:${AWS::Partition}:states:::aws-sdk:s3:putObject"
        assert parameters["Bucket"] == LINEAGE_BUCKET
        assert parameters["Key.$"] == key_expression
        # The bucket policy denies any PutObject without If-None-Match, so the conflict
        # record has to be conditional too or the failure path fails as well.
        assert {key: parameters[key] for key in CONDITIONAL_WRITE_PARAMETERS} == (
            CONDITIONAL_WRITE_PARAMETERS
        )


def test_both_lineage_writes_catch_the_error_the_probe_measured() -> None:
    # S3.S3Exception, observed rather than guessed: tools/probe_conditional_write.py
    # wrote one object with IfNoneMatch "*", wrote it again, and read that name off the
    # failed execution. A guessed name here produces a Catch that silently never fires,
    # which is why this waited on a measurement.
    states = state_machine_definition()["States"]

    for name in ("WriteIntent", "WriteDecision"):
        assert states[name]["Catch"] == [
            {
                "ErrorEquals": ["S3.S3Exception"],
                "ResultPath": "$.write_failure",
                "Next": "RecordConflict",
            }
        ]
        # No Retry, and the measurement is the reason rather than an obstacle to it. A
        # retry is only correct for the transient case, scoping it there needs an error
        # name the transient case does not share with the 412, and S3.S3Exception is
        # exactly such a shared name.
        assert "Retry" not in states[name]
    # The last-resort catch stays broad on purpose: whatever stops the conflict record
    # being written, the execution still has to reach a terminal state that says so.
    assert states["RecordConflict"]["Catch"][0]["ErrorEquals"] == ["States.ALL"]
    assert states["RecordConflict"]["Catch"][0]["Next"] == "AdmissionConflict"
    assert states["RecordConflict"]["Next"] == "AdmissionConflict"


def test_the_conflict_record_carries_what_the_error_name_could_not_distinguish() -> None:
    # S3.S3Exception is the generic bucket for every unmodelled S3 error, so a 412 and a
    # transient 500 arrive under one name and the routing cannot tell them apart. The
    # status code lives in the Cause, which ErrorEquals cannot match on. What makes that
    # survivable is that the conflict record preserves the failure rather than only the
    # fact of one, so a reader can still tell a duplicate from a blip.
    states = state_machine_definition()["States"]

    for name in ("WriteIntent", "WriteDecision"):
        assert states[name]["Catch"][0]["ResultPath"] == "$.write_failure"
    # RecordConflict serialises the whole execution state, $.write_failure included.
    assert states["RecordConflict"]["Parameters"]["Body.$"] == "States.JsonToString($)"


def test_every_state_is_reachable_and_every_transition_names_a_real_state() -> None:
    # Step Functions rejects a dangling transition at CreateStateMachine, but an
    # unreachable state deploys quietly, and an orphaned RecordConflict would mean the
    # conflict path exists only in the diff.
    definition = state_machine_definition()
    states = definition["States"]
    reachable = {definition["StartAt"]}
    for state in states.values():
        reachable.update(state[key] for key in ("Next", "Default") if key in state)
        reachable.update(choice["Next"] for choice in state.get("Choices", []))
        reachable.update(catch["Next"] for catch in state.get("Catch", []))

    assert sorted(reachable - set(states)) == []
    assert sorted(set(states) - reachable) == []
    # Submitted rather than Admitted since Phase 3: the accepted branch now continues into
    # submission, so there is no terminal state meaning "admitted and then nothing".
    assert {name for name, state in states.items() if state["Type"] in ("Succeed", "Fail")} == {
        "Submitted",
        "Rejected",
        "AdmissionConflict",
        "SubmissionFailed",
    }


def test_admission_ends_in_a_succeed_or_a_named_rejection_failure() -> None:
    states = state_machine_definition()["States"]
    choice = states["AdmissionAccepted"]

    assert choice["Type"] == "Choice"
    # The true branch runs the accepted submission rather than ending, since Phase 3. The
    # false branch is unchanged, which is the half this test exists for: a rejection still
    # ends in a named failure and never reaches Batch.
    assert choice["Choices"] == [
        {
            "Variable": "$.admission.accepted",
            "BooleanEquals": True,
            "Next": "ResolveExecutionTarget",
        }
    ]
    assert choice["Default"] == "Rejected"
    assert states["Submitted"] == {"Type": "Succeed"}
    assert states["Rejected"]["Type"] == "Fail"
    assert states["Rejected"]["Error"] == "AdmissionRejected"
    assert states["AdmissionConflict"]["Type"] == "Fail"
    assert states["AdmissionConflict"]["Error"] == "AdmissionConflict"


def test_admission_stack_outputs_the_names_and_the_arn_the_workflow_verifies() -> None:
    template = load_template(STATE_MACHINE_PATH)
    machine_id, _ = resource_of_type(template, "AWS::StepFunctions::StateMachine")
    function_id, _ = resource_of_type(template, "AWS::Lambda::Function")
    log_group_id, _ = resource_of_type(template, "AWS::Logs::LogGroup")

    # Ref on a state machine returns its ARN, not its name, so these two are the other
    # way round from every IAM role output in this repository.
    assert template["Outputs"] == {
        "StateMachineName": {"Value": {"Fn::GetAtt": [machine_id, "Name"]}},
        "StateMachineArn": {"Value": {"Ref": machine_id}},
        "ValidatorFunctionName": {"Value": {"Ref": function_id}},
        "LogGroupName": {"Value": {"Ref": log_group_id}},
    }


def test_deployer_trusts_both_phase_deployment_workflows_and_nothing_else() -> None:
    condition = role_named(DEPLOYER_PATH, "sbsandbox-intern-edullm-infra-deployer")[
        "AssumeRolePolicyDocument"
    ]["Statement"][0]["Condition"]
    references = condition["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]

    assert isinstance(references, list)
    assert references == [
        PHASE1_DEPLOY_WORKFLOW_REF,
        PHASE2_DEPLOY_WORKFLOW_REF,
        PHASE3_DEPLOY_WORKFLOW_REF,
    ]
    assert not any("*" in reference for reference in references)
    assert (PROJECT_ROOT / PHASE2_DEPLOY_WORKFLOW).is_file()


def test_deployer_passrole_names_both_service_roles_and_never_a_prefix() -> None:
    # Passing a role is how a principal lends its own limits away, so the two ARNs are
    # written out. A sbsandbox-intern-edullm-* prefix would let this role pass any role
    # that later takes such a name, with permissions nobody weighed against a deployer.
    statements = policy_statements(
        role_named(DEPLOYER_PATH, "sbsandbox-intern-edullm-infra-deployer"),
        "deploy-phase2-admission-stacks",
    )
    pass_role = [
        statement for statement in statements if statement_actions(statement) == ["iam:PassRole"]
    ]

    assert len(pass_role) == 1
    assert pass_role[0]["Effect"] == "Allow"
    assert pass_role[0]["Resource"] == [STATES_ROLE_ARN, LAMBDA_ROLE_ARN]
    assert [arn.rsplit("/", 1)[1] for arn in resource_arns(pass_role[0]["Resource"])] == [
        STATES_ROLE_NAME,
        LAMBDA_ROLE_NAME,
    ]
    assert not [arn for arn in resource_arns(pass_role[0]["Resource"]) if "*" in arn]

    iam_actions = [action for action in all_actions(statements) if action.startswith("iam:")]
    assert iam_actions == ["iam:PassRole"]


#: The Phase 2 actions whose service authorization reference lists no resource type, so
#: that "*" is the narrowest grant that exists rather than a scope somebody skipped.
#: logs:DescribeLogGroups is the only one, and it is here because scoping it to the log
#: group ARN is what made the second deploy fail.
PHASE2_ACTIONS_WITH_NO_RESOURCE_TYPE = {"logs:DescribeLogGroups"}


def test_deployer_phase2_grants_are_scoped_past_the_shared_intern_prefix() -> None:
    statements = policy_statements(
        role_named(DEPLOYER_PATH, "sbsandbox-intern-edullm-infra-deployer"),
        "deploy-phase2-admission-stacks",
    )
    unscoped = [
        statement
        for statement in statements
        if "*" in resource_arns(statement["Resource"])
    ]
    scoped = [
        arn
        for statement in statements
        if statement not in unscoped
        for arn in resource_arns(statement["Resource"])
    ]

    # An unscoped statement is allowed only for an action that cannot be scoped, and only
    # for one this file names. Anything else appearing here is a scope given up on.
    assert {
        action for statement in unscoped for action in all_actions([statement])
    } == PHASE2_ACTIONS_WITH_NO_RESOURCE_TYPE
    assert scoped
    assert all(arn.startswith("arn:${AWS::Partition}:") for arn in scoped)
    assert not [arn for arn in scoped if "sbsandbox-intern-*" in arn]
    assert not [arn for arn in scoped if arn.endswith(":*") or arn == "*"]
    assert all("sbsandbox-intern-edullm-" in arn for arn in scoped)


def test_deployer_cannot_run_the_admission_machine_it_deploys() -> None:
    # A deploy credential that could also start an execution would be a way to admit a
    # run without passing an environment gate.
    statements = policy_statements(
        role_named(DEPLOYER_PATH, "sbsandbox-intern-edullm-infra-deployer"),
        "deploy-phase2-admission-stacks",
    )
    actions = all_actions(statements)

    assert not {"states:StartExecution", "states:StopExecution"} & set(actions)
    assert "lambda:InvokeFunction" not in actions
    assert not {"lambda:AddPermission", "lambda:RemovePermission"} & set(actions)
    assert "logs:PutLogEvents" not in actions


def test_deployer_object_writes_never_reach_the_lineage_bucket() -> None:
    statements = policy_statements(
        role_named(DEPLOYER_PATH, "sbsandbox-intern-edullm-infra-deployer"),
        "deploy-phase2-admission-stacks",
    )
    object_statements = [
        statement
        for statement in statements
        if any("/*" in arn for arn in resource_arns(statement["Resource"]))
    ]

    assert len(object_statements) == 1
    assert object_statements[0]["Resource"] == {
        "Fn::Sub": f"arn:${{AWS::Partition}}:s3:::{ARTIFACTS_BUCKET}/*"
    }
    assert not [
        arn
        for statement in statements
        for arn in resource_arns(statement["Resource"])
        if arn.startswith(f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}")
    ]


def test_deployer_keeps_every_phase1_grant_it_already_had() -> None:
    # Phase 2 widens this role; it must not narrow it. The Phase 1 policy is left byte for
    # byte alone in its own inline policy so that a Phase 2 edit cannot reach it.
    role = role_named(DEPLOYER_PATH, "sbsandbox-intern-edullm-infra-deployer")
    phase1 = policy_statements(role, "deploy-phase1-stacks")
    actions = all_actions(phase1)

    assert [policy["PolicyName"] for policy in role["Policies"]] == [
        "deploy-phase1-stacks",
        "deploy-phase2-admission-stacks",
        "deploy-phase3-batch-stacks",
    ]
    assert all(action.startswith(("cloudformation:", "ecr:")) for action in actions)
    assert {"cloudformation:CreateStack", "cloudformation:ValidateTemplate"} <= set(actions)
    assert {"ecr:CreateRepository", "ecr:PutLifecyclePolicy"} <= set(actions)


def test_every_phase2_role_carries_the_permissions_boundary_and_a_capped_session() -> None:
    roles = [
        role
        for path in (ADMISSION_ROLE_PATH, SERVICE_ROLES_PATH)
        for role in iam_roles(load_template(path))
    ]

    assert [role["RoleName"] for role in roles] == [
        ADMISSION_ROLE_NAME,
        STATES_ROLE_NAME,
        LAMBDA_ROLE_NAME,
    ]
    for role in roles:
        assert role["PermissionsBoundary"] == BOUNDARY
        assert role["MaxSessionDuration"] <= 3600
        assert role["Policies"]


def test_no_phase2_template_uses_a_managed_policy_it_could_never_amend() -> None:
    # InternSandboxBoundary denies iam:CreatePolicyVersion on every policy, so a customer
    # managed policy is write-once in this account: the first permission change would fail
    # the stack update. Inline role policies go through iam:PutRolePolicy, which the
    # boundary permits on sbsandbox-intern-* names.
    for path in PHASE2_TEMPLATE_PATHS:
        for resource in load_template(path).get("Resources", {}).values():
            assert resource.get("Type") != "AWS::IAM::ManagedPolicy", (
                f"managed policy cannot be updated under this boundary: "
                f"{path.relative_to(PROJECT_ROOT)}"
            )


def test_no_phase2_template_carries_an_aws_account_id_literal() -> None:
    # tests/test_evidence.py scans the whole tracked tree for twelve-digit runs and fails
    # the build on one, so a hardcoded account id would not reach main. This asserts the
    # positive form as well: every ARN written here reaches the account through the
    # pseudo-parameter, and reaches the partition through one too.
    written: list[str] = list(walk_strings(state_machine_definition()))
    for path in (*PHASE2_TEMPLATE_PATHS, DEPLOYER_PATH):
        source = path.read_text(encoding="utf-8")
        assert not ACCOUNT_LITERAL.search(source), (
            f"hardcoded AWS account id in {path.relative_to(PROJECT_ROOT)}"
        )
        written.extend(walk_strings(load_template(path)))

    arns = [value for value in written if value.startswith("arn:")]
    assert arns
    for arn in arns:
        segments = arn_segments(arn)
        assert segments[1] == "PARTITION", f"partition is not a pseudo-parameter: {arn}"
        # S3 and the Step Functions service integrations carry no account segment at all.
        assert segments[4] in ("", "ACCOUNT"), f"account is not a pseudo-parameter: {arn}"


def test_no_phase2_template_declares_a_cloudformation_parameter() -> None:
    # Names are hardcoded literals in this repository. A parameter would let the same
    # template deploy under a different name, which is how a stack ends up pointing at a
    # bucket or a role that no committed file describes.
    for path in PHASE2_TEMPLATE_PATHS:
        assert "Parameters" not in load_template(path)


def test_the_admission_stack_creates_no_iam_resource_of_its_own() -> None:
    # infra/iam/ stacks are applied from a laptop because the deployer has no
    # iam:CreateRole. An IAM resource appearing in a CI-deployed template would fail at
    # deploy time, and would mean the workflow needed an IAM capability it deliberately
    # does not pass.
    for path in (LINEAGE_PATH, ARTIFACTS_PATH, STATE_MACHINE_PATH):
        for resource in load_template(path)["Resources"].values():
            assert not str(resource["Type"]).startswith("AWS::IAM::"), (
                f"IAM resource in a CI-deployed template: {path.relative_to(PROJECT_ROOT)}"
            )
