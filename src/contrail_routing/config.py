"""Fixed definition of the case study, shared by all notebooks."""
import pandas as pd

from .geometry import flight_level_to_m, make_route

ORIGIN = (-10.0, 45.0)         # (lon, lat) in degrees
DESTINATION = (10.0, 55.0)
DEPARTURE = pd.Timestamp("2022-03-01 00:00")
CRUISE_SPEED_MS = 230.0        # constant true airspeed; no wind
AIRCRAFT = "A320"

ERA5_TIME = ("2022-03-01T00", "2022-03-01T06")
LEVELS_COARSE = [200, 225, 250, 275, 300]                         # Sessions 2-6
LEVELS_FINE = [175, 190, 200, 210, 220, 230, 240, 250, 275, 300]  # Session 7 onwards
REGION_LON = (-15.0, 15.0)
REGION_LAT = (40.0, 60.0)

# Session 8: dates fixed before any results were seen (1st and 15th of each month, 2022)
STUDY_DATES = [f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 15)]
FLIGHT_LEVELS = [310, 320, 330, 340, 350, 360, 370, 380]
OFFSETS_KM = [-300, -150, 0, 150, 300]


def route_label(offset_km, flight_level):
    """E.g. (150, 360) -> 'NW150_FL360'; offset > 0 is left of travel (north-west)."""
    side = "GC" if offset_km == 0 else ("NW" if offset_km > 0 else "SE")
    return f"{side}{abs(offset_km)}_FL{flight_level}"


def route_specs(flight_levels=FLIGHT_LEVELS, offsets_km=OFFSETS_KM):
    """{route name: {offset_km, flight_level, altitude_m}} for a grid of routes."""
    return {
        route_label(o, fl): {"offset_km": o, "flight_level": fl,
                             "altitude_m": round(flight_level_to_m(fl), 1)}
        for fl in flight_levels for o in offsets_km
    }


def build_routes(specs, departure=DEPARTURE):
    """{route name: Flight} for the case-study endpoints and speed.
    departure defaults to the Sessions 2-7 case; other dates give the same
    routes shifted in time."""
    return {
        name: make_route(ORIGIN, DESTINATION, s["offset_km"], s["altitude_m"],
                         departure, CRUISE_SPEED_MS, name=name)
        for name, s in specs.items()
    }