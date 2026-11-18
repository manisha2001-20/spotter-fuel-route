from django.conf import settings
from rest_framework import serializers

from fuel.models import FuelStation


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, trim_whitespace=True,
                                  help_text='US place/address, or "latitude,longitude".')
    finish = serializers.CharField(max_length=200, trim_whitespace=True,
                                   help_text='US place/address, or "latitude,longitude".')
    initial_fuel_gallons = serializers.FloatField(required=False, default=0.0, min_value=0.0,
                                                  help_text="Fuel already in the tank at the start (default 0).")

    def validate_initial_fuel_gallons(self, value):
        cfg = settings.FUEL_ROUTE
        tank = cfg["VEHICLE_RANGE_MILES"] / cfg["MILES_PER_GALLON"]
        if value > tank:
            raise serializers.ValidationError(f"Cannot exceed the tank capacity of {tank:g} gallons.")
        return value


class FuelStationSerializer(serializers.ModelSerializer):
    retail_price = serializers.FloatField()

    class Meta:
        model = FuelStation
        fields = ["opis_id", "name", "address", "city", "state", "country", "rack_id", "retail_price",
                  "price_rows", "latitude", "longitude", "location_source"]
