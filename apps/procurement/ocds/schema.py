"""OCDS release contract used by CI and the API.

A full JSON-Schema copy of OCDS 1.1 is vendored in production (`ocds-release.json`
fetched from standard.open-contracting.org during deploy, checksummed). For the
repo we carry the normative subset that catches the failure modes that actually
happen: missing `ocid`, wrong `tag` enum, missing release date, malformed
amounts, missing party ids. The API validates outgoing releases with this and the
build fails on any violation — a transparency claim must be executable.
"""
from __future__ import annotations

from typing import Any

OCID_PATTERN_HINT = "TAR-<AGENCY>-<YYYY>-<NNNN>"

AMOUNT_SCHEMA = {
    "type": "object",
    "required": ["currency"],
    "properties": {"currency": {"type": "string", "minLength": 3, "maxLength": 3}, "amount": {"type": "number"}},
}

OCDS_RELEASE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Taraba OCDS 1.1 compile release (normative subset)",
    "type": "object",
    "required": ["ocid", "releaseID", "date", "language", "tag", "initiationType", "parties", "buyer", "tender"],
    "properties": {
        "ocid": {"type": "string", "minLength": 5},
        "releaseID": {"type": "string", "minLength": 5},
        "date": {"type": "string", "minLength": 10},
        "language": {"type": "string", "enum": ["en", "hau"]},
        "tag": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": [
            "planning", "tender", "tenderAmendment", "tenderUpdate", "tenderCancellation", "award",
            "awardAmendment", "awardUpdate", "awardCancellation", "contract", "contractAmendment",
            "contractUpdate", "contractTermination", "implementation", "milestone", "submission", "qualification",
        ]}},
        "initiationType": {"type": "string", "enum": ["tender"]},
        "parties": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["id", "name", "roles"]}},
        "buyer": {"type": "object", "required": ["id", "name"]},
        "planning": {"type": "object", "properties": {"budgetAmount": AMOUNT_SCHEMA}},
        "tender": {
            "type": "object",
            "required": ["id", "status", "items", "tenderPeriod", "submissionMethod"],
            "properties": {
                "id": {"type": "string", "minLength": 5},
                "status": {"type": "string", "enum": ["unpublished", "active", "complete", "cancelled"]},
                "value": AMOUNT_SCHEMA,
                "items": {"type": "array", "minItems": 1},
                "tenderPeriod": {"type": "object", "required": ["startDate", "endDate"]},
                "documents": {"type": "array"},
                "notices": {"type": "array", "minItems": 1},
            },
        },
        "submissions": {"type": "array", "items": {"type": "object", "required": ["submissionID", "date", "tender_id"]}},
        "awards": {"type": "array", "items": {"type": "object", "required": ["id", "date", "status", "suppliers"], "properties": {"value": AMOUNT_SCHEMA}}},
        "contracts": {"type": "array", "items": {"type": "object", "required": ["contractID", "awardID", "dateStarted", "status", "period"]}},
    },
}

# Platform-specific rules the JSON schema cannot express.
def semantic_errors(release: dict) -> list[str]:
    """Rules whose violation makes the data a lie rather than merely malformed."""
    errs: list[str] = []
    tender = release.get("tender") or {}
    ext = release.get("taraba") or {}

    if not tender.get("value"):
        errs.append("tender.value missing: a published tender must disclose the state's estimate (Axiom 1).")
    if not (tender.get("tenderDetails") or {}).get("estimateDisclosed"):
        errs.append("tender.tenderDetails.estimateDisclosed must be true for any published release.")
    period = tender.get("tenderPeriod") or {}
    if period.get("startDate") and period.get("endDate") and period["endDate"] <= period["startDate"]:
        errs.append("tender.tenderPeriod.endDate must be after startDate.")
    if not tender.get("notices"):
        errs.append("tender.notices must contain the public notice URL.")
    for award in release.get("awards") or []:
        if not award.get("value"):
            errs.append(f"award {award.get('id')} has no value: awards are published with their naira amount.")
        if not (award.get("description") or "").strip():
            errs.append(f"award {award.get('id')} has no published reasons.")
    for sub in release.get("submissions") or []:
        if not (sub.get("additionalData") or {}).get("commitmentHash"):
            errs.append(f"submission {sub.get('submissionID')} missing commitment hash.")
    if ext and not ext.get("immutableFrom"):
        errs.append("taraba.immutableFrom missing: only immutable (published) data may be released.")
    return errs


def validate_release(release: dict) -> None:
    """Raise ``OCDSValidationError`` describing every problem found."""
    errs = _structural_errors(release, OCDS_RELEASE_SCHEMA, "$")
    errs += semantic_errors(release)
    if errs:
        raise OCDSValidationError(errs)


def _structural_errors(instance: Any, schema: dict, path: str) -> list[str]:
    errs: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            return [f"{path}: expected object"]
        for key in schema.get("required", []):
            if key not in instance or instance[key] in (None, "", [], {}):
                errs.append(f"{path}.{key}: required and must be non-empty")
        for key, sub in (schema.get("properties") or {}).items():
            if key in instance and instance[key] is not None:
                errs += _structural_errors(instance[key], sub, f"{path}.{key}")
    elif t == "array":
        if not isinstance(instance, list):
            return [f"{path}: expected array"]
        if len(instance) < schema.get("minItems", 0):
            errs.append(f"{path}: needs at least {schema['minItems']} items")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errs += _structural_errors(item, item_schema, f"{path}[{i}]")
    elif t == "string":
        if not isinstance(instance, str):
            return [f"{path}: expected string"]
        if len(instance) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "enum" in schema and instance not in schema["enum"]:
            errs.append(f"{path}: {instance!r} not in allowed {schema['enum']}")
    elif t == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            errs.append(f"{path}: expected number")
    return errs


class OCDSValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("OCDS validation failed:\n  - " + "\n  - ".join(errors))
