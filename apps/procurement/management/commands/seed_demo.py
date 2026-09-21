"""Seed a realistic, self-consistent fiscal year of procurement data.

Not decoration: the seed deliberately produces the conditions the red-flag
engine and every invariant are meant to detect, so a reviewer can *see* the
mechanism bite rather than take it on trust.

  TENDER-A  clean, three well-separated bids, awarded at 88% of estimate
  TENDER-B  three bids within 0.4% of each other  -> T01 clustered bids
  TENDER-C  estimate ₦50,000,000, awarded ₦49,800,000 -> T03 threshold shaving
            (sits just under the ₦50m line where approval escalates)
  TENDER-D  one bid on a competitive method        -> T04 single bid
  TENDER-E  two bidders sharing a phone number      -> T06 shared identity
  TENDER-F  still a draft: attempting to publish it must fail (no estimate)
  TENDER-G  published with the committee dated *before* opening -> must be refused

After seeding, `verify_ledger` should report a valid chain and
`publish_selftest` should round-trip every record through the public API.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ledger.services import append
from procurement.models import (
    Award,
    Bid,
    Contract,
    ContractEvent,
    Criterion,
    EvaluationCommittee,
    Lot,
    Tender,
    TenderDocument,
    TenderQuestion,
)
from procurement.models_party import (
    Agency,
    BudgetLine,
    Party,
    PartyOwnership,
    PartyVerification,
    ThresholdRule,
    User,
)

NGN = Decimal


def rule(fy, method, lo, hi, body, days=14, sec="2.00", quotes=1, goods="GENERIC") -> ThresholdRule:
    """One band of the threshold matrix. Mirrors the shape of the BPP May-2025
    revision; **replace with the Taraba State Public Procurement Law figures** —
    that is a Phase 0 task, which is exactly why these are rows, not code."""
    return ThresholdRule.objects.create(
        fy=fy,
        method=method,
        goods=goods,
        min_amount=NGN(lo),
        max_amount=NGN(hi) if hi else None,
        approval_body=body,
        min_advert_days=days,
        bid_security_pct=NGN(sec),
        min_quotations=quotes,
    )


class Command(BaseCommand):
    help = "Populate the database with one fiscal year of demonstrable procurement data."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=timezone.localdate().year)
        parser.add_argument("--wipe", action="store_true", help="delete tenders/parties first")

    @transaction.atomic
    def handle(self, *a, **kw):
        fy = str(kw["year"])
        # Thresholds are versioned rows, so a re-run must not duplicate them.
        # Re-running must be safe. Threshold bands are PROTECTed by live tenders —
        # by design, so nobody can rewrite the rules underneath a running process —
        # so a normal re-run reuses the existing bands instead of recreating them.
        reuse = not kw["wipe"] and ThresholdRule.objects.filter(fy=fy).exists()
        if reuse:
            self.stdout.write(f"threshold bands for {fy} exist; reusing them (--wipe to rebuild)")
        elif kw["wipe"]:
            ThresholdRule.objects.all().delete()

        if not reuse:
            self.stdout.write(self.style.MIGRATE_HEADING(
                "Thresholds: BPP May-2025 revision shape (PLACEHOLDER - replace with the Taraba state law)")
            )
            # The unique band key is (fy, method, goods, min_amount), so the same method
            # may carry different bands for goods and works. The ₦50m line below is what
            # T03 "threshold shaving" detects a bidder aiming just under.
            bands = {}
            bands["r_ncb"] = rule(fy, "NCB", "50000000", "5000000000", "MTB", days=14, goods="GENERIC")
            rule(fy, "ICB", "5000000000", None, "FEC", days=28, goods="GENERIC")
            bands["r_rfq"] = rule(fy, "RFQ", "0", "30000000", "ACCOUNTING_OFFICER", days=7, sec="0", quotes=3, goods="GOODS")
            bands["r_works_rfq"] = rule(fy, "RFQ", "30000000", "50000000", "ACCOUNTING_OFFICER", days=7, sec="0", quotes=3, goods="WORKS")
            bands["r_shop"] = rule(fy, "SHOP", "0", "10000000", "ACCOUNTING_OFFICER", days=3, sec="0", quotes=3, goods="GOODS")
            rule(fy, "DIRECT", "0", "5000000", "DG_JUSTIFIED", days=0, sec="0", goods="GENERIC")
        else:
            def band(method, min_amount, goods):
                return ThresholdRule.objects.get(fy=fy, method=method, goods=goods, min_amount=NGN(min_amount))

            bands = {
                "r_ncb": band("NCB", "50000000", "GENERIC"),
                "r_rfq": band("RFQ", "0", "GOODS"),
                "r_works_rfq": band("RFQ", "30000000", "WORKS"),
                "r_shop": band("SHOP", "0", "GOODS"),
            }

        # Parties are get_or_create'd, so they too survive a re-run untouched.
        users = self._users()
        agencies = self._agencies()
        lines = self._budget(fy, agencies)
        suppliers = self._suppliers()

        # The shared-phone pair, planted on purpose so T06 has something to find.
        suppliers["ALHAJI MUHAMMUD ENTERPRISES"].phone = "+2348031112223"
        suppliers["ALHAJI MUHAMMUD ENTERPRISES"].save()
        suppliers["NORTHGATE CIVIL WORKS"].phone = "+2348031112223"
        suppliers["NORTHGATE CIVIL WORKS"].save()
        for name in ("ALHAJI MUHAMMUD ENTERPRISES", "NORTHGATE CIVIL WORKS"):
            PartyOwnership.objects.create(party=suppliers[name], owner_name="Ibrahim Suleiman", pct=NGN("60"))
            suppliers[name].bo_declared = True
            suppliers[name].save()

        now = timezone.now()
        built = []
        # agency, method, rule band, title, estimate, bid count, planted flavour
        specs = [
            ("MOH", "NCB", "ncb",       "Supply and delivery of essential medicines to 12 primary health centres", "120000000", 3, "clean"),
            ("MOH", "NCB", "ncb",       "Supply of ARV drugs, Q4 replenishment", "74000000", 3, "cluster"),
            ("EDU", "NCB", "ncb",       "Construction of ICT centre at Government College, Wukaro", "50000000", 3, "shave"),
            ("PUB", "NCB", "ncb",       "Rehabilitation of Jalingo-Baissa road section B (2.4km)", "860000000", 1, "single"),
            ("ENV", "RFQ", "rfq",       "Office furniture maintenance and repair", "18500000", 2, "collude"),
            ("AGS", "SHOP", "shop",     "Procurement of rice for school feeding, first tranche", "9200000", 3, "clean"),
        ]
        RULES = {"ncb": bands["r_ncb"], "rfq": bands["r_rfq"], "works_rfq": bands["r_works_rfq"], "shop": bands["r_shop"]}
        for agency_code, method, rule_key, title, est, nbids, flavour in specs:
            agency = agencies[agency_code]
            line = lines[agency_code]
            r = RULES[rule_key]
            historic = flavour != "open"
            published_at = now - timedelta(days=45) if historic else now + timedelta(days=2)
            tender = Tender(
                agency=agency,
                budget_line=line,
                method=method,
                title=title,
                description=(
                    "Direct procurement justification: sole licensed supplier within 300km, "
                    "documented in the Bureau's register." if method == "DIRECT" else title
                ),
                est_value=NGN(est),
                rule=r,
                approval_body=r.approval_body,
                reserved_for_local_pct=40,
                sme_set_aside_pct=20,
                created_by=users["pde"],
                published_at=published_at,
                # Timeline is deliberately consistent: published T-45, Q&A closes T-25,
                # bids close T-18, public opening T-17, award notice T-9, today T-0.
                # A seed that violates the rules it is meant to demonstrate is worthless.
                qa_close_at=(now - timedelta(days=25)) if historic else now + timedelta(days=15),
                submission_close_at=(now - timedelta(days=18)) if historic else now + timedelta(days=18),
                opening_at=(now - timedelta(days=17)) if historic else None,
                opening_venue="BPP conference hall, Jalingo",
            )
            tender.save()  # ocid assigned here
            tender.bid_security_amount = (tender.est_value * r.bid_security_pct / 100).quantize(NGN("1"))
            tender.publish(actor=users["pde"].username)  # validates + writes the ledger event

            self._documents(tender)
            self._criteria(tender)
            self._questions(tender, users)
            # Pools are chosen per flavour so the demo shows both outcomes: collusive
            # tenders contain the two firms that share an owner/phone; clean ones do not.
            # Pools per flavour: collude/cluster contain the planted shared-owner pair;
            # shave and clean do not — they must be judged on price alone.
            COLLUDE = [suppliers["NORTHGATE CIVIL WORKS"], suppliers["ALHAJI MUHAMMUD ENTERPRISES"], suppliers["ZAKI BUILDERS"]]
            CLEAN = [suppliers["ZAKI BUILDERS"], suppliers["SERIKENG TRADERS"], suppliers["NORTHGATE CIVIL WORKS"]]
            if flavour in ("cluster", "collude"):
                pool = COLLUDE
            else:
                pool = CLEAN
            n = min(nbids, len(pool))
            if flavour == "cluster":
                n = 3
            elif flavour == "shave":
                n = 3   # we want the shave tender to look competitive, not rigged
            elif flavour == "collude":
                n = max(n, 2)
            base = tender.est_value
            for i, sup in enumerate(pool[:n]):
                if flavour == "cluster":
                    amt = base * (NGN("0.912") + NGN("0.001") * i)
                elif flavour == "shave":
                    # Winner at 99.5% of estimate = 0.5% under the ₦50m NCB approval
                    # floor. That is exactly threshold shaving and T03 must flag it.
                    amt = NGN("49750000") if i == 0 else NGN("50500000") + base * NGN("0.01") * i
                else:
                    amt = base * (NGN("0.88") + NGN("0.045") * i)
                lot = tender.lots.first()
                bid = Bid.objects.create(
                    tender=tender, lot=lot, supplier=sup,
                    amount=amt.quantize(NGN("0.01")),
                    duration_days=90 + 15 * i,
                    status=Bid.Status.RECEIVED,
                    submitted_at=tender.submission_close_at - timedelta(hours=6 + i),
                )
                bid.refresh_from_db()
                if flavour != "open":
                    bid.status = Bid.Status.EVALUATED
                    bid.save()

            if flavour != "open":
                tender.close(actor=users["pde"].username)
                tender.open_bids(actor=users["dg"].username, custodians=["DG", "CHIEF_REGISTRAR", "PCACC"])
                for b in tender.bids.all():
                    b.price_schedule_public = True
                    b.save()
                    b.status = Bid.Status.UNSEALED
                    b.save()
                self._committee(tender, users)
                self._scores(tender)
                for b in tender.bids.all():
                    b.status = Bid.Status.EVALUATED
                    b.save()
                tender.transition(Tender.Status.EVALUATING, actor=users["dg"].username, reason="committee seated")
                winner = min(tender.bids.all(), key=lambda b: b.amount)
                award = Award.objects.create(
                    tender=tender, lot=tender.lots.first(), bid=winner, amount=winner.amount,
                    reason=(
                        f"Lowest evaluated responsive bid. {winner.supplier.legal_name} met all pass/fail criteria "
                        f"(tax clearance, PenCom, similar works) and scored {winner.score_total:.1f}/100 against a "
                        f"published estimate of {tender.estimate_display}."
                    ),
                    status=Award.Status.RECOMMENDED,
                    notice_ref=f"NO/{tender.ocid}",
                )
                # Approve first (the CHECK requires approved_at on APPROVED rows), then publish.
                award.status = Award.Status.APPROVED
                award.approved_by = users["approver"]
                award.approved_at = now - timedelta(days=9)
                award.save()
                award.publish(actor=users["dg"].username)
                winner.status = Bid.Status.RECOMMENDED
                winner.save()
                for other in tender.bids.exclude(pk=winner.pk):
                    other.status = Bid.Status.REJECTED
                    other.save()
                if len(tender.awards.all()) and tender.method in ("NCB", "ICB"):
                    contract = Contract.objects.create(
                        award=award, reference=f"CTR/{tender.ocid}", value=award.amount,
                        duration_days=120, advance_pct=NGN("15"), perf_guarantee_pct=NGN("10"),
                        location=f"{tender.agency.lga or 'Jalingo'} LGA, Taraba State",
                        deliverables=tender.title,
                    )
                    contract.status = Contract.Status.ACTIVE
                    contract.save()
                    if flavour == "shave":
                        ContractEvent.objects.create(contract=contract, kind="VARIATION", amount=award.amount * NGN("0.14"), note="Site conditions required deeper foundation than the BOQ.", occurred_at=now - timedelta(days=4))
                    ContractEvent.objects.create(contract=contract, kind="MILESTONE", amount=award.amount * NGN("0.4"), note="50% physical progress certified by the resident engineer.", occurred_at=now - timedelta(days=3))
                    append(
                        aggregate=f"procurement.Tender.{tender.pk}",
                        event_type="contract.progress_published",
                        actor=users["dg"].username,
                        payload={"pct": 40, "milestone": "first phase"},
                    )

            built.append(tender)

        # F: an unpublished draft with no estimate — publish() must refuse it.
        broken = Tender.objects.create(agency=agencies["MOH"], budget_line=lines["MOH"], method="RFQ",
                                       title="Draft: procurement that must not be publishable",
                                       rule=bands["r_rfq"], created_by=users["pde"])
        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeeded {len(built)} live processes + 1 blocked draft.\n"
                f"  users: {[u.username for u in users.values()]}\n"
                f"  agencies: {len(agencies)}  suppliers: {len(suppliers)}  tenders: {Tender.objects.count()}\n"
                f"Run: python manage.py verify_ledger   (expect: chain valid)\n"
                f"     python manage.py publish_selftest (expect: every release validates)\n"
            )
        )

    # ------------------------------------------------------------------ helpers
    def _users(self):
        mk = lambda u, r, n: User.objects.create(username=u, role=r, first_name=n.split()[0], last_name=" ".join(n.split()[1:]), email=f"{u}@tr.gov.ng", password=make_password("Change-me-first!"), mfa_secret="JBSWY3DPEHPK3PXP" * 2, mfa_enrolled_at=timezone.now())
        out = {}
        for uname, role, full in [
            ("dg", "DG", "Tanko Assemboh"),
            ("pde", "PDE", "Amina Yusuf"),
            ("eval1", "EVALUATOR", "Ibrahim Danladi"),
            ("eval2", "EVALUATOR", "Grace Terkumbur"),
            ("approver", "APPROVER", "Simon Waja"),
            ("treasury", "TREASURY", "Halima Bello"),
            ("auditor", "AUDITOR", "Emeka Obi"),
            ("appeals", "APPEALS", "Justice A. Rigam"),
        ]:
            out[uname] = mk(uname, role, full) if not User.objects.filter(username=uname).exists() else User.objects.get(username=uname)
        return out

    def _agencies(self):
        out = {}
        for code, name, kind, lga in [
            ("MOH", "Ministry of Health", "MINISTRY", "Jalingo"),
            ("EDU", "Ministry of Education", "MINISTRY", "Wukaro"),
            ("PUB", "Ministry of Works and Infrastructure", "MINISTRY", "Jalingo"),
            ("ENV", "Ministry of Environment", "MINISTRY", "Takum"),
            ("AGS", "Ministry of Agriculture", "MINISTRY", "Bali"),
            ("BPP", "Bureau of Public Procurement", "AGENCY", "Jalingo"),
        ]:
            out[code] = Agency.objects.filter(code=code).first() or Agency.objects.create(code=code, name=name, kind=kind, lga=lga, contact_email=f"{code.lower()}@tr.gov.ng")
        return out

    def _budget(self, fy, agencies):
        out = {}
        vals = {"MOH": "4800000000", "EDU": "3100000000", "PUB": "9200000000", "ENV": "900000000", "AGS": "1600000000", "BPP": "260000000"}
        for code, agency in agencies.items():
            line = BudgetLine.objects.filter(fy=fy, agency=agency).first() or BudgetLine.objects.create(
                fy=fy, agency=agency, project_code=f"{code}/CAP/01", description=f"{agency.name} capital programme {fy}",
                amount=NGN(vals[code]), released=NGN(vals[code]),
            )
            out[code] = line
        return out

    def _suppliers(self):
        out = {}
        seed = [
            ("NORTHGATE CIVIL WORKS", "RC1048822", "09876543-1234", "Taraba", "Jalingo", "LOCAL", "A"),
            ("ALHAJI MUHAMMUD ENTERPRISES", "RC2231907", "11223344-0001", "Taraba", "Wukaro", "LOCAL", "B"),
            ("ZAKI BUILDERS", "RC1778001", "44332211-0001", "Taraba", "Ibi", "LOCAL", "A"),
            ("SERIKENG TRADERS", "RC9938121", "55119933-0001", "Nasarawa", "Keffi", "NATIONAL", "B"),
        ]
        for name, rc, tin, st, lga, scope, cat in seed:
            p = Party.objects.filter(rc_number=rc).first() or Party.objects.create(
                legal_name=name, rc_number=rc, tin=tin, state=st, lga=lga, scope=scope, category=cat,
                email=f"info@{rc.lower()}.ng", phone=f"+234800000{len(out):04d}", year_incorporated=2016, bo_declared=False,
            )
            for kind, status in [("CAC", "PASSED"), ("TIN", "PASSED"), ("PENSION", "PASSED"), ("BANK", "PASSED")]:
                PartyVerification.objects.get_or_create(
                    party=p, kind=kind,
                    defaults={"status": status, "verified_at": timezone.now() - timedelta(days=30), "expires_at": (timezone.now() + timedelta(days=300)).date(), "reference": f"CHK-{rc}-{kind}"},
                )
            out[name.upper()] = p
        return out

    def _documents(self, tender):
        import hashlib

        if tender.documents.exists():
            return
        for i, (kind, title) in enumerate([("SOLICITATION", "Invitation for Bids (IFB)"), ("BOQ", "Bill of quantities and drawings"), ("EVALUATION", "Evaluation criteria and score sheet")]):
            TenderDocument.objects.create(
                tender=tender, kind=kind, title=title, version=1,
                sha256=hashlib.sha256(f"{tender.ocid}-{kind}".encode()).hexdigest(),
                size_bytes=482113 + 20000 * i, obj_key=f"tenders/{tender.ocid}/v1/{kind.lower()}.pdf",
                published=True, published_at=tender.published_at,
            )
        if tender.title.startswith("Supply of ARV"):
            TenderDocument.objects.create(
                tender=tender, kind="ADDENDUM", title="Addendum 1: cold-chain specification corrected", version=2,
                supersedes=tender.documents.filter(kind="SOLICITATION").first(),
                sha256=hashlib.sha256(b"addendum").hexdigest(), size_bytes=91002,
                obj_key=f"tenders/{tender.ocid}/v2/addendum-1.pdf", published=True,
                published_at=tender.published_at + timedelta(days=9),
                note="Published to all bidders simultaneously; the deadline was extended 5 days because this landed inside the final week.",
                upload_deadline_effect=True,
            )

    def _criteria(self, tender):
        if tender.criteria.exists():
            return
        for code, name, weight, kind in [
            ("PF1", "Tax clearance and PenCom compliance", "0", "PASSFAIL"),
            ("PF2", "Similar contracts completed in the last 5 years", "0", "PASSFAIL"),
            ("T1", "Technical methodology and programme", "30", "SCORED"),
            ("F1", "Financial capability and cash flow", "20", "SCORED"),
            ("P1", "Evaluated bid price", "50", "SCORED"),
        ]:
            Criterion.objects.create(tender=tender, code=code, name=name, weight=NGN(weight), kind=kind, min_score=NGN("0" if kind == "PASSFAIL" else "1"))

    def _questions(self, tender, users):
        if tender.questions.exists() or tender.status == Tender.Status.DRAFT:
            return
        q = TenderQuestion.objects.create(
            tender=tender,
            question="Is mobilisation advance available, and against what instrument?",
            # Day 5 of a 27-day advert: comfortably outside the final week, so the
            # model's automatic +7-day extension does NOT fire here.
            asked_at=tender.published_at + timedelta(days=5),
        )
        q.answer_now(
            "15% mobilisation against an advance guarantee from a licensed bank; no other terms are negotiable.",
            actor=users["pde"].username,
            answered_at=tender.published_at + timedelta(days=6),
        )

    def _committee(self, tender, users):
        if tender.committee.exists():
            return
        for u, role in [(users["eval1"], "CHAIR"), (users["eval2"], "MEMBER"), (users["pde"], "SECRETARY")]:
            EvaluationCommittee.objects.create(
                tender=tender, user=u, role=role,
                formed_at=tender.opening_at + timedelta(hours=2),
                declaration_sha256="a" * 64,
            )

    def _scores(self, tender):
        from procurement.models import Score

        for bid in tender.bids.all():
            for c in tender.criteria.all():
                if Score.objects.filter(bid=bid, criterion=c).exists():
                    continue
                if c.kind == Criterion.Kind.PASSFAIL:
                    raw, note = NGN("100"), "Evidence on file: certificate attached to bid."
                elif c.code == "P1":
                    lowest = min(b.amount for b in tender.bids.all())
                    raw = (lowest / bid.amount * 100).quantize(NGN("0.01")) if bid.amount else NGN("0")
                    note = f"Lowest-price formula applied to {bid.amount}."
                else:
                    raw, note = NGN("72"), "Methodology judged adequate; programme lacks float for rainy season."
                Score.objects.create(bid=bid, criterion=c, evaluator=None, raw=raw, narrative=note)
