"""Loading, checking and repairing ERA5 meteorology."""
import gc
from pathlib import Path

import pandas as pd
from pycontrails.datalib.ecmwf import ERA5ARCO

VARIABLES = ("air_temperature", "specific_humidity")


def load_era5(time, pressure_levels, variables=VARIABLES):
    """
    Open ERA5 (ARCO) on pressure levels through pycontrails' disk cache.

    Keep the cache enabled: converting ERA5's 137 model levels to pressure
    levels without it is done in memory and exceeds a typical laptop's RAM.
    Prefer load_era5_checked, which also detects and repairs bad cached hours.
    """
    era5 = ERA5ARCO(time=time, variables=list(variables),
                    pressure_levels=list(pressure_levels))
    return era5.open_metdataset()


def nan_count_by_hour(met, lon_range, lat_range):
    """Number of missing values per hour in a lon/lat box (does not raise)."""
    rows = []
    for t in met.data["time"].values:
        b = met.data.sel(time=t, longitude=slice(*lon_range),
                         latitude=slice(*lat_range))
        rows.append({
            "time": pd.Timestamp(t),
            "air_temperature_nan": int(b["air_temperature"].isnull().sum().compute()),
            "specific_humidity_nan": int(b["specific_humidity"].isnull().sum().compute()),
        })
    return pd.DataFrame(rows)


def check_met_box(met, lon_range, lat_range):
    """Count NaNs per hour in a lon/lat box; raise if any are present."""
    df = nan_count_by_hour(met, lon_range, lat_range)
    n_nan = int(df[["air_temperature_nan", "specific_humidity_nan"]].values.sum())
    if n_nan:
        raise ValueError(f"Met data contains {n_nan} NaNs in this box:\n{df}")
    return df


def delete_cached_hours(time, pressure_levels, hours, variables=VARIABLES):
    """
    Delete the cache files for specific hours so pycontrails rebuilds them.
    On Windows a file cannot be deleted while it is open: if this raises
    PermissionError, restart the kernel and call it before loading any data.
    """
    era5 = ERA5ARCO(time=time, variables=list(variables),
                    pressure_levels=list(pressure_levels))
    deleted = []
    for t in hours:
        path = Path(era5.create_cachepath(pd.Timestamp(t).to_pydatetime()))
        if path.exists():
            path.unlink()
            deleted.append(path.name)
    return deleted


def load_era5_checked(time, pressure_levels, lon_range, lat_range,
                      variables=VARIABLES, max_repairs=2):
    """
    Load ERA5 like load_era5, then check every hour in the lon/lat box for
    missing values. Hours that came back empty (a conversion that failed and
    was cached) are deleted from the cache and rebuilt, up to max_repairs
    times. Returns (met, report) where report lists each check and repair.
    Raises if missing values remain after the last attempt.
    """
    era5 = ERA5ARCO(time=time, variables=list(variables),
                    pressure_levels=list(pressure_levels))
    report = []
    for attempt in range(max_repairs + 1):
        met = era5.open_metdataset()
        counts = nan_count_by_hour(met, lon_range, lat_range)
        bad = counts.loc[counts[["air_temperature_nan", "specific_humidity_nan"]].sum(axis=1) > 0, "time"]
        report.append({"attempt": attempt, "bad_hours": [t.strftime("%Y-%m-%d %H:%M") for t in bad]})
        if bad.empty:
            print(f"ERA5 {list(pressure_levels)} hPa: all {len(counts)} hours complete"
                  + (f" after {attempt} repair(s)." if attempt else "."))
            return met, pd.DataFrame(report)
        if attempt == max_repairs:
            raise ValueError(f"Missing values remain after {max_repairs} repairs:\n{counts}")

        print(f"Hours with missing data: {report[-1]['bad_hours']}; deleting their cache files and rebuilding ...")
        met.data.close()          # release the files so Windows allows deletion
        del met
        gc.collect()
        try:
            for t in bad:
                Path(era5.create_cachepath(t.to_pydatetime())).unlink(missing_ok=True)
        except PermissionError as err:
            raise PermissionError(
                "A cache file is still open. Restart the kernel, then call "
                "delete_cached_hours(time, pressure_levels, hours) before loading any data."
            ) from err


def load_region(met, lon_range, lat_range, variables=VARIABLES):
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
