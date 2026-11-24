from django.urls import path

from . import api, views

urlpatterns = [
    path("", views.home, name="home"),
    # Offline farmer app
    path("app/", views.app_shell, name="app_shell"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("offline/", views.offline, name="offline"),
    # Sync API
    path("api/bootstrap/", api.bootstrap, name="api_bootstrap"),
    path("api/sync/", api.sync, name="api_sync"),
    path("api/photos/", api.upload_photo, name="api_photo"),
    # Staff / desktop
    path("dashboard/", views.dashboard, name="dashboard"),
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/<str:code>/", views.batch_detail, name="batch_detail"),
    path("batches/<str:code>/qr.svg", views.batch_qr, name="batch_qr"),
    path("batches/<str:code>/label/", views.batch_label, name="batch_label"),
    path("verify/<str:code>/", views.verify, name="verify"),
    path("depot/", views.depot_home, name="depot_home"),
    path("depot/batch/<str:code>/", views.depot_batch, name="depot_batch"),
    path("depot/bag/<uuid:bag_id>/test/", views.bag_test, name="bag_test"),
    path("reports/", views.reports, name="reports"),
    path("farmers/", views.farmer_list, name="farmer_list"),
    path("farmers/new/", views.farmer_create, name="farmer_create"),
]
