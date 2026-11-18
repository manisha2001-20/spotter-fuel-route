from django.db import models


class FuelStation(models.Model):
    """One truck stop from the assessment CSV.

    The CSV lists some stations on several rows with different prices (6,738
    unique OPIS IDs across 8,151 rows). One record is stored per OPIS ID, with
    the lowest listed price; `price_rows` keeps the number of CSV rows seen.
    """

    class LocationSource(models.TextChoices):
        CENSUS_PLACE = "census_place", "US Census place"
        CENSUS_COUNTY_SUBDIVISION = "census_county_subdivision", "US Census county subdivision"
        NOMINATIM = "nominatim", "OpenStreetMap Nominatim"
        NONE = "none", "Not located"

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    country = models.CharField(max_length=2, default="US")
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=12, decimal_places=8, db_index=True)
    price_rows = models.PositiveSmallIntegerField(default=1)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location_source = models.CharField(max_length=32, choices=LocationSource.choices, default=LocationSource.NONE)

    class Meta:
        ordering = ["retail_price", "opis_id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"
