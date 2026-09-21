"""Phase 3-4 write API endpoints: evaluation scoring, objection filing,
contract management (milestones, variations, guarantees), acceptance/payment
certification, and performance rating."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST

from procurement.models import (
    Award, Bid, Contract, Criterion, Score, Tender,
)
from procurement.models_party import User


def _json(request) -> dict:
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception:
        return {}


def _u(request) -> User | None:
    return request.user if request.user.is_authenticated else None


# ============================================================ Phase 3: Evaluation

@require_POST
def api_form_committee(request, ocid: str):
    """Form the evaluation committee. Each member must have a conflict-of-interest
    declaration."""
    from workflow.services_phases import form_evaluation_committee
    u = _u(request)
    if not u or u.role not in ("DG", "PDE", "ADMIN"):
        return HttpResponseForbidden("Only DG/PDE/Admin may form the committee.")
    body = _json(request)
    tender = Tender.objects.get(ocid=ocid)
    members = []
    for m in body.get("members", []):
        user = User.objects.get(username=m["username"])
        members.append({
            "user": user,
            "role": m.get("role", "MEMBER"),
            "declaration_sha256": m.get("declaration_sha256", ""),
        })
    committee = form_evaluation_committee(tender, members, actor=u.username)
    return JsonResponse({
        "ok": True,
        "members": [{"user": ec.user.username, "role": ec.role} for ec in committee],
    })


@require_POST
def api_score_bid(request, ocid: str, bid_id: int):
    """Enter a score for one criterion on one bid."""
    from workflow.services_phases import enter_score
    u = _u(request)
    if not u:
        return HttpResponseForbidden()
    body = _json(request)
    bid = Bid.objects.get(pk=bid_id, tender__ocid=ocid)
    criterion = Criterion.objects.get(code=body["criterion"], tender__ocid=ocid)
    score = enter_score(
        bid, criterion, u,
        raw=Decimal(str(body["raw"])),
        narrative=body["narrative"],
    )
    return JsonResponse({
        "ok": True,
        "score_id": score.pk,
        "criterion": criterion.code,
        "raw": str(score.raw),
        "weighted": str(score.weighted),
    })


@require_POST
def api_record_dissent(request, ocid: str, score_id: int):
    """Record a dissent on a specific score."""
    from workflow.services_phases import record_dissent
    u = _u(request)
    if not u:
        return HttpResponseForbidden()
    body = _json(request)
    score = Score.objects.get(pk=score_id, bid__tender__ocid=ocid)
    dissent = record_dissent(score, u, body["note"])
    return JsonResponse({"ok": True, "dissent_id": dissent.pk})


@require_POST
def api_publish_report(request, ocid: str):
    """Publish the evaluation report."""
    from workflow.services_phases import publish_evaluation_report
    u = _u(request)
    if not u or u.role not in ("DG", "PDE", "EVALUATOR", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    tender = Tender.objects.get(ocid=ocid)
    report = publish_evaluation_report(
        tender, body["summary"], body.get("methodology", ""), actor=u.username,
    )
    return JsonResponse({"ok": True, "sha256": report.report_sha256})


@require_POST
def api_route_approval(request, ocid: str):
    """Determine and record the required approving authority."""
    from workflow.services_phases import route_approval, approve_routing
    u = _u(request)
    if not u or u.role not in ("DG", "PDE", "APPROVER", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    tender = Tender.objects.get(ocid=ocid)

    if body.get("approve"):
        routing = approve_routing(tender, u, body.get("ref", ""))
    else:
        routing = route_approval(tender, actor=u.username)

    return JsonResponse({
        "ok": True,
        "required_body": routing.required_body,
        "approved_by_body": routing.approved_by_body or None,
        "is_correct": routing.is_correct,
    })


# ============================================================ Phase 3: Objections

@require_POST
def api_file_objection(request, ocid: str, award_id: int):
    """File an objection against an award. Triggers the Georgia 10-day freeze."""
    from workflow.services import file_objection
    body = _json(request)
    award = Award.objects.get(pk=award_id, tender__ocid=ocid)
    u = _u(request)

    panelists = body.get("panelists", [])
    if not panelists:
        # Default: 2 GOV + 2 CSO
        panelists = [
            {"name": "Government nominee 1", "nominator": "GOV"},
            {"name": "Government nominee 2", "nominator": "GOV"},
            {"name": "CSO nominee 1", "nominator": "CSO"},
            {"name": "CSO nominee 2", "nominator": "CSO"},
        ]

    objection = file_objection(
        award, body["ground"], u, body.get("label", "anonymous"),
        panelists=panelists, evidence=body.get("evidence"),
    )
    return JsonResponse({
        "ok": True,
        "objection_id": objection.pk,
        "frozen_until": objection.frozen_until.isoformat(),
    })


@require_POST
def api_decide_objection(request, objection_id: int):
    """Panel decides an objection."""
    from workflow.services import decide_objection
    from procurement.models import Objection
    u = _u(request)
    if not u or u.role not in ("APPEALS", "DG", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    objection = Objection.objects.get(pk=objection_id)
    decide_objection(objection, body["outcome"], body["decision"])
    return JsonResponse({"ok": True, "outcome": objection.outcome})


# ============================================================ Phase 3: Debriefs

@require_POST
def api_request_debrief(request, ocid: str, award_id: int):
    """A losing bidder requests a debrief."""
    from workflow.services_phases import request_debrief
    from procurement.models_party import Party
    body = _json(request)
    award = Award.objects.get(pk=award_id, tender__ocid=ocid)
    supplier = Party.objects.get(pk=body["supplier_id"])
    debrief = request_debrief(award, supplier, body.get("questions", ""))
    return JsonResponse({
        "ok": True,
        "debrief_id": debrief.pk,
        "due_by": debrief.due_by.isoformat(),
    })


@require_POST
def api_respond_debrief(request, debrief_id: int):
    """Provide the written debrief response."""
    from workflow.services_phases import respond_debrief
    from workflow.models_phases import DebriefRequest
    u = _u(request)
    if not u or u.role not in ("PDE", "DG", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    debrief = DebriefRequest.objects.get(pk=debrief_id)
    respond_debrief(debrief, body["response"], u)
    return JsonResponse({"ok": True, "status": debrief.status})


# ============================================================ Phase 4: Contract management

@require_POST
def api_create_milestone(request, reference: str):
    """Create a contract milestone."""
    from workflow.services_phases import create_milestone
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    ms = create_milestone(
        contract, body["seq"], body["title"],
        date.fromisoformat(body["planned_date"]),
        Decimal(str(body.get("value", 0))),
        body.get("description", ""),
    )
    return JsonResponse({"ok": True, "milestone_id": ms.pk})


@require_POST
def api_complete_milestone(request, milestone_id: int):
    """Mark a milestone as delivered and inspected."""
    from workflow.services_phases import complete_milestone
    from workflow.models_phases import ContractMilestone
    u = _u(request)
    if not u:
        return HttpResponseForbidden()
    body = _json(request)
    ms = ContractMilestone.objects.get(pk=milestone_id)
    complete_milestone(ms, u, body.get("note", ""), body.get("accepted", True))
    return JsonResponse({"ok": True, "status": ms.status})


@require_POST
def api_propose_variation(request, reference: str):
    """Propose a contract variation."""
    from workflow.services_phases import propose_variation
    u = _u(request)
    if not u:
        return HttpResponseForbidden()
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    variation = propose_variation(
        contract, body["title"], body["reason"],
        Decimal(str(body["amount_change"])),
        int(body.get("time_change_days", 0)), u,
    )
    return JsonResponse({
        "ok": True,
        "variation_id": variation.pk,
        "alarm": variation.alarm_triggered,
        "pct": str(variation.pct_of_contract),
    })


@require_POST
def api_approve_variation(request, variation_id: int):
    """Approve a contract variation."""
    from workflow.services_phases import approve_variation
    from workflow.models_phases import ContractVariation
    u = _u(request)
    if not u or u.role not in ("DG", "APPROVER", "ADMIN"):
        return HttpResponseForbidden()
    variation = ContractVariation.objects.get(pk=variation_id)
    approve_variation(variation, u)
    return JsonResponse({"ok": True, "status": variation.status})


@require_POST
def api_add_guarantee(request, reference: str):
    """Register a contract guarantee."""
    from workflow.services_phases import add_guarantee
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    g = add_guarantee(
        contract, body["kind"], body["issuer"], body["reference"],
        Decimal(str(body["amount"])),
        date.fromisoformat(body["issued_at"]),
        date.fromisoformat(body["expires_at"]),
        body.get("doc_sha256", ""),
    )
    return JsonResponse({"ok": True, "guarantee_id": g.pk})


@require_POST
def api_certify_acceptance(request, reference: str):
    """Issue an acceptance certificate (CRAC)."""
    from workflow.services import certify_acceptance
    u = _u(request)
    if not u or u.role not in ("PDE", "DG", "TREASURY", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    cert = certify_acceptance(
        contract, u, Decimal(str(body["value"])),
        body["ref"], body.get("report_sha256", ""),
    )
    return JsonResponse({"ok": True, "ref": cert.ref})


@require_POST
def api_certify_payment(request, reference: str):
    """Certify payment. The payment lock: requires acceptance certificate."""
    from workflow.services import certify_payment
    from procurement.models import AcceptanceCertificate
    u = _u(request)
    if not u or u.role not in ("TREASURY", "DG", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    acceptance = AcceptanceCertificate.objects.get(pk=body["acceptance_id"])
    cert = certify_payment(
        contract, acceptance, u, Decimal(str(body["amount"])),
        body["ref"], body.get("treasury_ref", ""),
    )
    return JsonResponse({"ok": True, "reference": cert.reference})


@require_POST
def api_rate_performance(request, reference: str):
    """Compute and publish a supplier performance rating."""
    from workflow.services_phases import rate_supplier_performance
    u = _u(request)
    if not u or u.role not in ("DG", "PDE", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    rating = rate_supplier_performance(contract, u, body.get("narrative", ""))
    return JsonResponse({
        "ok": True,
        "grade": rating.grade,
        "composite": str(rating.composite),
    })


@require_POST
def api_close_contract(request, reference: str):
    """Close out a contract after defects liability period."""
    from workflow.services_phases import close_contract
    u = _u(request)
    if not u or u.role not in ("DG", "PDE", "ADMIN"):
        return HttpResponseForbidden()
    body = _json(request)
    contract = Contract.objects.get(reference=reference)
    close_contract(contract, u, body.get("note", ""))
    return JsonResponse({"ok": True, "status": contract.status})
