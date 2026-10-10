"""Route geometry: great-circle routes with smooth lateral deviations,
flown at constant speed and constant altitude."""
import numpy as np
import pandas as pd
from pycontrails import Flight

R_EARTH_KM = 6371.0
FT_TO_M = 0.3048


def haversine_km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km between points given in degrees."""
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * R_EARTH_KM * np.arcsin(np.sqrt(a))


def flight_level_to_m(flight_level):
    """Pressure altitude in metres for a flight level (FL370 = 37,000 ft)."""
    return flight_level * 100 * FT_TO_M


def lonlat_to_xyz(lon, lat):
    lon, lat = np.radians(lon), np.radians(lat)
    return np.stack([np.cos(lat) * np.cos(lon),
                     np.cos(lat) * np.sin(lon),
                     np.sin(lat)], axis=-1)


def xyz_to_lonlat(v):
    lon = np.degrees(np.arctan2(v[..., 1], v[..., 0]))
    lat = np.degrees(np.arcsin(np.clip(v[..., 2], -1.0, 1.0)))
    return lon, lat


def make_route(origin, destination, offset_km, altitude_m, departure, speed_ms,
               time_step_s=60, name="route", n_shape=400):
    """
    Great-circle route from origin to destination (each (lon, lat) in degrees),
    bent sideways by offset_km * sin(pi * s), s = 0 -> 1 along the route.
    offset_km > 0 bends to the left of travel. Flown at constant speed_ms,
    one point every time_step_s seconds, at constant altitude_m.
    """
    a, b = lonlat_to_xyz(*origin), lonlat_to_xyz(*destination)
    omega = np.arccos(np.clip(a @ b, -1.0, 1.0))
    s = np.linspace(0.0, 1.0, n_shape)[:, None]
    p = (np.sin((1 - s) * omega) * a + np.sin(s * omega) * b) / np.sin(omega)
    n = np.cross(a, b)
    n /= np.linalg.norm(n)                         # unit normal = left of travel
    delta = offset_km * np.sin(np.pi * s) / R_EARTH_KM
    lon_d, lat_d = xyz_to_lonlat(np.cos(delta) * p + np.sin(delta) * n)

    seg = haversine_km(lon_d[:-1], lat_d[:-1], lon_d[1:], lat_d[1:])
    cum_km = np.concatenate([[0.0], np.cumsum(seg)])
    duration_s = cum_km[-1] * 1000.0 / speed_ms
    t_s = np.arange(0.0, duration_s, time_step_s)
    if duration_s - t_s[-1] > 1.0:
        t_s = np.append(t_s, duration_s)
    d_km = t_s * speed_ms / 1000.0

    return Flight(
        longitude=np.interp(d_km, cum_km, lon_d),
        latitude=np.interp(d_km, cum_km, lat_d),
        altitude=np.full(len(t_s), float(altitude_m)),
        time=pd.Timestamp(departure) + pd.to_timedelta(t_s, unit="s"),
        attrs={"flight_id": name},
    )
