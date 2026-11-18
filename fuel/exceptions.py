"""Domain errors and the DRF exception handler that renders every error as

    {"error": {"code": "<machine_code>", "message": "<human text>", "details": ...}}
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class FuelRouteError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "fuel_route_error"

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.message = message
        self.details = details


class LocationNotFound(FuelRouteError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "location_not_found"


class LocationOutsideUSA(FuelRouteError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "location_outside_usa"


class RouteNotFound(FuelRouteError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "route_not_found"


class NoFeasibleFuelPlan(FuelRouteError):
    """No sequence of stations keeps every leg within the vehicle range."""
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_feasible_fuel_plan"


class StationsNotLoaded(FuelRouteError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "stations_not_loaded"


class UpstreamTimeout(FuelRouteError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "upstream_timeout"


class UpstreamError(FuelRouteError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


def api_exception_handler(exc, context):
    if isinstance(exc, FuelRouteError):
        body = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details is not None:
            body["error"]["details"] = exc.details
        return Response(body, status=exc.status_code)

    response = exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", None) or {404: "not_found"}.get(response.status_code, "error")
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            response.data = {"error": {"code": "invalid_request", "message": "Invalid request parameters.",
                                       "details": response.data}}
        else:
            detail = response.data.get("detail", response.data) if isinstance(response.data, dict) else response.data
            response.data = {"error": {"code": code, "message": str(detail)}}
    return response
