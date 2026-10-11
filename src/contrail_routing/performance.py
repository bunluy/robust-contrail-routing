"""Fuel burn from the Poll-Schumann (PS) aircraft performance model."""
import numpy as np
import pandas as pd
from pycontrails import Flight
from pycontrails.models.ps_model import PSFlight
from pycontrails.models.ps_model import ps_operational_limits as ps_lims
from pycontrails.models.ps_model.ps_aircraft_params import load_aircraft_engine_params
from pycontrails.physics import units

GAMMA_AIR = 1.4
R_AIR = 287.05  # J/(kg K)


def fuel_burn(track, aircraft_type, true_airspeed_ms, takeoff_mass_kg=None):
    """
    Fuel burned along one route's along-track table (as produced by
    evaluate_route: needs longitude, latitude, altitude_m, time, temperature_K).

    takeoff_mass_kg: aircraft mass at the first point of the route (here the
    start of cruise). None lets the PS model estimate it.

    Fuel flow is per segment (point i applies to segment i -> i+1), so the
    final point is NaN by construction. Any other NaN means the model could
    not fly that segment (e.g. outside the aircraft's envelope); the route is
    then reported as infeasible (fuel_kg = NaN) rather than silently summed.
    """
    fl = Flight(
        data={
            "longitude": track["longitude"].to_numpy(dtype=float),
            "latitude": track["latitude"].to_numpy(dtype=float),
            "altitude": track["altitude_m"].to_numpy(dtype=float),
            "time": pd.to_datetime(track["time"]).to_numpy(),
            "air_temperature": track["temperature_K"].to_numpy(dtype=float),
        },
        attrs={"flight_id": str(track["route"].iloc[0]), "aircraft_type": aircraft_type},
    )
    fl["true_airspeed"] = np.full(len(fl), float(true_airspeed_ms))
    if takeoff_mass_kg is not None:
        fl.attrs["takeoff_mass"] = float(takeoff_mass_kg)
    out = PSFlight().eval(source=fl)

    ff = np.asarray(out["fuel_flow"], dtype=float)[:-1]
    mass = np.asarray(out["aircraft_mass"], dtype=float)
    t = (pd.to_datetime(track["time"]) - pd.to_datetime(track["time"]).iloc[0]).dt.total_seconds().to_numpy()
    n_bad = int(np.isnan(ff).sum())

    # Envelope check: the PS model does not refuse to fly an aircraft that is
    # too heavy for its altitude, so compare the mass with the buffet-limited
    # maximum mass at each point (PS operational limits).
    params = load_aircraft_engine_params()[aircraft_type]
    temp = track["temperature_K"].to_numpy(dtype=float)
    p_pa = units.m_to_pl(track["altitude_m"].to_numpy(dtype=float)) * 100.0
    mach = float(true_airspeed_ms) / np.sqrt(GAMMA_AIR * R_AIR * temp)
    mass_max = ps_lims.max_allowable_aircraft_mass(
        p_pa, mach, params.m_des, params.c_l_do, params.wing_surface_area, params.amass_mtow)
    margin = float(np.min(mass_max - mass))

    return {
        "fuel_kg": float(np.sum(ff * np.diff(t))) if n_bad == 0 else np.nan,
        "start_mass_kg": float(mass[0]),
        "end_mass_kg": float(mass[-1]),
        "nan_segments": n_bad,
        "mach_mean": float(np.mean(mach)),
        "mass_margin_kg": margin,          # min over route of (max allowable - actual)
        "within_envelope": margin >= 0.0,
    }


def fuel_table(tracks, aircraft_type, true_airspeed_ms, masses):
    """
    Fuel for every route under every start mass.
    tracks: {route: along-track DataFrame}; masses: {label: kg or None}.
    Returns one row per (route, mass label).
    """
    rows = []
    for label, mass in masses.items():
        for name, track in tracks.items():
            r = fuel_burn(track, aircraft_type, true_airspeed_ms, takeoff_mass_kg=mass)
            rows.append({"route": name, "mass_case": label, "mass_setting_kg": mass, **r})
    return pd.DataFrame(rows)
