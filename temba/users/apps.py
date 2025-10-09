from django.apps import AppConfig
from django.db.models.signals import post_migrate


class UsersConfig(AppConfig):
    name = "temba.users"

    def ready(self):
        post_migrate.connect(on_post_migrate)


def on_post_migrate(sender, **kwargs):
    """
    Creates the system user if necessary
    """
    # Only run this for our own app to avoid referencing fields (like uuid)
    # before our migrations have been applied when other apps are migrated.
    if getattr(sender, "name", None) != "temba.users":
        return

    from .models import User

    try:
        User.get_system_user()
    except User.DoesNotExist:
        User.objects.create_system_user()
