"""Loading and checking ERA5 meteorology."""
import pandas as pd
from pycontrails.datalib.ecmwf import ERA5ARCO


def load_era5(time, pressure_levels,
              variables=("air_temperature", "specific_humidity")):
    """
    Open ERA5 (ARCO) on pressure levels through pycontrails' disk cache.

    Keep the cache enabled: converting ERA5's 137 model levels to pressure
    levels without it is done in memory and exceeds a typical laptop's RAM.
    """
    era5 = ERA5ARCO(time=time, variables=list(variables),
                    pressure_levels=list(pressure_levels))
    return era5.open_metdataset()


def check_met_box(met, lon_range, lat_range):
    """Count NaNs per hour in a lon/lat box; raise if any are present."""
    rows = []
    for t in met.data["time"].values:
        b = met.data.sel(time=t, longitude=slice(*lon_range),
                         latitude=slice(*lat_range))
        rows.append({
            "time": pd.Timestamp(t),
            "air_temperature_nan": int(b["air_temperature"].isnull().sum().compute()),
            "specific_humidity_nan": int(b["specific_humidity"].isnull().sum().compute()),
        })
    df = pd.DataFrame(rows)
    n_nan = int(df[["air_temperature_nan", "specific_humidity_nan"]].values.sum())
    if n_nan:
        raise ValueError(f"Met data contains {n_nan} NaNs in this box:\n{df}")
    return df


def load_region(met, lon_range, lat_range,
                variables=("air_temperature", "specific_humidity")):
    """
    Cut a lon/lat region out of a (lazy, global) MetDataset and load it into
    memory as a new MetDataset. Much faster to evaluate many routes against,
    and the starting point for perturbed scenarios.
    """
    from pycontrails import MetDataset

    ds = (met.data[list(variables)]
          .sel(longitude=slice(*lon_range), latitude=slice(*lat_range))
          .load())
    return MetDataset(ds)
