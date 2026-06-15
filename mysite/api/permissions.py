"""Custom permissions for RuijinNurse API."""
from rest_framework.permissions import BasePermission, IsAuthenticated


class IsAuthenticatedOrReadOnly(IsAuthenticated):
    """Allow read-only access to authenticated users.

    All API endpoints require authentication for both read and write.
    This class extends IsAuthenticated and can be further customized
    for admin-only operations.
    """

    pass


class IsAdminUserOrReadOnly(BasePermission):
    """Allow read-only for any authenticated user, write for admin users."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return request.user.is_staff or request.user.is_superuser
