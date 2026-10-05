"""V1 service-principal authentication. Trusted service code owns environment and DB."""
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import json
import logging
import os
import re

PRODUCE_WORK = "PRODUCE_WORK"
VALIDATE_WORK = "VALIDATE_WORK"
PRODUCER_ID = "marketplace:producer:v1"
VALIDATOR_ID = "marketplace:validator:v1"
PRODUCER_SECRET_ENV = "MARKETPLACE_PRODUCER_SECRET"
VALIDATOR_SECRET_ENV = "MARKETPLACE_VALIDATOR_SECRET"
UNSET = object()
AUDIT = logging.getLogger("marketplace.authority")
OPERATIONS = {"authenticate", "create_delivery", "record_work_evidence", "validate_work",
    "local_validator", "save_evidence_and_result", "legacy_policy"}

class PrincipalAuthenticationError(ValueError):
    pass

@dataclass(frozen=True)
class Principal:
    principal_id: str
    capability: str


def audit_authority(principal, capability, operation, outcome):
    # Only trusted fixed identifiers/categories can enter the log.
    if not isinstance(principal, str) or principal not in {PRODUCER_ID, VALIDATOR_ID, "unauthenticated"}:
        principal = "unauthenticated"
    if not isinstance(capability, str) or capability not in {PRODUCE_WORK, VALIDATE_WORK}:
        capability = "UNKNOWN"
    if not isinstance(operation, str) or operation not in OPERATIONS:
        operation = "authenticate"
    if not isinstance(outcome, str) or outcome not in {"authenticated", "succeeded", "rejected"}:
        outcome = "rejected"
    AUDIT.info(json.dumps(dict(principal_id=principal, capability=capability, operation=operation,
        outcome=outcome, timestamp=datetime.now(timezone.utc).isoformat()), sort_keys=True))


def _well_formed(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value):
        return False
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        # This checks encoding/length, not entropy; generate with secrets.token_urlsafe(32).
        return len(decoded) >= 32 and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == value
    except (ValueError, UnicodeError):
        return False


def _configured_secrets():
    producer = os.environ.get(PRODUCER_SECRET_ENV)
    validator = os.environ.get(VALIDATOR_SECRET_ENV)
    if not _well_formed(producer) or not _well_formed(validator):
        raise PrincipalAuthenticationError("Principal authentication unavailable")
    if hmac.compare_digest(producer.encode(), validator.encode()):
        raise PrincipalAuthenticationError("Principal authentication unavailable")
    return producer, validator


def authenticate(credential, required_capability, *, operation="authenticate"):
    """No caller principal objects/IDs are accepted; no secret-bearing handle is returned."""
    principal_id = "unauthenticated"
    try:
        if (not isinstance(required_capability, str) or required_capability not in {PRODUCE_WORK, VALIDATE_WORK}
            or not isinstance(operation, str) or operation not in OPERATIONS):
            raise PrincipalAuthenticationError("Principal authentication rejected")
        producer, validator = _configured_secrets()
        if not _well_formed(credential):
            raise PrincipalAuthenticationError("Principal authentication rejected")
        # Compare both independently, without short-circuiting cross-capability matches.
        producer_match = hmac.compare_digest(credential.encode(), producer.encode())
        validator_match = hmac.compare_digest(credential.encode(), validator.encode())
        if producer_match:
            principal_id = PRODUCER_ID
        elif validator_match:
            principal_id = VALIDATOR_ID
        if not ((required_capability == PRODUCE_WORK and producer_match) or
                (required_capability == VALIDATE_WORK and validator_match)):
            raise PrincipalAuthenticationError("Principal authentication rejected")
    except PrincipalAuthenticationError:
        audit_authority(principal_id, required_capability, operation, "rejected")
        raise PrincipalAuthenticationError("Principal authentication rejected or unavailable") from None
    audit_authority(principal_id, required_capability, operation, "authenticated")
    return Principal(principal_id, required_capability)


def reject_credential_material(value):
    """Prevent configured credential strings from being persisted/returned as work data."""
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=True)
    for name in (PRODUCER_SECRET_ENV, VALIDATOR_SECRET_ENV):
        secret = os.environ.get(name)
        if secret and secret in serialized:
            raise PrincipalAuthenticationError("Credential material is forbidden in authority data")


def compatibility_environment(environment, *, operation):
    if environment not in {"TEST", "SANDBOX"}:
        audit_authority("unauthenticated", VALIDATE_WORK, operation, "rejected")
        raise PrincipalAuthenticationError("Explicit TEST/SANDBOX compatibility environment required")
    return environment
