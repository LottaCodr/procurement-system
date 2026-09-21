"""OCDS 1.1 release serialisation.

The database *is* the public dataset. OCDS output is generated from the same
model instances that the workflow writes, never from a hand-maintained export
table — because that is exactly how you end up advertising an "Open data (OCDS
JSON)" link that 404s, as both Kano's successor and Taraba's current portal do.

Every release is validated against `schema.OCDS_RELEASE_SCHEMA` before it leaves
the API (`validate=True` on the view). A malformed release is a build failure,
not a runtime surprise.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.utils import timezone as dj_timezone


def _indicator_version() -> str:
    from procurement.risk import INDICATOR_VERSION

    return INDICATOR_VERSION


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
        return dt.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _amount(value) -> dict | None:
    if value in (None, ""):
        return None
    d = Decimal(str(value))
    return {"currency": "NGN", "amount": float(round(d, 2))}


def _party(p, *, details: bool = False) -> dict:
    out = {
        "id": p.rc_number or f"TAR-P-{p.pk}",
        "name": p.legal_name,
        "roles": ["supplier"],
    }
    if details:
        out["addressDetails"] = {"countryName": "Nigeria", "region": p.state, "city": p.lga or p.state}
        out["contactPoint"] = {"name": p.legal_name, "email": p.email}
        out["details"] = {
            "registrationNumber": p.rc_number,
            "taxIdentifiers": [{"scheme": "NG-TIN", "id": p.tin}] if p.tin else [],
            "supplierScope": p.scope,
            "supplierCategory": p.category,
            "beneficialOwnershipDeclared": p.bo_declared,
            "debarred": p.is_debarred,
        }
    return out


def _buyer(agency) -> dict:
    return {
        "id": agency.code,
        "name": agency.name,
        "roles": ["buyer"],
        "addressDetails": {"countryName": "Nigeria", "region": "Taraba State", "city": agency.lga or "Jalingo"},
        "contactPoint": {"name": agency.name, "email": agency.contact_email or "bpp@tr.gov.ng"},
    }


def planning(tender) -> dict:
    bl = tender.budget_line
    return {
        "budgetAmount": _amount(tender.est_value),
        "rationale": tender.description or "",
        "relatedPlans": (
            [{"id": f"{bl.fy}-{bl.project_code}", "budgetAmount": _amount(bl.amount), "description": bl.description}]
            if bl
            else []
        ),
    }


def tender_stage(tender) -> dict:
    items = [
        {
            "id": f"lot-{lot.seq}",
            "description": lot.title,
            "unit": lot.unit or None,
            "quantity": float(lot.quantity) if lot.quantity is not None else None,
            "additionalClassifications": [],
        }
        for lot in tender.lots.all()
    ] or [
        {
            "id": "main",
            "description": tender.title,
            "classification": {"scheme": "UNSPSC", "id": "", "description": ""},
            "unit": None,
            "quantity": 1,
        }
    ]
    docs = [
        {
            "documentType": d.kind.lower(),
            "title": d.title,
            "url": d.download_path,
            "format": "https://www.iana.org/assignments/media-types/application/pdf",
            "hash": {"algorithm": "sha256", "value": d.sha256} if d.sha256 else None,
            "relatedLots": [],
        }
        for d in tender.documents.filter(published=True)
    ]
    notices = [
        {
            "url": f"https://procurement.taraba.gov.ng/tenders/{tender.ocid}",
            "publicationDate": _iso(tender.published_at),
            "title": f"{tender.get_method_display()} — {tender.title}",
        }
    ]
    return {
        "id": tender.ocid,
        "items": items,
        "status": "cancelled" if tender.status == tender.Status.TERMINATED else ("active" if tender.status in ("PUBLISHED", "CLARIFYING") else "complete"),
        "value": _amount(tender.est_value),
        "tenderNumber": tender.reference or tender.ocid,
        "mainProcurementCategory": {"A": "works", "B": "goods", "C": "services", "D": "services"}.get(
            getattr(tender, "category", "") or "B", "goods"
        ),
        "method": tender.method.lower(),
        "procuringEntity": _buyer(tender.agency),
        "tenderPeriod": {"startDate": _iso(tender.published_at), "endDate": _iso(tender.submission_close_at)},
        "qualificationPeriod": {"startDate": _iso(tender.published_at), "endDate": _iso(tender.qa_close_at)} if tender.qa_close_at else None,
        "awardPeriod": {"startDate": _iso(tender.published_at), "endDate": None},
        "documents": docs,
        "notices": notices,
        "submissionMethod": ["electronicSubmission"],
        "tenderDetails": {
            "bidSecurity": _amount(tender.bid_security_amount) if tender.bid_security_amount else None,
            "minAdvertDays": tender.rule.min_advert_days if tender.rule else None,
            "estimateDisclosed": tender.est_value is not None,
            "evaluationCriteriaLocked": tender.criteria.filter(locked=True).exists(),
            "publicOpening": {
                "date": _iso(tender.opening_at),
                "venue": tender.opening_venue,
                "committee": [
                    {"name": c.user.get_full_name() or c.user.username, "role": c.role}
                    for c in tender.committee.select_related("user")
                ],
            },
            "localContentPct": tender.reserved_for_local_pct,
            "smeSetAsidePct": tender.sme_set_aside_pct,
        },
    }


def submissions(tender) -> list[dict]:
    """Value is only revealed post-opening, then published permanently."""
    reveal = tender.status in ("OPENED", "EVALUATING", "AWARDED", "CONTRACTED", "FROZEN")
    out = []
    for b in tender.bids.select_related("supplier"):
        row = {
            "submissionID": b.receipt_ref,
            "date": _iso(b.submitted_at),
            "tender_id": tender.ocid,
            "value": _amount(b.amount) if reveal else None,
            "status": {
                "RECEIVED": "unsuccessful" if not reveal else "valid",
                "UNSEALED": "valid",
                "RESPONSIVE": "valid",
                "EVALUATED": "valid",
                "RECOMMENDED": "valid",
                "REJECTED": "invalid",
                "WITHDRAWN": "withdrawn",
            }.get(b.status, "valid"),
            # The commitment hash is public from the moment of submission: it is
            # what lets a bidder prove *what* they submitted and when.
            "additionalData": {"commitmentHash": b.commitment_hash, "late": b.late},
        }
        out.append(row)
    return out


def awards(tender) -> list[dict]:
    out = []
    for a in tender.awards.select_related("bid", "bid__supplier"):
        out.append(
            {
                "id": f"award-{a.pk}",
                "title": f"Award to {a.bid.supplier.legal_name}",
                "description": a.reason,
                "date": _iso(a.published_at or a.updated_at),
                "status": "active" if a.status in ("PUBLISHED", "CONTRACTED", "APPROVED") else "pending",
                "value": _amount(a.amount),
                "suppliers": [_party(a.bid.supplier)],
                "relatedLots": [f"lot-{a.lot.seq}"] if a.lot_id else [],
                "items": [],
                "awardDetails": {
                    "approvedBy": a.approved_by.get_full_name() if a.approved_by else None,
                    "approvalBody": a.tender.approval_body,
                    "objectionUntil": _iso(a.objection_until),
                    "nonLowestReason": a.non_lowest_reason or None,
                    "estimateToAwardRatio": (
                        float(round(a.amount / tender.est_value, 4)) if tender.est_value and a.amount else None
                    ),
                },
            }
        )
    return out


def contract_stage(tender) -> list[dict]:
    out = []
    for a in tender.awards.select_related("contract"):
        c = getattr(a, "contract", None)
        if not c:
            continue
        implementation = [
            {
                "title": e.get_kind_display(),
                "date": _iso(e.occurred_at),
                "value": _amount(e.amount),
                "description": e.note,
            }
            for e in c.events.filter(published=True)
        ]
        out.append(
            {
                "contractID": c.reference,
                "awardID": f"award-{a.pk}",
                "dateStarted": _iso(c.signed_at),
                "status": c.status.lower(),
                "value": _amount(c.value),
                "period": {"startDate": _iso(c.signed_at), "endDate": _iso(c.signed_at + timedelta(days=c.duration_days))},
                "relatedLots": [],
                "implementation": implementation,
                "contractDetails": {
                    "variationPct": float(c.variation_pct),
                    "advancePct": float(c.advance_pct),
                    "performanceGuaranteePct": float(c.perf_guarantee_pct),
                    "acceptances": [
                        {"ref": x.ref, "value": float(x.value), "inspectedAt": _iso(x.inspected_at), "by": x.certified_by.get_full_name()}
                        for x in c.acceptances.select_related("certified_by")
                    ],
                    # The payment lock, made visible: which certifications exist.
                    "paymentCertifications": [
                        {"ref": p.reference, "amount": float(p.amount), "at": _iso(p.certified_at), "treasuryRef": p.treasury_ref}
                        for p in c.certifications.select_related("certified_by")
                    ],
                },
            }
        )
    return out


def tender_release(tender, *, full: bool = True) -> dict:
    """The one release a reader needs for a process: planning → tender → submissions
    → awards → contracts, in a single document (OCDS "compile" release)."""
    return releases_for(tender, release_type="awarded" if full else "tender")[0]


def releases_for(tender, *, release_type: str = "awarded") -> list[dict]:
    """Full compile release for one tender (planning..contract)."""
    now = _iso(dj_timezone.now())
    parties = [_buyer(tender.agency)]
    for b in tender.bids.select_related("supplier"):
        p = _party(b.supplier, details=True)
        if not any(q["id"] == p["id"] for q in parties):
            parties.append(p)
    for a in tender.awards.select_related("bid__supplier"):
        p = _party(a.bid.supplier)
        if not any(q["id"] == p["id"] for q in parties):
            parties.append(p)

    release = {
        "ocid": tender.ocid,
        "releaseID": f"{tender.ocid}-R-{now}",
        "date": now,
        "language": "en",
        "tag": ["tender", "tenderUpdate", "award", "contract"] if release_type == "awarded" else ["tender"],
        "initiationType": "tender",
        "parties": parties,
        "buyer": {"id": tender.agency.code, "name": tender.agency.name, "roles": ["buyer"]},
        "planning": planning(tender),
        "tender": tender_stage(tender),
        "submissions": submissions(tender),
        "awards": awards(tender),
        "contracts": contract_stage(tender),
        # Extensions must never fork the core (OCDS extension mechanism).
        "taraba": {
            "lga": tender.agency.lga,
            "agencyCode": tender.agency.code,
            "method": tender.method,
            "approvalBody": tender.approval_body,
            "noObjectionRef": tender.no_objection_ref,
            "status": tender.status,
            "immutableFrom": _iso(tender.immutable_from),
            "commitmentRoot": tender.commitment_root,
            "redFlags": tender.red_flag_codes,
            "indicatorVersion": _indicator_version(),
            "localContentPct": tender.reserved_for_local_pct,
            "smeSetAsidePct": tender.sme_set_aside_pct,
            "objectionsOpen": tender.objections.filter(outcome="PENDING").count(),
        },
    }
    return [release]


def continuous_releases(tender, after=None) -> list[dict]:
    """ProZorro's pattern: publish a release whenever the process changes, not a
    nightly dump. Here we emit the compile release; the ledger gives callers the
    per-event stream via /api/v1/releases.
    """
    return releases_for(tender)
