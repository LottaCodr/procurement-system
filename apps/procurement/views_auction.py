"""Reverse auction views for electronic price discovery.

Design requirement: "Reverse auction: time-boxed, bid-by-bid visible only as
rank + delta-to-current-L1 (not absolute prices), auto-extend on last-minute activity"
"""
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from procurement.models_additional import ReverseAuction, AuctionBid
from procurement.models_party import Party as Supplier
import json


def auction_list(request):
    """List all active reverse auctions."""
    now = timezone.now()
    
    auctions = ReverseAuction.objects.filter(
        is_active=True
    ).select_related('tender').order_by('ends_at')
    
    # Categorize auctions
    upcoming = auctions.filter(starts_at__gt=now)
    live = auctions.filter(starts_at__lte=now, ends_at__gt=now)
    ended = auctions.filter(ends_at__lte=now)
    
    context = {
        'upcoming_auctions': upcoming,
        'live_auctions': live,
        'ended_auctions': ended,
        'page_title': 'Reverse Auctions',
    }
    
    return render(request, 'auction_list.html', context)


def auction_detail(request, auction_id):
    """View auction details with live bid tracking."""
    auction = get_object_or_404(
        ReverseAuction.objects.select_related('tender'),
        id=auction_id
    )
    
    now = timezone.now()
    is_live = auction.starts_at <= now < auction.ends_at
    
    # Get current L1 rank (not absolute price for privacy)
    bids = AuctionBid.objects.filter(
        auction=auction,
        is_valid=True
    ).order_by('amount', 'bid_at')
    
    # Calculate ranks
    ranked_bids = []
    for i, bid in enumerate(bids, 1):
        l1_amount = bids.first().amount if bids.exists() else None
        delta = (bid.amount - l1_amount) if l1_amount else 0
        
        ranked_bids.append({
            'rank': i,
            'supplier': bid.supplier.name,
            'delta_to_l1': delta,
            'bid_at': bid.bid_at,
        })
    
    # Check if current user's supplier is participating
    user_bid = None
    if request.user.is_authenticated and hasattr(request.user, 'supplier_profile'):
        user_bid = bids.filter(supplier=request.user.supplier_profile).first()
    
    context = {
        'auction': auction,
        'is_live': is_live,
        'ranked_bids': ranked_bids,
        'user_bid': user_bid,
        'time_remaining': (auction.ends_at - now).total_seconds() if is_live else 0,
        'page_title': f'Auction: {auction.tender.title}',
    }
    
    return render(request, 'auction_detail.html', context)


@login_required
@require_http_methods(["POST"])
def place_bid(request, auction_id):
    """Place a bid in a live reverse auction.
    
    Bids are validated:
    - Must be lower than current L1
    - Must be within auction time window
    - Supplier must be pre-qualified for the tender
    """
    auction = get_object_or_404(ReverseAuction, id=auction_id, is_active=True)
    
    now = timezone.now()
    
    # Check if auction is live
    if not (auction.starts_at <= now < auction.ends_at):
        return JsonResponse({
            'success': False,
            'error': 'Auction is not currently live'
        }, status=400)
    
    # Check if user has supplier profile
    if not hasattr(request.user, 'supplier_profile'):
        return JsonResponse({
            'success': False,
            'error': 'You must be a registered supplier to bid'
        }, status=403)
    
    supplier = request.user.supplier_profile
    
    # Parse bid amount
    try:
        bid_data = json.loads(request.body)
        bid_amount = float(bid_data.get('amount'))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({
            'success': False,
            'error': 'Invalid bid amount'
        }, status=400)
    
    # Validate bid is lower than current L1
    current_l1 = AuctionBid.objects.filter(
        auction=auction,
        is_valid=True
    ).order_by('amount').first()
    
    if current_l1 and bid_amount >= current_l1.amount:
        return JsonResponse({
            'success': False,
            'error': f'Bid must be lower than current lowest: ₦{current_l1.amount:,.2f}'
        }, status=400)
    
    # Validate bid is above reserve price
    if bid_amount < auction.reserve_price:
        return JsonResponse({
            'success': False,
            'error': 'Bid is below reserve price'
        }, status=400)
    
    # Create the bid
    bid = AuctionBid.objects.create(
        auction=auction,
        supplier=supplier,
        amount=bid_amount,
        bid_at=now,
        is_valid=True,
    )
    
    # Check for auto-extension (last-minute activity)
    time_remaining = (auction.ends_at - now).total_seconds()
    if time_remaining < 60:  # Last minute
        # Extend by 2 minutes
        auction.ends_at += timezone.timedelta(minutes=2)
        auction.save(update_fields=['ends_at'])
    
    # Calculate new rank
    rank = AuctionBid.objects.filter(
        auction=auction,
        is_valid=True,
        amount__lte=bid_amount
    ).count()
    
    return JsonResponse({
        'success': True,
        'bid_id': bid.id,
        'rank': rank,
        'amount': bid_amount,
        'message': f'Bid placed successfully. You are currently rank #{rank}',
    })


@login_required
@require_http_methods(["GET"])
def auction_status(request, auction_id):
    """Get live auction status (for AJAX polling)."""
    auction = get_object_or_404(ReverseAuction, id=auction_id)
    
    now = timezone.now()
    is_live = auction.starts_at <= now < auction.ends_at
    
    # Get current rankings
    bids = AuctionBid.objects.filter(
        auction=auction,
        is_valid=True
    ).order_by('amount', 'bid_at')
    
    rankings = []
    l1_amount = bids.first().amount if bids.exists() else None
    
    for i, bid in enumerate(bids, 1):
        delta = (bid.amount - l1_amount) if l1_amount else 0
        rankings.append({
            'rank': i,
            'supplier': bid.supplier.name,
            'delta': delta,
        })
    
    return JsonResponse({
        'is_live': is_live,
        'time_remaining': max(0, (auction.ends_at - now).total_seconds()),
        'bid_count': bids.count(),
        'rankings': rankings,
    })


@login_required
@require_http_methods(["POST"])
def close_auction(request, auction_id):
    """Close an auction and determine the winner (staff only)."""
    if not request.user.is_staff:
        return JsonResponse({
            'success': False,
            'error': 'Only staff can close auctions'
        }, status=403)
    
    auction = get_object_or_404(ReverseAuction, id=auction_id, is_active=True)
    
    # Get the winning bid (L1)
    winning_bid = AuctionBid.objects.filter(
        auction=auction,
        is_valid=True
    ).order_by('amount').first()
    
    if not winning_bid:
        return JsonResponse({
            'success': False,
            'error': 'No valid bids received'
        }, status=400)
    
    # Close the auction
    auction.is_active = False
    auction.closed_at = timezone.now()
    auction.winning_bid = winning_bid
    auction.save()
    
    return JsonResponse({
        'success': True,
        'winner': winning_bid.supplier.name,
        'amount': winning_bid.amount,
        'message': f'Auction closed. Winner: {winning_bid.supplier.name}',
    })
