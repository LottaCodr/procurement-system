"""Whistleblower encrypted intake page view.

Design requirement: "intake with anonymous handle + tracked case ID"
The whistleblower submits encrypted reports using a one-time key.
"""
import secrets
import hashlib
import logging
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from workflow.models_whistleblower import WhistleblowerCase

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def whistleblower_intake(request):
    """Display the whistleblower intake form.
    
    This page allows anonymous submission of corruption reports.
    The form uses client-side encryption with a one-time key.
    """
    # Generate a one-time key for this session
    one_time_key = secrets.token_urlsafe(32)
    request.session['whistleblower_key'] = one_time_key
    
    context = {
        'one_time_key': one_time_key,
        'page_title': 'Report Corruption Anonymously',
    }
    return render(request, 'whistleblower_intake.html', context)


@require_http_methods(["POST"])
@csrf_protect
def whistleblower_submit(request):
    """Submit an encrypted whistleblower report.
    
    The report is encrypted client-side before submission.
    We store only the encrypted content and a case ID.
    """
    try:
        # Validate the one-time key
        session_key = request.session.get('whistleblower_key')
        submitted_key = request.POST.get('encryption_key')
        
        if not session_key or session_key != submitted_key:
            return JsonResponse({
                'success': False,
                'error': 'Invalid or expired submission key'
            }, status=400)
        
        # Clear the key from session (one-time use)
        del request.session['whistleblower_key']
        
        # Get encrypted content
        encrypted_content = request.POST.get('encrypted_content')
        if not encrypted_content:
            return JsonResponse({
                'success': False,
                'error': 'No encrypted content provided'
            }, status=400)
        
        # Generate case ID
        case_id = f"WB-{secrets.token_hex(4).upper()}"
        
        # Compute hash of encrypted content for integrity verification
        content_hash = hashlib.sha256(encrypted_content.encode()).hexdigest()
        
        # Store the case
        case = WhistleblowerCase.objects.create(
            case_id=case_id,
            encrypted_content=encrypted_content,
            content_hash=content_hash,
            status='RECEIVED',
        )
        
        logger.info(f"Whistleblower case created: {case_id}")
        
        # Return case ID and verification hash
        return JsonResponse({
            'success': True,
            'case_id': case_id,
            'verification_hash': content_hash[:16],
            'message': 'Your report has been submitted securely. Save your case ID for reference.',
        })
        
    except Exception as e:
        logger.error(f"Whistleblower submission failed: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Submission failed. Please try again.'
        }, status=500)


@require_http_methods(["GET"])
def whistleblower_status(request, case_id):
    """Check the status of a whistleblower case.
    
    Only returns status information, never the encrypted content.
    """
    try:
        case = WhistleblowerCase.objects.get(case_id=case_id)
        
        return JsonResponse({
            'case_id': case.case_id,
            'status': case.status,
            'received_at': case.created_at.isoformat(),
            'last_updated': case.updated_at.isoformat(),
        })
        
    except WhistleblowerCase.DoesNotExist:
        return JsonResponse({
            'error': 'Case not found'
        }, status=404)
