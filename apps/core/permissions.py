"""Two principals exist: a Kiosk (device token) and a User (JWT).

Kiosk requests authenticate with `KioskTokenAuthentication`, which puts the Kiosk in `request.auth`
and leaves `request.user` anonymous. Admin requests authenticate with JWT and carry a User.
"""

from rest_framework.permissions import BasePermission


class IsKiosk(BasePermission):
    message = "Kiosk token required."

    def has_permission(self, request, view):
        from apps.stores.models import Kiosk

        return isinstance(request.auth, Kiosk)


class IsStoreAdmin(BasePermission):
    """ADMIN role users (scoped to their store) or superusers (platform operators)."""

    message = "Admin role required."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        return user.is_superuser or user.role == user.Role.ADMIN


class IsSuperuser(BasePermission):
    message = "Platform superuser required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)
