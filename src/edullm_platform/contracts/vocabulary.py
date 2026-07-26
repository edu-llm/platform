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


JobTypeValue = Annotated[JobType, BeforeValidator(parse_str_enum(JobType))]
RetentionClassValue = Annotated[RetentionClass, BeforeValidator(parse_str_enum(RetentionClass))]
DataClassificationValue = Annotated[
    DataClassification, BeforeValidator(parse_str_enum(DataClassification))
]

__all__ = [
    "ApprovalClass",
    "DataClassification",
    "DataClassificationValue",
    "JobType",
    "JobTypeValue",
    "RetentionClass",
    "RetentionClassValue",
]
