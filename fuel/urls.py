from django.urls import path

from fuel import views

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("stations/", views.FuelStationListView.as_view(), name="station-list"),
    path("stations/<int:opis_id>/", views.FuelStationDetailView.as_view(), name="station-detail"),
    path("route/", views.RouteView.as_view(), name="route"),
]
