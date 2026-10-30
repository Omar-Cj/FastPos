"""View mixins shared by kiosk and admin APIs."""

from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.permissions import IsKiosk, IsStoreAdmin
from apps.stores.authentication import KioskTokenAuthentication


class KioskViewMixin:
    """Kiosk endpoints. `self.kiosk` and `self.store` are the calling device and its store."""

    authentication_classes = [KioskTokenAuthentication]
    permission_classes = [IsKiosk]
    pagination_class = None

    @property
    def kiosk(self):
        return self.request.auth

    @property
    def store(self):
        return self.request.auth.store


class AdminViewMixin:
    """Admin panel endpoints, JWT + ADMIN role."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsStoreAdmin]


class StoreScopedMixin(AdminViewMixin):
    """Filters the queryset to the admin's store.

    Superusers have no store and must pass `?store=<id>` (or `store` in the body on create).
    Subclasses set `store_field = "store"` (default) to the FK path that leads to the Store,
    e.g. `"order__store"`.
    """

    store_field = "store"

    def resolve_store_id(self):
        user = self.request.user
        if user.store_id:
            return user.store_id
        store_id = self.request.query_params.get("store") or self.request.data.get("store")
        if not store_id:
            raise ValidationError({"store": ["Superusers must specify a store."]})
        return int(store_id)

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(**{f"{self.store_field}_id": self.resolve_store_id()})

    def perform_create(self, serializer):
        if self.store_field == "store":
            serializer.save(store_id=self.resolve_store_id())
        else:
            serializer.save()
