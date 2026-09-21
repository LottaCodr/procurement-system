"""Phase 2-4 web views: supplier profiles, registration, bid submission,
evaluation workspace, objection filing, contract management, and payment
certification. All read-only views are unauthenticated per the design's
Axiom 3 (trust must not depend on trusting the operator)."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from procurement.models import (
    Award, Bid, Contract, ContractEvent, Objection, Tender,
)
from procurement.models_party import Party


# ============================================================ Phase 2: Supplier

@require_GET
def supplier_detail(request, pk: int):
    """Public supplier profile: verification status, beneficial ownership,
    awards won, performance ratings. This is what makes a good contractor
    in Wukari known without meeting an official."""
    supplier = get_object_or_404(
        Party.objects.prefetch_related(
            "verifications", "owners", "declarations",
            "bids__tender", "ratings",
        ),
        pk=pk,
    )
    awards = Award.objects.filter(
        bid__supplier=supplier,
        status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED],
    ).select_related("tender__agency")

    bids = supplier.bids.select_related("tender__agency").order_by("-submitted_at")

    # Performance ratings
    from workflow.models_phases import SupplierPerformanceRating
    ratings = SupplierPerformanceRating.objects.filter(
        supplier=supplier, published=True
    ).select_related("contract__award__tender")

    avg_composite = ratings.aggregate(avg=__import__("django.db.models", fromlist=["Avg"]).Avg("composite"))["avg"]

    total_awarded = awards.aggregate(v=Sum("amount"))["v"] or Decimal("0")
    return render(request, "supplier_detail.html", {
        # Canonical names: `p` for a party, matching the register pages, so the
        # same supplier object is not `s` on one page and `p` on the next.
        "p": supplier,
        "s": supplier,
        "awards": awards,
        "total_awarded": total_awarded,
        "bids": bids,
        "ratings": ratings,
        "performance_records": ratings,
        "avg_composite": avg_composite or 0,
        "verifications": {
            v.kind: v for v in supplier.verifications.all()
        },
        "is_debarred": supplier.is_debarred,
    })


REGISTRATION_STEPS = [
    (1, "Company identity", "Legal name, RC number, TIN, incorporation year"),
    (2, "Contact details", "Address, phone, email, contact person"),
    (3, "Certificates", "CAC, TIN, PenCom documents with expiry dates"),
    (4, "Capability", "Similar contracts, staff count, turnover, category"),
    (5, "Beneficial ownership", "Owners with ≥5% share, PEP declarations"),
    (6, "Review & submit", "Check everything, accept terms, submit"),
]

# Which draft fields each form step may write. Anything not listed here is
# dropped by the view before it ever reaches the service — the form can only
# say what the step is about.
STEP_FIELDS = {
    1: ["company_name", "rc_number", "tin", "pencom", "year_incorporated", "website"],
    2: ["full_name", "email", "phone", "address", "state", "lga"],
    4: ["category", "scope", "similar_contracts_n", "annual_turnover", "employees_count"],
    5: ["related_party_declaration", "pep_flag"],
    6: ["accept_terms"],
}


@require_GET
def register_start(request):
    """Supplier registration: landing page and requirements."""
    return render(request, "register_start.html", {"steps": REGISTRATION_STEPS})


@require_POST
def register_create(request):
    """'Begin registration' pressed: create an empty draft and hand the vendor
    their private link. No account, no fee, nothing published."""
    from workflow.services import start_registration

    draft = start_registration()
    return redirect(f"/tenders/register/form/{draft.draft_token}/")


def _registration_context(draft, step: int, errors: dict | None = None, values: dict | None = None):
    from workflow.models import DraftDocument

    return {
        "draft": draft,
        "token": draft.draft_token,
        "step": step,
        "steps": REGISTRATION_STEPS,
        "steps_total": len(REGISTRATION_STEPS),
        "documents": draft.documents.all(),
        "owners": draft.owners.all(),
        "ready": draft.ready_to_submit(),
        "required_documents": sorted(draft.required_document_kinds()),
        "doc_kinds": DraftDocument.Kind.choices,
        "categories": Party.Category.choices,
        "scopes": Party.Scope.choices,
        "errors": errors or {},
        "values": values or {},
        "locked": draft.status != "DRAFT",
    }


@require_http_methods(["GET", "POST"])
def register_form(request, token: str = ""):
    """Multi-step registration form. The token is the draft's identity: an
    unguessable 40-character link, so no account is needed and nothing is
    published until step 6 is submitted."""
    from workflow.models import SupplierRegistrationDraft

    if request.method == "POST":
        if not token:
            return redirect("/tenders/register/")
        return register_form_post(request, token)

    if not token:
        return render(request, "register_form.html", {
            "draft": None, "step": 1, "steps": REGISTRATION_STEPS,
            "steps_total": len(REGISTRATION_STEPS),
        })
    draft = get_object_or_404(SupplierRegistrationDraft, draft_token=token)
    step = min(max(int(request.GET.get("step", draft.current_step)), 1), len(REGISTRATION_STEPS))
    return render(request, "register_form.html", _registration_context(draft, step))


@require_POST
def register_form_post(request, token: str):
    """All writes from the registration form land here.

    One endpoint, one CSRF token, one `action` field deciding what happens —
    the same pattern as the whistleblower intake. Every branch re-renders the
    form with named errors on failure: nothing a vendor typed is ever lost.
    """
    from django.core.exceptions import ValidationError
    from workflow.models import SupplierRegistrationDraft
    from workflow.services import (
        add_draft_owner, save_draft_step, submit_draft, upload_draft_document,
    )

    draft = get_object_or_404(SupplierRegistrationDraft, draft_token=token)
    action = request.POST.get("action", "save")
    step = min(max(int(request.POST.get("step", draft.current_step)), 1), len(REGISTRATION_STEPS))
    errors: dict = {}
    values = {k: request.POST.get(k, "") for k in STEP_FIELDS.get(step, [])}

    try:
        if action == "save":
            payload = {k: request.POST.get(k, "") for k in STEP_FIELDS.get(step, [])}
            if "pep_flag" in payload:
                payload["pep_flag"] = request.POST.get("pep_flag") == "on"
            if "accept_terms" in payload:
                payload["accept_terms"] = request.POST.get("accept_terms") == "on"
            save_draft_step(draft, step, payload)
            return redirect(f"/tenders/register/form/{token}/?step={min(step + 1, len(REGISTRATION_STEPS))}")

        elif action == "upload":
            kind = request.POST.get("kind", "")
            file = request.FILES.get("file")
            if not file:
                errors["file"] = "Choose the certificate file (scan or phone photo) before uploading."
            else:
                upload_draft_document(
                    draft, kind,
                    filename=file.name, content_type=file.content_type or "application/pdf",
                    issued=request.POST.get("issued") or None,
                    expiry=request.POST.get("expiry") or None,
                    blob=file.read(),
                )
                return redirect(f"/tenders/register/form/{token}/?step=3")
            step = 3

        elif action == "remove_doc":
            draft.documents.filter(kind=request.POST.get("kind", "")).delete()
            return redirect(f"/tenders/register/form/{token}/?step=3")

        elif action == "add_owner":
            add_draft_owner(
                draft, request.POST.get("owner_name", ""), request.POST.get("owner_rc", ""),
                Decimal(request.POST.get("owner_pct") or "0"),
                is_pep=request.POST.get("owner_pep") == "on",
            )
            return redirect(f"/tenders/register/form/{token}/?step=5")

        elif action == "remove_owner":
            draft.owners.filter(owner_name=request.POST.get("owner_name", "")).delete()
            return redirect(f"/tenders/register/form/{token}/?step=5")

        elif action == "submit":
            draft.accept_terms = request.POST.get("accept_terms") == "on"
            draft.save(update_fields=["accept_terms", "updated_at"])
            submit_draft(draft)
            return redirect(f"/tenders/register/done/{draft.draft_token}/")

    except ValidationError as e:
        errors = getattr(e, "message_dict", {"detail": e.messages})
    except Exception:
        errors = {"detail": ["Something went wrong saving that step. Nothing was lost — try again."]}

    draft.refresh_from_db()
    ctx = _registration_context(draft, step, errors, values)
    return render(request, "register_form.html", ctx, status=400 if errors else 200)


@require_GET
def register_done(request, token: str):
    """Confirmation page immediately after submission: the reference number,
    what happens next, and the private link to track progress."""
    from workflow.models import SupplierRegistrationDraft

    draft = get_object_or_404(SupplierRegistrationDraft, draft_token=token)
    if draft.status == "DRAFT":
        return redirect(f"/tenders/register/form/{token}/")
    return render(request, "register_done.html", {"draft": draft})


@require_http_methods(["GET", "POST"])
def register_status(request, token: str = ""):
    """The vendor's tracking page. Looked up either by the private link the
    vendor kept, or by typing the reference + email they submitted with — a
    vendor who lost the link is not locked out of their own application."""
    from workflow.models import SupplierRegistrationDraft

    drafts = None
    lookup_error = ""
    if request.method == "POST":
        reference = (request.POST.get("reference") or "").strip().upper()
        email = (request.POST.get("email") or "").strip().lower()
        if reference and email:
            drafts = SupplierRegistrationDraft.objects.filter(
                reference=reference, email__iexact=email,
            ).exclude(status="DRAFT")
            if not drafts.exists():
                lookup_error = ("No submitted registration matches that reference and email. "
                                "Check both, or use the private link from your submission.")
        else:
            lookup_error = "Enter both the reference (e.g. VND-000042) and the email you registered with."

    if token:
        draft = get_object_or_404(SupplierRegistrationDraft, draft_token=token)
        return render(request, "register_status.html", {"draft": draft})
    return render(request, "register_status.html", {
        "draft": drafts.first() if drafts else None,
        "all_matches": list(drafts) if drafts else [],
        "lookup_error": lookup_error,
    })


@require_GET
def register_review_queue(request):
    """Bureau review queue: pending supplier registrations. Public read,
    authenticated write — the queue itself is a measured thing."""
    from workflow.models import SupplierRegistrationDraft

    status = request.GET.get("status", "SUBMITTED")
    if status not in ("SUBMITTED", "VERIFYING", "APPROVED", "REJECTED"):
        status = "SUBMITTED"
    drafts = SupplierRegistrationDraft.objects.filter(
        status=status
    ).prefetch_related("documents", "owners").order_by("-submitted_at", "-updated_at")

    return render(request, "register_review.html", {
        "drafts": drafts,
        "status": status,
        "statuses": ["SUBMITTED", "VERIFYING", "APPROVED", "REJECTED"],
        "counts": {
            s: SupplierRegistrationDraft.objects.filter(status=s).count()
            for s in ["SUBMITTED", "VERIFYING", "APPROVED", "REJECTED"]
        },
        "can_review": request.user.is_authenticated and getattr(request.user, "role", "") in ("ADMIN", "DG"),
    })


@require_GET
def register_review_detail(request, pk: int):
    """One registration, everything about it, and the decision buttons."""
    from workflow.models import SupplierRegistrationDraft
    from workflow.services import REVIEW_ROLES

    draft = get_object_or_404(
        SupplierRegistrationDraft.objects.prefetch_related("documents", "owners"), pk=pk,
    )
    return render(request, "register_review_detail.html", {
        "d": draft,
        "required_documents": sorted(draft.required_document_kinds()),
        "can_review": request.user.is_authenticated and getattr(request.user, "role", "") in REVIEW_ROLES,
        "duplicate": _duplicate_party_for(draft),
    })


def _duplicate_party_for(draft):
    from workflow.services import duplicate_party
    existing = duplicate_party(draft.rc_number)
    if existing and existing.pk != getattr(draft.submitted_party, "pk", None):
        return existing
    return None


@require_POST
def register_review_decide(request, pk: int):
    """Reviewer decisions: accept/refuse each document, approve, reject.
    ADMIN/DG only; everyone else gets the 403 page, not a 500."""
    from django.core.exceptions import PermissionDenied, ValidationError
    from workflow.models import SupplierRegistrationDraft
    from workflow.services import (
        REVIEW_ROLES, approve_draft, decide_document, reject_draft, start_verification,
    )

    draft = get_object_or_404(SupplierRegistrationDraft, pk=pk)
    user = request.user
    if not user.is_authenticated or getattr(user, "role", "") not in REVIEW_ROLES:
        return render(request, "403.html", {
            "message": "Reviewing registrations is limited to Bureau administrators (ADMIN or DG role).",
        }, status=403)

    action = request.POST.get("action", "")
    error = ""
    try:
        if action == "verify_start":
            start_verification(draft, user)
        elif action == "accept_doc":
            decide_document(draft, request.POST.get("kind", ""), True, user, request.POST.get("note", ""))
        elif action == "refuse_doc":
            decide_document(draft, request.POST.get("kind", ""), False, user, request.POST.get("note", ""))
        elif action == "approve":
            approve_draft(draft, user)
        elif action == "reject":
            reject_draft(draft, user, request.POST.get("reason", ""))
    except PermissionDenied:
        return render(request, "403.html", {"message": "That action needs a Bureau administrator account."}, status=403)
    except ValidationError as e:
        messages = getattr(e, "message_dict", None) or {"detail": e.messages}
        error = " ".join(m for group in messages.values() for m in group)

    draft.refresh_from_db()
    return render(request, "register_review_detail.html", {
        "d": draft,
        "required_documents": sorted(draft.required_document_kinds()),
        "can_review": True,
        "duplicate": _duplicate_party_for(draft),
        "error": error,
    }, status=400 if error else 200)


# ============================================================ Phase 2: Bidding

@require_GET
def bid_submit_page(request, ocid: str):
    """Bid submission page. Shows the tender details and the sealing key for
    client-side encryption."""
    tender = get_object_or_404(
        Tender.objects.public().select_related("agency", "rule"),
        ocid=ocid,
    )
    if not tender.is_open:
        return render(request, "bid_closed.html", {"t": tender})

    sealing = getattr(tender, "sealing", None)
    criteria = tender.criteria.all()
    lots = tender.lots.all()

    return render(request, "bid_submit.html", {
        "t": tender,
        "sealing": sealing,
        "public_keys": sealing.public_keys if sealing else {},
        "criteria": criteria,
        "lots": lots,
        "user_party": getattr(request.user, "party", None) if request.user.is_authenticated else None,
    })


@require_GET
def bid_receipt(request, receipt_ref: str):
    """Bid receipt verification page. Anyone with the receipt reference can
    verify their bid was received and the commitment hash matches."""
    bid = get_object_or_404(
        Bid.objects.select_related("tender__agency", "supplier"),
        receipt_ref=receipt_ref,
    )
    from procurement.crypto import verify_receipt
    sig_valid = verify_receipt(
        bid.commitment_hash, bid.tender.ocid, bid.submitted_at,
        bid.receipt_ref.replace("RCP-", "") if bid.receipt_ref.startswith("RCP-") else ""
    )
    return render(request, "bid_receipt.html", {
        "bid": bid,
        "tender": bid.tender,
        "receipt_ref": receipt_ref,
        "sig_valid": sig_valid,
    })


# ============================================================ Phase 3: Evaluation

@require_GET
def evaluation_workspace(request, ocid: str):
    """Evaluation workspace: committee members enter scores, view dissent,
    and build the recommendation. Access controlled to committee members."""
    tender = get_object_or_404(
        Tender.objects.select_related("agency", "rule").prefetch_related(
            "criteria", "committee__user", "bids__scores__criterion",
        ),
        ocid=ocid,
    )
    if tender.status not in (Tender.Status.OPENED, Tender.Status.EVALUATING, Tender.Status.AWARDED):
        raise Http404("Evaluation workspace is only available after bid opening.")

    criteria = list(tender.criteria.all())
    bids = list(tender.bids.filter(
        status__in=[Bid.Status.UNSEALED, Bid.Status.RESPONSIVE, Bid.Status.EVALUATED, Bid.Status.RECOMMENDED]
    ).select_related("supplier").prefetch_related("scores__criterion", "scores__dissents"))

    committee = list(tender.committee.select_related("user").all())

    # Build scorecard matrix
    scorecard = []
    for bid in bids:
        row = {"bid": bid, "scores": {}}
        for c in criteria:
            score = bid.scores.filter(criterion=c).first()
            row["scores"][c.code] = {
                "score": score,
                "dissents": list(score.dissents.all()) if score else [],
            }
        total = sum(
            (s.raw * s.criterion.weight / Decimal("100"))
            for s in bid.scores.select_related("criterion").all()
        )
        row["total"] = round(total, 2)
        scorecard.append(row)

    scorecard.sort(key=lambda r: r["total"], reverse=True)

    # Check if current user is on the committee
    user_on_committee = False
    if request.user.is_authenticated:
        user_on_committee = any(m.user_id == request.user.id for m in committee)

    # Evaluation report if published
    from workflow.models_phases import EvaluationReport
    report = None
    try:
        report = tender.eval_report
    except EvaluationReport.DoesNotExist:
        pass

    # Approval routing
    from workflow.models_phases import ApprovalRouting
    routing = None
    try:
        routing = tender.approval_routing
    except ApprovalRouting.DoesNotExist:
        pass

    return render(request, "evaluation.html", {
        "t": tender,
        "criteria": criteria,
        "scorecard": scorecard,
        "committee": committee,
        "user_on_committee": user_on_committee,
        "report": report,
        "routing": routing,
        "can_score": tender.status in (Tender.Status.OPENED, Tender.Status.EVALUATING),
    })


@require_GET
def evaluation_report_page(request, ocid: str):
    """Public evaluation report page. Shows the committee, scores, dissent,
    and recommendation."""
    tender = get_object_or_404(
        Tender.objects.public().select_related("agency").prefetch_related(
            "criteria", "committee__user", "bids__scores__criterion",
        ),
        ocid=ocid,
    )
    from workflow.models_phases import EvaluationReport
    report = None
    try:
        report = tender.eval_report
    except EvaluationReport.DoesNotExist:
        pass

    if not report and tender.status not in (Tender.Status.EVALUATING, Tender.Status.AWARDED, Tender.Status.CONTRACTED):
        raise Http404("Evaluation report is not yet available.")

    return render(request, "evaluation_report.html", {
        "t": tender,
        "report": report,
        "criteria": tender.criteria.all(),
        "committee": tender.committee.select_related("user").all(),
        "bids": tender.bids.filter(
            status__in=[Bid.Status.UNSEALED, Bid.Status.RESPONSIVE, Bid.Status.EVALUATED, Bid.Status.RECOMMENDED]
        ).select_related("supplier").prefetch_related("scores__criterion"),
    })


# ============================================================ Phase 3: Objections

@require_GET
def objection_list(request, ocid: str):
    """Public list of objections filed against a tender's awards."""
    tender = get_object_or_404(Tender.objects.public(), ocid=ocid)
    objections = Objection.objects.filter(tender=tender).select_related(
        "award__bid__supplier"
    ).prefetch_related("panelists", "evidence")
    return render(request, "objections.html", {
        "t": tender,
        "objections": objections,
    })


