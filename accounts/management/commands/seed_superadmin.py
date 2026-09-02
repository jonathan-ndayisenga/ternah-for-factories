"""Seed the software owner once — SaaS ref §01: never created through the UI."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the single platform super user (idempotent)."

    def handle(self, *args, **opts):
        User = get_user_model()
        u, created = User.objects.get_or_create(
            username="superadmin",
            defaults={"is_superuser": True, "is_staff": True, "role": "OWNER"},
        )
        if created:
            u.set_password("change-me-now")
            u.save()
            self.stdout.write(self.style.SUCCESS("superadmin created (password: change-me-now — rotate it)."))
        else:
            self.stdout.write("superadmin already exists.")
