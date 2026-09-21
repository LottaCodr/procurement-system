"""Per-MDA utilization dashboard.

Design requirement: "publish per-MDA utilisation and per-MDA direct-procurement share,
monthly, unredacted. Sunlight on the laggards is the cheapest management tool you have."
"""
from django.shortcuts import render
from django.db.models import Sum, Count, Q, F
from django.utils import timezone
from procurement.models import Tender, Award, Contract
from procurement.models_party import Agency


def mda_dashboard(request):
    """Public dashboard showing per-MDA procurement utilization metrics."""
    # Get all active agencies
    agencies = Agency.objects.filter(is_active=True).order_by('code')
    
    # Calculate metrics for each agency
    mda_metrics = []
    current_year = timezone.now().year
    
    for agency in agencies:
        tenders = Tender.objects.filter(agency=agency, published_at__year=current_year)
        
        total_tenders = tenders.count()
        total_value = tenders.aggregate(total=Sum('estimated_value'))['total'] or 0
        
        # Direct procurement metrics
        direct_tenders = tenders.filter(method='DIRECT')
        direct_count = direct_tenders.count()
        direct_value = direct_tenders.aggregate(total=Sum('estimated_value'))['total'] or 0
        
        # Calculate direct procurement percentage
        direct_pct = (direct_value / total_value * 100) if total_value > 0 else 0
        
        # Awards metrics
        awards = Award.objects.filter(
            tender__agency=agency,
            tender__published_at__year=current_year
        )
        total_awards = awards.count()
        awarded_value = awards.aggregate(total=Sum('amount'))['total'] or 0
        
        # Contracts metrics
        contracts = Contract.objects.filter(
            award__tender__agency=agency,
            award__tender__published_at__year=current_year
        )
        total_contracts = contracts.count()
        contract_value = contracts.aggregate(total=Sum('value'))['total'] or 0
        
        # Local content metrics (Taraba-based suppliers)
        local_awards = awards.filter(bid__supplier__state='Taraba')
        local_count = local_awards.count()
        local_value = local_awards.aggregate(total=Sum('amount'))['total'] or 0
        local_pct = (local_value / awarded_value * 100) if awarded_value > 0 else 0
        
        # Completion rate
        completed_contracts = contracts.filter(status='COMPLETED')
        completion_rate = (completed_contracts.count() / total_contracts * 100) if total_contracts > 0 else 0
        
        mda_metrics.append({
            'agency': agency,
            'total_tenders': total_tenders,
            'total_value': total_value,
            'direct_count': direct_count,
            'direct_value': direct_value,
            'direct_pct': direct_pct,
            'total_awards': total_awards,
            'awarded_value': awarded_value,
            'total_contracts': total_contracts,
            'contract_value': contract_value,
            'local_count': local_count,
            'local_value': local_value,
            'local_pct': local_pct,
            'completion_rate': completion_rate,
        })
    
    # Sort by total value (descending)
    mda_metrics.sort(key=lambda x: x['total_value'], reverse=True)
    
    # Calculate aggregate totals
    totals = {
        'total_tenders': sum(m['total_tenders'] for m in mda_metrics),
        'total_value': sum(m['total_value'] for m in mda_metrics),
        'direct_count': sum(m['direct_count'] for m in mda_metrics),
        'direct_value': sum(m['direct_value'] for m in mda_metrics),
        'total_awards': sum(m['total_awards'] for m in mda_metrics),
        'awarded_value': sum(m['awarded_value'] for m in mda_metrics),
        'total_contracts': sum(m['total_contracts'] for m in mda_metrics),
        'contract_value': sum(m['contract_value'] for m in mda_metrics),
        'local_count': sum(m['local_count'] for m in mda_metrics),
        'local_value': sum(m['local_value'] for m in mda_metrics),
    }
    
    totals['direct_pct'] = (totals['direct_value'] / totals['total_value'] * 100) if totals['total_value'] > 0 else 0
    totals['local_pct'] = (totals['local_value'] / totals['awarded_value'] * 100) if totals['awarded_value'] > 0 else 0
    
    context = {
        'mda_metrics': mda_metrics,
        'totals': totals,
        'current_year': current_year,
        'page_title': 'MDA Utilization Dashboard',
    }
    
    return render(request, 'mda_dashboard.html', context)


def mda_detail(request, agency_code):
    """Detailed view for a single MDA."""
    agency = Agency.objects.get(code=agency_code)
    current_year = timezone.now().year
    
    tenders = Tender.objects.filter(
        agency=agency,
        published_at__year=current_year
    ).order_by('-published_at')
    
    awards = Award.objects.filter(
        tender__agency=agency,
        tender__published_at__year=current_year
    ).select_related('tender', 'bid__supplier').order_by('-awarded_at')
    
    contracts = Contract.objects.filter(
        award__tender__agency=agency,
        award__tender__published_at__year=current_year
    ).select_related('award__tender', 'award__supplier').order_by('-signed_at')
    
    # Monthly breakdown
    monthly_data = []
    for month in range(1, 13):
        month_tenders = tenders.filter(published_at__month=month)
        month_awards = awards.filter(awarded_at__month=month)
        
        monthly_data.append({
            'month': month,
            'month_name': timezone.datetime(current_year, month, 1).strftime('%B'),
            'tender_count': month_tenders.count(),
            'tender_value': month_tenders.aggregate(total=Sum('estimated_value'))['total'] or 0,
            'award_count': month_awards.count(),
            'award_value': month_awards.aggregate(total=Sum('amount'))['total'] or 0,
        })
    
    context = {
        'agency': agency,
        'tenders': tenders,
        'awards': awards,
        'contracts': contracts,
        'monthly_data': monthly_data,
        'current_year': current_year,
        'page_title': f'{agency.name} - Procurement Detail',
    }
    
    return render(request, 'mda_detail.html', context)