@require_GET
def objection_detail(request, pk: int):
    """Public objection detail: ground, panel composition (CSO/GOV split),
    evidence, and decision."""
    objection = get_object_or_404(
        Objection.objects.select_related(
            "tender__agency", "award__bid__supplier", "filed_by"
        ).prefetch_related("panelists", "evidence"),
        pk=pk,
    )
    # Split panelists by nominator
    gov_panelists = objection.panelists.filter(nominator="GOV")
    cso_panelists = objection.panelists.filter(nominator="CSO")

    return render(request, "objection_detail.html", {
        "o": objection,
        "gov_panelists": gov_panelists,
        "cso_panelists": cso_panelists,
        "evidence_public": objection.evidence.filter(confidential=False),
    })


@require_GET
def objection_file_page(request, ocid: str, award_id: int):
    """File an objection against an award. The Georgia mechanism: 10-day freeze,
    independent panel with CSO seat."""
    tender = get_object_or_404(Tender.objects.public(), ocid=ocid)
    award = get_object_or_404(Award, pk=award_id, tender=tender)

    window_open = True
    if award.objection_until and timezone.now() > award.objection_until:
        window_open = False

    return render(request, "objection_file.html", {
        "t": tender,
        "award": award,
        "window_open": window_open,
        "objection_until": award.objection_until,
    })


