def require_checkpoint_for_retries(
    *,
    maximum_attempts: int,
    checkpoint: object | None,
) -> None:
    if maximum_attempts > 1 and checkpoint is None:
        raise ValueError("retryable workloads require a checkpoint contract")
