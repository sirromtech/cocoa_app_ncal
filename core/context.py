from django.conf import settings


def app_context(request):
    return {"COCOA": settings.COCOA, "APP_VERSION": settings.COCOA["APP_VERSION"]}