# ============================================================ Phase 3: Debriefs

@require_GET
def debrief_list(request, ocid: str):
    """Public debrief log: who requested, when it was due, and the response."""
    tender = get_object_or_404(Tender.objects.public(), ocid=ocid)
    from workflow.models_phases import DebriefRequest
    debriefs = DebriefRequest.objects.filter(
        award__tender=tender
    ).select_related("supplier", "award", "responded_by")
    return render(request, "debriefs.html", {
        "t": tender,
        "debriefs": debriefs,
    })


# ============================================================ Phase 4: Contract management

@require_GET
def contract_workspace(request, reference: str):
    """Contract implementation workspace: milestones, variations, guarantees,
    acceptance certificates, and payment certification."""
    contract = get_object_or_404(
        Contract.objects.select_related(
            "award__bid__supplier", "award__tender__agency"
        ).prefetch_related(
            "events", "acceptances__certified_by", "certifications__certified_by",
        ),
        reference=reference,
    )

    from workflow.models_phases import (
        ContractGuarantee, ContractMilestone, ContractVariation,
        PaymentSchedule, SupplierPerformanceRating,
    )

    milestones = ContractMilestone.objects.filter(contract=contract)
    variations = ContractVariation.objects.filter(contract=contract)
    guarantees = ContractGuarantee.objects.filter(contract=contract)
    schedule = PaymentSchedule.objects.filter(contract=contract).select_related("milestone")

    # Performance rating
    rating = None
    try:
        rating = contract.performance
    except SupplierPerformanceRating.DoesNotExist:
        pass

    # Variation alarm
    total_variation_pct = contract.variation_pct
    variation_alarm = total_variation_pct > Decimal("10")

    # Guarantee expiry warnings
    expiring_guarantees = [g for g in guarantees if g.days_to_expiry <= 30 and not g.released_at]

    # Milestone tracking
    overdue_milestones = [m for m in milestones if m.status == "PLANNED" and m.days_late > 0]

    return render(request, "contract_workspace.html", {
        "c": contract,
        "events": contract.events.filter(published=True).order_by("-occurred_at"),
        "acceptances": contract.acceptances.select_related("certified_by"),
        "certifications": contract.certifications.select_related("certified_by"),
        "milestones": milestones,
        "variations": variations,
        "guarantees": guarantees,
        "schedule": schedule,
        "rating": rating,
        "variation_alarm": variation_alarm,
        "total_variation_pct": total_variation_pct,
        "expiring_guarantees": expiring_guarantees,
        "overdue_milestones": overdue_milestones,
        "variation_high": total_variation_pct > 10,
    })


