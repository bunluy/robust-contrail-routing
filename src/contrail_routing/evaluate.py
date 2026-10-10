"""Contrail exposure (PCR) and fuel burn (Poll-Schumann) along routes."""
import numpy as np
import pandas as pd
from pycontrails.models.pcr import PCR
from pycontrails.models.ps_model import PSFlight
from pycontrails.physics import thermo

from .geometry import haversine_km

EI_CO2 = 3.159  # kg CO2 per kg Jet A-1 (pycontrails JetA default)


def evaluate_route(flight, met, aircraft_type=None, true_airspeed_ms=None,
                   humidity_scaling=None):
    """PCR along one route; optionally fuel flow from the PS model.

    humidity_scaling: None for the unscaled baseline, or a pycontrails
    HumidityScaling instance (uncertainty scenarios).

    Fuel flow is segment-based: the value at point i applies to the segment
    i -> i+1, so the final point has no value (NaN) by construction. This
    matches the leg_km convention (each leg attributed to its start point).
    """
    pcr_model = PCR(met=met, humidity_scaling=humidity_scaling)  # fresh model per route
    if humidity_scaling is None and pcr_model.params["humidity_scaling"] is not None:
        raise ValueError("Baseline must run without humidity scaling")
    out = pcr_model.eval(source=flight)

    # With scaling, pycontrails attaches its own (scaled) RHi; otherwise compute it
    if "rhi" in out:
        rhi = np.asarray(out["rhi"], dtype=float)
    else:
        rhi = thermo.rhi(out["specific_humidity"], out["air_temperature"], out["air_pressure"])

    df = pd.DataFrame({
        "route": flight.attrs["flight_id"],
        "time": out["time"],
        "longitude": out["longitude"],
        "latitude": out["latitude"],
        "altitude_m": out["altitude"],
        "air_pressure_Pa": out["air_pressure"],
        "temperature_K": out["air_temperature"],
        "rh": out["rh"],
        "rhi": rhi,
        "SAC": out["sac"],
        "ISSR": out["issr"],
        "PCR": out["pcr"],
    })
    lon, lat = df["longitude"].to_numpy(), df["latitude"].to_numpy()
    df["leg_km"] = np.append(haversine_km(lon[:-1], lat[:-1], lon[1:], lat[1:]), 0.0)

    if aircraft_type is not None:
        out.attrs["aircraft_type"] = aircraft_type
        out["true_airspeed"] = np.full(len(out), float(true_airspeed_ms))
        perf = PSFlight().eval(source=out)
        df["fuel_flow_kg_s"] = np.asarray(perf["fuel_flow"], dtype=float)
        df["aircraft_mass_kg"] = np.asarray(perf["aircraft_mass"], dtype=float)
    return df


def evaluate_routes(routes, met, **kwargs):
    """Evaluate a dict {name: Flight}; stop if any result is missing."""
    tables = {}
    for name, fl in routes.items():
        tables[name] = evaluate_route(fl, met, **kwargs)
    combined = pd.concat(tables.values(), ignore_index=True)
    cols = [c for c in ["temperature_K", "rhi", "SAC", "ISSR", "PCR"] if c in combined]
    nan_counts = combined[cols].isna().sum()
    if "fuel_flow_kg_s" in combined:  # the final point is NaN by design; check the rest
        inner = pd.concat([t["fuel_flow_kg_s"].iloc[:-1] for t in tables.values()])
        nan_counts["fuel_flow_kg_s (excl. final point)"] = int(inner.isna().sum())
    if nan_counts.sum():
        raise ValueError(f"NaNs in route results - inspect before interpreting:\n{nan_counts}")
    print(f"Evaluated {len(tables)} routes, no missing values.")
    return tables


def route_metrics(tables, specs):
    """One row of metrics per route. specs = {name: {extra columns}}."""
    rows = []
    for name, df in tables.items():
        pcr = df["PCR"] > 0
        row = {
            "route": name,
            **specs[name],
            "length_km": df["leg_km"].sum(),
            "duration_min": (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 60,
            "pcr_km": df.loc[pcr, "leg_km"].sum(),
            "rhi_max": df["rhi"].max(),
            "points_rhi_within_0.05": int(((df["rhi"] - 1).abs() < 0.05).sum()),
        }
        if "fuel_flow_kg_s" in df:
            # segment fuel = fuel flow at segment start x segment duration
            t = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy()
            ff = df["fuel_flow_kg_s"].to_numpy()[:-1]
            if np.isnan(ff).any():  # never silently skip a missing segment
                raise ValueError(f"{name}: NaN fuel flow before the final point")
            row["fuel_kg"] = float(np.sum(ff * np.diff(t)))
        rows.append(row)
    m = pd.DataFrame(rows)
    m["pcr_fraction"] = m["pcr_km"] / m["length_km"]
    if "fuel_kg" in m:
        m["co2_kg"] = EI_CO2 * m["fuel_kg"]
    return m


def pareto_mask(m, cost="fuel_kg", harm="pcr_km"):
    """True for routes where neither cost nor harm can fall without the other rising."""
    mask = pd.Series(False, index=m.index)
    best = np.inf
    for i in m.sort_values([cost, harm]).index:
        if m.at[i, harm] < best - 1e-9:
            mask[i] = True
            best = m.at[i, harm]
    return mask