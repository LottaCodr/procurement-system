"""Agent audit middleware for acted_on_behalf_of tracking.

Design requirement: "Agent-assisted bidding: physical desk at the Bureau + each
Senatorial zone hub, whose actions are the same API with a acted_on_behalf_of field"
"""
import logging
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class AgentAuditMiddleware(MiddlewareMixin):
    """Track when bureau agents act on behalf of suppliers.

    This middleware detects the X-Acted-On-Behalf-Of header and logs
    the action for audit purposes. The header is set by the agent desk
    interface when submitting on behalf of a supplier.
    """

    def process_request(self, request):
        """Extract agent information from request headers."""
        acted_on_behalf = request.META.get('HTTP_X_ACTED_ON_BEHALF_OF')

        if acted_on_behalf:
            # Store in request for use in views
            request.acted_on_behalf_of = acted_on_behalf

            # Log the action
            agent_user = request.user if request.user.is_authenticated else 'anonymous'
            logger.info(
                f'Agent action: user={agent_user}, '
                f'acting_on_behalf_of={acted_on_behalf}, '
                f'path={request.path}, '
                f'method={request.method}'
            )

            # Add to audit log if user is authenticated
            if request.user.is_authenticated:
                from ledger.services import log_event
                log_event(
                    event_type='agent_action',
                    user=request.user,
                    data={
                        'acted_on_behalf_of': acted_on_behalf,
                        'path': request.path,
                        'method': request.method,
                        'ip_address': request.META.get('REMOTE_ADDR'),
                        'user_agent': request.META.get('HTTP_USER_AGENT'),
                    }
                )
        else:
            request.acted_on_behalf_of = None