@require_GET
def contract_milestones(request, reference: str):
    """Detailed milestone timeline for a contract."""
    contract = get_object_or_404(Contract, reference=reference)
    from workflow.models_phases import ContractMilestone
    milestones = ContractMilestone.objects.filter(contract=contract).order_by("seq")
    return render(request, "contract_milestones.html", {
        "c": contract,
        "contract": contract,
        "milestones": milestones,
    })


@require_GET
def contract_variations(request, reference: str):
    """Variation history for a contract. Public — every variation is disclosed."""
    contract = get_object_or_404(Contract, reference=reference)
    from workflow.models_phases import ContractVariation
    variations = ContractVariation.objects.filter(contract=contract)
    return render(request, "contract_variations.html", {
        "c": contract,
        "contract": contract,
        "variations": variations,
        "total_pct": contract.variation_pct,
        "variation_pct": contract.variation_pct,
    })


@require_GET
def contracts_dashboard(request):
    """Public dashboard of all active contracts: value, milestone progress,
    variation rates, guarantee expiry, and payment certifications."""
    contracts = Contract.objects.select_related(
        "award__bid__supplier", "award__tender__agency"
    ).order_by("-signed_at")


    status_filter = request.GET.get("status", "")
    if status_filter:
        contracts = contracts.filter(status=status_filter)

    # Aggregate stats
    total_value = contracts.aggregate(v=Sum("value"))["v"] or 0
    active_count = contracts.filter(status__in=["SIGNED", "ACTIVE"]).count()
    paid_count = contracts.filter(status="PAID").count()
    defaulted_count = contracts.filter(status="DEFAULTED").count()

    # Variation stats. `Contract.variation_pct` aggregates the contract's own
    # events, so calling it per row is one query per row — 100 contracts meant
    # 100 extra round trips to render a list. One grouped query answers the same
    # question for the whole page.
    rows = list(contracts[:100])
    pct_by_contract: dict[int, Decimal] = {}
    growth = (
        ContractEvent.objects.filter(
            contract__in=rows, kind__in=["VARIATION", "CLAIM"]
        )
        .values("contract")
        .annotate(growth=Sum("amount"))
    )
    for row in growth:
        contract = next((c for c in rows if c.pk == row["contract"]), None)
        if contract is None or not contract.value:
            continue
        pct_by_contract[contract.pk] = (row["growth"] or Decimal("0")) / contract.value * 100

    high_variation = [(c, pct_by_contract[c.pk]) for c in rows if pct_by_contract.get(c.pk, Decimal("0")) > 10]

    return render(request, "contracts_dashboard.html", {
        "contracts": rows,
        "variation_pct_by_id": pct_by_contract,
        "total_value": total_value,
        "active_count": active_count,
        "paid_count": paid_count,
        "defaulted_count": defaulted_count,
        "high_variation": high_variation,
        "status_filter": status_filter,
        "statuses": Contract.Status.choices,
    })


