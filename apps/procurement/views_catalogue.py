"""Catalogue fast-lane views for common-use goods.

Design requirement: "Catalogue fast-lane for repeatable goods (medicines, furniture,
ICT, vehicles) with ≥3-quote auto-comparison and L1"
"""
from django.shortcuts import render, get_object_or_404
from django.db.models import Min, Count, Q
from procurement.models_additional import CatalogueItem, CatalogueQuote, PurchaseOrder
from procurement.models_party import Party as Supplier


def catalogue_list(request):
    """List all catalogue items grouped by category."""
    category = request.GET.get('category')
    
    items = CatalogueItem.objects.filter(is_active=True)
    
    if category:
        items = items.filter(category=category)
    
    # Group by category
    categories = {}
    for item in items:
        if item.category not in categories:
            categories[item.category] = []
        categories[item.category].append(item)
    
    # Get all unique categories
    all_categories = CatalogueItem.objects.filter(
        is_active=True
    ).values_list('category', flat=True).distinct().order_by('category')
    
    context = {
        'categories': categories,
        'all_categories': all_categories,
        'selected_category': category,
        'page_title': 'Catalogue - Common-Use Goods',
    }
    
    return render(request, 'catalogue_list.html', context)


def catalogue_detail(request, item_id):
    """View details and quotes for a catalogue item."""
    item = get_object_or_404(CatalogueItem, id=item_id, is_active=True)
    
    # Get all quotes for this item
    quotes = CatalogueQuote.objects.filter(
        item=item,
        is_active=True
    ).select_related('supplier').order_by('unit_price')
    
    # Identify L1 (lowest price)
    l1_quote = quotes.first()
    
    context = {
        'item': item,
        'quotes': quotes,
        'l1_quote': l1_quote,
        'quote_count': quotes.count(),
        'page_title': f'{item.name} - Catalogue',
    }
    
    return render(request, 'catalogue_detail.html', context)


def create_purchase_order(request, item_id):
    """Create a purchase order for a catalogue item.
    
    Automatically selects L1 supplier if ≥3 quotes exist.
    """
    item = get_object_or_404(CatalogueItem, id=item_id, is_active=True)
    
    if request.method == 'POST':
        quantity = int(request.POST.get('quantity', 1))
        delivery_location = request.POST.get('delivery_location', '')
        
        # Get quotes
        quotes = CatalogueQuote.objects.filter(
            item=item,
            is_active=True
        ).select_related('supplier').order_by('unit_price')
        
        if quotes.count() < 3:
            return render(request, 'catalogue_insufficient_quotes.html', {
                'item': item,
                'quote_count': quotes.count(),
                'required': 3,
            })
        
        # Select L1 supplier
        l1_quote = quotes.first()
        
        # Create purchase order
        po = PurchaseOrder.objects.create(
            catalogue_item=item,
            supplier=l1_quote.supplier,
            quantity=quantity,
            unit_price=l1_quote.unit_price,
            total_amount=l1_quote.unit_price * quantity,
            delivery_location=delivery_location,
            status='AWARDED',
            awarded_at=timezone.now(),
        )
        
        # Link to quote
        po.selected_quote = l1_quote
        po.save()
        
        return render(request, 'purchase_order_created.html', {
            'po': po,
            'item': item,
            'supplier': l1_quote.supplier,
        })
    
    # GET request - show form
    quotes = CatalogueQuote.objects.filter(
        item=item,
        is_active=True
    ).select_related('supplier').order_by('unit_price')
    
    context = {
        'item': item,
        'quotes': quotes,
        'can_create': quotes.count() >= 3,
        'page_title': f'Create Purchase Order - {item.name}',
    }
    
    return render(request, 'create_purchase_order.html', context)


def purchase_order_list(request):
    """List all purchase orders."""
    status = request.GET.get('status')
    
    pos = PurchaseOrder.objects.select_related(
        'catalogue_item', 'supplier'
    ).order_by('-created_at')
    
    if status:
        pos = pos.filter(status=status)
    
    # Calculate totals
    total_amount = pos.aggregate(total=models.Sum('total_amount'))['total'] or 0
    total_orders = pos.count()
    
    context = {
        'purchase_orders': pos,
        'total_amount': total_amount,
        'total_orders': total_orders,
        'selected_status': status,
        'page_title': 'Purchase Orders',
    }
    
    return render(request, 'purchase_order_list.html', context)


def purchase_order_detail(request, po_id):
    """View purchase order details."""
    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            'catalogue_item', 'supplier', 'selected_quote'
        ),
        id=po_id
    )
    
    context = {
        'po': po,
        'page_title': f'Purchase Order {po.po_number}',
    }
    
    return render(request, 'purchase_order_detail.html', context)
