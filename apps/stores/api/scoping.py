"""`StoreScopedMixin` that also validates a superuser's `?store=` value (unknown id -> 400, not 500)."""

from rest_framework.exceptions import ValidationError

from apps.core.mixins import StoreScopedMixin
from apps.stores.models import Store


class CheckedStoreScopedMixin(StoreScopedMixin):
    def resolve_store_id(self):
        cached = getattr(self, "_resolved_store_id", None)
        if cached is not None:
            return cached
        if self.request.user.store_id:
            store_id = self.request.user.store_id
        else:
            try:
                store_id = super().resolve_store_id()
            except (TypeError, ValueError) as exc:
                raise ValidationError({"store": ["Must be a store id."]}) from exc
            if not Store.objects.filter(pk=store_id).exists():
                raise ValidationError({"store": ["Unknown store."]})
        self._resolved_store_id = store_id
        return store_id