# ============================================================ Phase 4: Performance

@require_GET
def supplier_ratings(request):
    """Public supplier performance ratings. A good contractor in Wukari gets
    known without meeting an official."""
    from workflow.models_phases import SupplierPerformanceRating

    ratings = SupplierPerformanceRating.objects.filter(
        published=True
    ).select_related(
        "supplier", "contract__award__tender__agency"
    ).order_by("-rated_at")

    grade_filter = request.GET.get("grade", "")
    if grade_filter:
        ratings = ratings.filter(grade=grade_filter)

    # Aggregate by supplier
    supplier_stats = {}
    for r in ratings:
        key = r.supplier_id
        if key not in supplier_stats:
            supplier_stats[key] = {
                "supplier": r.supplier,
                "ratings": [],
                "avg_composite": Decimal("0"),
            }
        supplier_stats[key]["ratings"].append(r)

    for key, data in supplier_stats.items():
        composites = [r.composite for r in data["ratings"]]
        data["avg_composite"] = sum(composites) / len(composites) if composites else Decimal("0")

    sorted_suppliers = sorted(
        supplier_stats.values(),
        key=lambda d: d["avg_composite"],
        reverse=True,
    )

    return render(request, "supplier_ratings.html", {
        "ratings": ratings[:200],
        "sorted_suppliers": sorted_suppliers,
        "grade_filter": grade_filter,
        "grades": SupplierPerformanceRating.Grade.choices,
    })


