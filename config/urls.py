from django.contrib import admin
from django.urls import include, path

from fuel.views import MapView

urlpatterns = [
    path("", MapView.as_view(), name="map"),
    path("api/", include("fuel.urls")),
    path("admin/", admin.site.urls),
]
