from enum import StrEnum
from typing import Annotated

from pydantic import BeforeValidator

from .base import parse_str_enum
from .policy import ApprovalClass


class JobType(StrEnum):
    CORPUS_PREPROCESSING = "corpus_preprocessing"
    TOKENIZER_TRAINING = "tokenizer_training"
    MODEL_PRETRAINING = "model_pretraining"
    MODEL_FINE_TUNING = "model_fine_tuning"
    MODEL_EVALUATION = "model_evaluation"
    BATCH_INFERENCE = "batch_inference"


class RetentionClass(StrEnum):
    TRANSIENT = "transient"
    STANDARD = "standard"
    LONG_LIVED = "long_lived"
    PERMANENT = "permanent"


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class InputRole(StrEnum):
    """What an input is to the run that names it.

    The role is what the platform checks and the address is what it resolves, so a tokenizer
    named as a corpus is refused because a tokenizer is not one rather than because a list of
    families left it out. See system-overview.md, "Where data lives".

    Closed on purpose. A third role is a deliberate addition here and to the family sets in
    contracts/dataset_registry.py, and the submission form's repeatable input flag means adding
    one is not a fourth decision anywhere else.
    """

    CORPUS = "corpus"
    WEIGHTS = "weights"


JobTypeValue = Annotated[JobType, BeforeValidator(parse_str_enum(JobType))]
RetentionClassValue = Annotated[RetentionClass, BeforeValidator(parse_str_enum(RetentionClass))]
DataClassificationValue = Annotated[
    DataClassification, BeforeValidator(parse_str_enum(DataClassification))
]
InputRoleValue = Annotated[InputRole, BeforeValidator(parse_str_enum(InputRole))]

__all__ = [
    "ApprovalClass",
    "DataClassification",
    "DataClassificationValue",
    "InputRole",
    "InputRoleValue",
    "JobType",
    "JobTypeValue",
    "RetentionClass",
    "RetentionClassValue",
]
