from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views

admin.site.site_header = "NCAL Cocoa – Administration"
admin.site.site_title = "NCAL Cocoa"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", views.CocoaLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("core.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