# ============================================================ Phase 2: Agent desk

@require_GET
def agent_desk(request):
    """Agent-assisted bidding desk. Physical desk at the Bureau + each
    Senatorial zone hub, whose actions use the same API with an
    'acted_on_behalf_of' field — never a separate, unaudited back channel."""
    return render(request, "agent_desk.html", {
        # `.open()` is the single definition of "can still be bid on", shared with
        # the register and the headline count.
        "open_tenders": Tender.objects.public().open()
        .select_related("agency").order_by("submission_close_at")[:20],
        "lgas": ["Jalingo", "Wukari", "Bali", "Takum", "Gembu"],
    })


# ============================================================ Phase 2: Language

@require_GET
def set_language(request):
    """Language toggle: English / Hausa."""
    lang = request.GET.get("lang", "en")
    if lang not in ("en", "ha"):
        lang = "en"

    # Store in session
    request.session["lang"] = lang

    # If user is authenticated, store preference
    if request.user.is_authenticated:
        from workflow.models import UserLanguagePreference
        UserLanguagePreference.objects.update_or_create(
            user=request.user, defaults={"language": lang}
        )

    return redirect(request.META.get("HTTP_REFERER", "/"))


# ============================================================ Payments dashboard

@require_GET
def payments_dashboard(request):
    """Public payment certification dashboard. The payment lock: no platform
    record, no payment. This page makes the lock visible."""
    from procurement.models import PaymentCertification

    certs = PaymentCertification.objects.select_related(
        "contract__award__bid__supplier",
        "contract__award__tender__agency",
        "certified_by",
        "acceptance",
    ).order_by("-certified_at")

    year = request.GET.get("year")
    if year:
        certs = certs.filter(certified_at__year=int(year))

    total_certified = certs.aggregate(v=Sum("amount"))["v"] or 0

    # Contracts awaiting certification (accepted but not paid)
    from procurement.models import Contract
    awaiting = Contract.objects.filter(
        status=Contract.Status.ACCEPTED
    ).select_related("award__bid__supplier", "award__tender__agency")

    return render(request, "payments_dashboard.html", {
        "certs": certs[:200],
        "total_certified": total_certified,
        "awaiting": awaiting,
        "year": year,
        "years": sorted(set(
            PaymentCertification.objects.dates("certified_at", "year")
        ), reverse=True) if PaymentCertification.objects.exists() else [],
    })
