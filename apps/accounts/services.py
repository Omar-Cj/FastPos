"""Staff verification used by kiosk override / remove / cancel flows."""

from apps.core.exceptions import InvalidStaffPin

from .models import User


def verify_staff(store, staff_code: str, pin: str) -> User:
    """Return the active staff/admin user of `store` matching code + PIN, else raise InvalidStaffPin."""
    if not staff_code or not pin:
        raise InvalidStaffPin()
    user = User.objects.filter(store=store, staff_code=staff_code, is_active=True).first()
    if user is None or not user.check_pin(pin):
        raise InvalidStaffPin()
    return user
