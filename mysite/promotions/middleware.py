# yourapp/middleware.py
from django.conf import settings
from django.shortcuts import redirect
from django.urls import resolve
import re

class LoginRequiredMiddleware:
    """
    Redirects ALL anonymous users to LOGIN_URL,
    except for explicitly whitelisted paths.
    """
    def __init__(self, get_response):
        self.get_response = get_response

        login_url = settings.LOGIN_URL.lstrip('/')
        self.exempt_urls = [
            re.compile(r'^' + login_url + r'$'),
        ]

        # add any paths that should stay public:
        #   - Django admin login (optional)
        #   - static/media endpoints (if needed)
        #   - health checks, etc.
        extra_exempt = getattr(settings, 'LOGIN_EXEMPT_URLS', [])
        self.exempt_urls += [re.compile(expr) for expr in extra_exempt]

    def __call__(self, request):
        # Already authenticated -> allow
        if request.user.is_authenticated:
            return self.get_response(request)

        # Path without leading slash
        path = request.path.lstrip('/')

        # Check if this path matches any exempt pattern
        for pattern in self.exempt_urls:
            if pattern.match(path):
                return self.get_response(request)

        # For safety, you could also whitelist by URL names instead of raw paths
        # via resolve(request.path), but this simple version works fine.

        # Redirect to login, preserving ?next=...
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")
