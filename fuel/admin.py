from django.contrib import admin

from fuel.models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "retail_price", "price_rows", "location_source")
    list_filter = ("country", "location_source", "state")
    search_fields = ("name", "city", "address", "opis_id")
