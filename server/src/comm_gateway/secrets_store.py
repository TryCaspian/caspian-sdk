"""Resolve the credentials-encryption key.

Prefer an explicit ``COMM_CREDENTIALS_KEY``; otherwise fetch it from AWS SSM
Parameter Store (a ``SecureString``, KMS-encrypted) via the instance IAM role,
so the key never lives in ``.env`` - only the parameter *name* does.

Fail closed: if a parameter name is configured but the fetch fails, raise rather
than silently degrade to plaintext credential storage.
"""

import logging

log = logging.getLogger("comm.secrets")


def _fetch_ssm(param: str, region: str | None) -> str:
    import boto3  # lazy: only when SSM-backed

    ssm = boto3.client("ssm", region_name=region)
    return ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]


def resolve_credentials_key(settings) -> str:
    if settings.credentials_key:
        return settings.credentials_key
    param = getattr(settings, "credentials_key_ssm_param", "")
    if not param:
        return ""
    region = getattr(settings, "credentials_key_ssm_region", "") or None
    value = _fetch_ssm(param, region)
    log.info("loaded credentials encryption key from SSM parameter %s", param)
    return value


def resolve_stripe_keys(settings) -> None:
    """Load the Stripe live keys from SSM SecureString into settings when a
    parameter name is configured and the key isn't already set from the env.
    Live payment keys belong in SSM, not .env. Only the param *name* is in .env."""
    region = getattr(settings, "credentials_key_ssm_region", "") or None
    secret_param = getattr(settings, "stripe_secret_key_ssm_param", "")
    if not settings.stripe_secret_key and secret_param:
        settings.stripe_secret_key = _fetch_ssm(secret_param, region)
        log.info("loaded stripe secret key from SSM parameter %s", secret_param)
    hook_param = getattr(settings, "stripe_webhook_secret_ssm_param", "")
    if not settings.stripe_webhook_secret and hook_param:
        settings.stripe_webhook_secret = _fetch_ssm(hook_param, region)
        log.info("loaded stripe webhook secret from SSM parameter %s", hook_param)
