"""`Authorization: Kiosk <token>` → request.auth is the Kiosk, request.user stays anonymous."""

from django.contrib.auth.models import AnonymousUser
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from apps.stores.models import Kiosk


class KioskTokenAuthentication(BaseAuthentication):
    keyword = "Kiosk"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None
        if len(auth) != 2:
            raise exceptions.AuthenticationFailed("Invalid kiosk token header.")
        kiosk = Kiosk.authenticate_token(auth[1].decode())
        if kiosk is None:
            raise exceptions.AuthenticationFailed("Invalid or inactive kiosk token.")
        return (AnonymousUser(), kiosk)

    def authenticate_header(self, request):
        return self.keyword


# drf-spectacular: document the kiosk token scheme in the OpenAPI file.
try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class KioskTokenScheme(OpenApiAuthenticationExtension):
        target_class = "apps.stores.authentication.KioskTokenAuthentication"
        name = "KioskToken"

        def get_security_definition(self, auto_schema):
            return {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "Device token issued when a kiosk is created: `Authorization: Kiosk <token>`",
            }
except ImportError:  # pragma: no cover
    pass
