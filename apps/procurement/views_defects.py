"""Defects liability and contract close-out views.

Design requirement: "Defects liability period tracking, retention money release,
and formal close-out with performance evaluation"
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from procurement.models import Contract
from procurement.models_additional import DefectReport, ContractCloseOut
from procurement.models_party import Party as Supplier


def defects_list(request):
    """List all contracts in defects liability period."""
    now = timezone.now()
    
    # Contracts with active defects liability period
    contracts = Contract.objects.filter(
        status='COMPLETED',
        defects_liability_end__gt=now,
    ).select_related(
        'award__tender',
        'award__bid__supplier'
    ).order_by('defects_liability_end')
    
    # Calculate remaining days
    contract_data = []
    for contract in contracts:
        remaining = (contract.defects_liability_end - now).days
        contract_data.append({
            'contract': contract,
            'remaining_days': remaining,
            'defect_count': DefectReport.objects.filter(contract=contract).count(),
            'open_defects': DefectReport.objects.filter(
                contract=contract,
                status='OPEN'
            ).count(),
        })
    
    context = {
        'contract_data': contract_data,
        'page_title': 'Defects Liability Period',
    }
    
    return render(request, 'defects_list.html', context)


def defects_detail(request, contract_id):
    """View defects for a specific contract."""
    contract = get_object_or_404(
        Contract.objects.select_related('award__tender', 'award__bid__supplier'),
        id=contract_id
    )
    
    defects = DefectReport.objects.filter(
        contract=contract
    ).order_by('-reported_at')
    
    # Calculate retention money status
    retention_amount = contract.value * (contract.retention_pct / 100)
    retention_released = contract.retention_released
    retention_held = retention_amount - retention_released
    
    context = {
        'contract': contract,
        'defects': defects,
        'retention_amount': retention_amount,
        'retention_released': retention_released,
        'retention_held': retention_held,
        'page_title': f'Defects - Contract {contract.contract_number}',
    }
    
    return render(request, 'defects_detail.html', context)


@login_required
@require_http_methods(["POST"])
def report_defect(request, contract_id):
    """Report a defect during the liability period."""
    contract = get_object_or_404(Contract, id=contract_id)
    
    # Check if still in defects liability period
    if timezone.now() > contract.defects_liability_end:
        return JsonResponse({
            'success': False,
            'error': 'Defects liability period has expired'
        }, status=400)
    
    try:
        import json
        data = json.loads(request.body)
        
        defect = DefectReport.objects.create(
            contract=contract,
            description=data.get('description'),
            severity=data.get('severity', 'MEDIUM'),
            reported_at=timezone.now(),
            reported_by=request.user,
            status='OPEN',
        )
        
        return JsonResponse({
            'success': True,
            'defect_id': defect.id,
            'message': 'Defect reported successfully',
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def resolve_defect(request, defect_id):
    """Mark a defect as resolved (supplier action)."""
    defect = get_object_or_404(DefectReport, id=defect_id)
    
    # Verify user is the supplier
    if request.user.supplier_profile != defect.contract.award.bid.supplier:
        return JsonResponse({
            'success': False,
            'error': 'Only the supplier can resolve defects'
        }, status=403)
    
    defect.status = 'RESOLVED'
    defect.resolved_at = timezone.now()
    defect.save()
    
    return JsonResponse({
        'success': True,
        'message': 'Defect marked as resolved',
    })


@login_required
@require_http_methods(["POST"])
def verify_defect(request, defect_id):
    """Verify a resolved defect (buyer/staff action)."""
    if not request.user.is_staff:
        return JsonResponse({
            'success': False,
            'error': 'Only staff can verify defects'
        }, status=403)
    
    defect = get_object_or_404(DefectReport, id=defect_id)
    
    defect.status = 'VERIFIED'
    defect.verified_at = timezone.now()
    defect.verified_by = request.user
    defect.save()
    
    return JsonResponse({
        'success': True,
        'message': 'Defect verified successfully',
    })


@login_required
@require_http_methods(["POST"])
def close_out_contract(request, contract_id):
    """Formal contract close-out with performance evaluation."""
    if not request.user.is_staff:
        return JsonResponse({
            'success': False,
            'error': 'Only staff can close out contracts'
        }, status=403)
    
    contract = get_object_or_404(Contract, id=contract_id)
    
    # Check all defects are resolved
    open_defects = DefectReport.objects.filter(
        contract=contract,
        status='OPEN'
    ).count()
    
    if open_defects > 0:
        return JsonResponse({
            'success': False,
            'error': f'Cannot close out: {open_defects} defects still open'
        }, status=400)
    
    # Check defects liability period has ended
    if timezone.now() < contract.defects_liability_end:
        return JsonResponse({
            'success': False,
            'error': 'Defects liability period has not ended'
        }, status=400)
    
    try:
        import json
        data = json.loads(request.body)
        
        # Create close-out record
        close_out = ContractCloseOut.objects.create(
            contract=contract,
            performance_rating=data.get('performance_rating', 'SATISFACTORY'),
            comments=data.get('comments', ''),
            closed_out_at=timezone.now(),
            closed_out_by=request.user,
        )
        
        # Release retention money
        retention_amount = contract.value * (contract.retention_pct / 100)
        contract.retention_released = retention_amount
        contract.status = 'CLOSED_OUT'
        contract.save()
        
        # Update supplier performance score
        supplier = contract.award.bid.supplier
        supplier.update_performance_score()
        
        return JsonResponse({
            'success': True,
            'close_out_id': close_out.id,
            'retention_released': retention_amount,
            'message': f'Contract closed out. Retention ₦{retention_amount:,.2f} released.',
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


def contract_performance(request, supplier_id):
    """View supplier's contract performance history."""
    supplier = get_object_or_404(Supplier, id=supplier_id)
    
    # Get all close-outs for this supplier
    close_outs = ContractCloseOut.objects.filter(
        contract__award__bid__supplier=supplier
    ).select_related('contract__award__tender').order_by('-closed_out_at')
    
    # Calculate performance metrics
    total_contracts = close_outs.count()
    excellent = close_outs.filter(performance_rating='EXCELLENT').count()
    satisfactory = close_outs.filter(performance_rating='SATISFACTORY').count()
    poor = close_outs.filter(performance_rating='POOR').count()
    
    context = {
        'supplier': supplier,
        'close_outs': close_outs,
        'total_contracts': total_contracts,
        'excellent': excellent,
        'satisfactory': satisfactory,
        'poor': poor,
        'performance_score': supplier.performance_score,
        'page_title': f'Performance - {supplier.name}',
    }
    
    return render(request, 'contract_performance.html', context)
