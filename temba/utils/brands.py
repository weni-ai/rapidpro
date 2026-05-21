from django.conf import settings


def _configured_brands():
    for brand in settings.BRANDS:
        yield None, brand

    for key, brand in getattr(settings, "BRANDING", {}).items():
        yield key, brand


def _brand_matches(value: str, key: str, brand: dict) -> bool:
    identifiers = {key, brand.get("slug"), brand.get("domain")}
    identifiers.update(brand.get("hosts", []))
    return value in identifiers


def _get_default_brand() -> dict:
    for key, brand in _configured_brands():
        if _brand_matches(settings.DEFAULT_BRAND, key, brand):
            return brand

    return settings.BRANDS[0]


def get_by_host(host: str) -> dict:
    """
    Returns the branding for the given host
    """
    for key, brand in _configured_brands():
        if _brand_matches(host, key, brand):
            return brand

    return _get_default_brand()


def get_by_slug(slug: str) -> dict:
    """
    Returns the branding for the given slug
    """
    for key, brand in _configured_brands():
        if _brand_matches(slug, key, brand):
            return brand

    return _get_default_brand()
