"""Reverse auctions — public read views.

A reverse auction publishes *rank and distance to the current best price*, never
the absolute prices of losing bids: competitors must be able to see the pressure
they are under without being handed each other's commercial terms. Rank and
delta are published; the amounts stay sealed until the auction closes.
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from procurement.models_additional import AuctionBid, ReverseAuction


def _ranked_bids(auction: ReverseAuction) -> list[dict]:
    bids = list(
        AuctionBid.objects.filter(auction=auction, is_valid=True)
        .select_related("supplier")
        .order_by("amount", "bid_at")
    )
    best = bids[0].amount if bids else None
    return [
        {
            "rank": i,
            "supplier": bid.supplier,
            "delta_to_l1": (bid.amount - best) if best is not None else 0,
            "amount": bid.amount,
            "bid_at": bid.bid_at,
        }
        for i, bid in enumerate(bids, 1)
    ]


def auction_list(request):
    now = timezone.now()
    auctions = ReverseAuction.objects.select_related("tender__agency").order_by("ends_at")
    return render(
        request,
        "auction_list.html",
        {
            "upcoming_auctions": auctions.filter(starts_at__gt=now),
            "live_auctions": auctions.filter(starts_at__lte=now, ends_at__gt=now, is_active=True),
            "ended_auctions": auctions.filter(ends_at__lte=now).order_by("-ends_at"),
        },
    )


def auction_detail(request, auction_id: int):
    auction = get_object_or_404(
        ReverseAuction.objects.select_related("tender__agency", "winning_bid__supplier"),
        pk=auction_id,
    )
    now = timezone.now()
    is_live = auction.starts_at <= now < auction.ends_at and auction.is_active
    ended = auction.ends_at <= now

    return render(
        request,
        "auction_detail.html",
        {
            "auction": auction,
            "is_live": is_live,
            "ended": ended,
            "is_upcoming": auction.starts_at > now,
            "ranked_bids": _ranked_bids(auction),
            "seconds_remaining": max(0, int((auction.ends_at - now).total_seconds())) if is_live else 0,
        },
    )
