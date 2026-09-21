"""RSS feed for tenders.

Design requirement: /feed/tenders.rss - real-time tender notifications.
"""
from django.contrib.syndication.views import Feed
from django.urls import reverse
from procurement.models import Tender
from django.utils import timezone


class TendersFeed(Feed):
    title = "Taraba State Procurement - Open Tenders"
    link = "/tenders/"
    description = "Latest open tenders from Taraba State Bureau of Public Procurement"
    
    def items(self):
        return Tender.objects.filter(
            status="PUBLISHED",
            submission_close_at__gt=timezone.now()
        ).order_by("-published_at")[:50]
    
    def item_title(self, item):
        return f"{item.ocid}: {item.title}"
    
    def item_description(self, item):
        return (
            f"Method: {item.get_method_display()}\n"
            f"Estimated Value: ₦{item.est_value:,.2f}\n"
            f"Closing: {item.submission_close_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"Agency: {item.agency.name}\n\n"
            f"{item.description[:500]}"
        )
    
    def item_link(self, item):
        return f"/tenders/{item.ocid}/"
    
    def item_pubdate(self, item):
        return item.published_at


class AwardsFeed(Feed):
    title = "Taraba State Procurement - Award Notices"
    link = "/tenders/awards/"
    description = "Latest contract awards from Taraba State"
    
    def items(self):
        from procurement.models import Award
        return Award.objects.filter(
            status__in=["PUBLISHED", "CONTRACTED"]
        ).select_related("tender", "bid__supplier").order_by("-published_at")[:50]
    
    def item_title(self, item):
        return f"Award: {item.tender.ocid} - ₦{item.amount:,.2f}"
    
    def item_description(self, item):
        return (
            f"Supplier: {item.bid.supplier.legal_name}\n"
            f"Amount: ₦{item.amount:,.2f}\n"
            f"Tender: {item.tender.title}\n\n"
            f"Reason: {item.reason}"
        )
    
    def item_link(self, item):
        return f"/tenders/{item.tender.ocid}/"
    
    def item_pubdate(self, item):
        return item.published_at
