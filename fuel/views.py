import django
from django.conf import settings
from django.db.models import Count, Q
from django.views.generic import TemplateView
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.models import FuelStation
from fuel.serializers import FuelStationSerializer, RouteRequestSerializer
from fuel.services.planner import plan_trip


class HealthView(APIView):
    """GET /api/health/ - liveness plus a summary of the loaded dataset."""

    def get(self, request):
        counts = FuelStation.objects.aggregate(
            total=Count("id"),
            us=Count("id", filter=Q(country="US")),
            routable=Count("id", filter=Q(country="US", latitude__isnull=False)),
        )
        return Response({
            "status": "ok" if counts["routable"] else "degraded",
            "django_version": django.get_version(),
            "stations": counts,
            "vehicle": {"range_miles": settings.FUEL_ROUTE["VEHICLE_RANGE_MILES"],
                        "miles_per_gallon": settings.FUEL_ROUTE["MILES_PER_GALLON"]},
        })


class FuelStationListView(generics.ListAPIView):
    """GET /api/stations/?state=TX&max_price=3.2&located=true&ordering=price

    Stations from the dataset, cheapest first. Paginated (limit/offset)."""

    serializer_class = FuelStationSerializer

    def get_queryset(self):
        qs = FuelStation.objects.all()
        params = self.request.query_params
        if state := params.get("state"):
            qs = qs.filter(state=state.strip().upper())
        if (located := params.get("located")) is not None:
            qs = qs.filter(latitude__isnull=located.lower() not in {"1", "true", "yes"})
        if max_price := params.get("max_price"):
            try:
                qs = qs.filter(retail_price__lte=float(max_price))
            except ValueError:
                pass
        if params.get("ordering") == "-price":
            qs = qs.order_by("-retail_price", "opis_id")
        return qs


class FuelStationDetailView(generics.RetrieveAPIView):
    """GET /api/stations/<opis_id>/"""

    queryset = FuelStation.objects.all()
    serializer_class = FuelStationSerializer
    lookup_field = "opis_id"


class RouteView(APIView):
    """Plan the cheapest refuelling stops between two US locations.

    POST /api/route/  {"start": "New York, NY", "finish": "Chicago, IL"}
    GET  /api/route/?start=New+York,+NY&finish=Chicago,+IL

    Optional: initial_fuel_gallons (default 0 = start with an empty tank).
    """

    def get(self, request):
        return self._plan(request.query_params)

    def post(self, request):
        return self._plan(request.data)

    def _plan(self, data):
        serializer = RouteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        return Response(plan_trip(v["start"], v["finish"], v["initial_fuel_gallons"]))


class MapView(TemplateView):
    """GET / - a small Leaflet page that calls /api/route/ and draws the result."""

    template_name = "fuel/map.html"
