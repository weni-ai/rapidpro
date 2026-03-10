from django.conf import settings


def config(request):
    return {
        "COMPONENTS_DEV_MODE": getattr(settings, "COMPONENTS_DEV_MODE", False),
        "EDITOR_DEV_MODE": getattr(settings, "EDITOR_DEV_MODE", False),
        "EDITOR_DEV_HOST": getattr(settings, "EDITOR_DEV_HOST", "localhost"),
        "COMPONENTS_DEV_HOST": getattr(settings, "COMPONENTS_DEV_HOST", "localhost"),
        # Expose allauth-related flags used by templates
        "SOCIALACCOUNT_ENABLED": getattr(settings, "SOCIALACCOUNT_ENABLED", True),
        "SOCIALACCOUNT_ONLY": getattr(settings, "SOCIALACCOUNT_ONLY", False),
    }


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(branding=request.branding)
