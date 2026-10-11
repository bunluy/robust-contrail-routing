"""load_era5_checked: a cached hour full of NaNs is detected, deleted and rebuilt."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pycontrails import MetDataset

import contrail_routing.met as met_module

TIMES = pd.date_range("2022-03-01", periods=3, freq="h")


class FakeERA5:
    """Stands in for ERA5ARCO: one netCDF file per hour in a temporary cache."""
    cache_dir = None
    rebuilt = []

    def __init__(self, time, variables, pressure_levels):
        self.levels = np.asarray(pressure_levels, dtype=float)

    def create_cachepath(self, t):
        return str(self.cache_dir / f"{pd.Timestamp(t):%Y%m%d%H}.nc")

    def _write_hour(self, t, value):
        coords = {"longitude": np.arange(-2.0, 2.1, 1.0), "latitude": np.arange(48.0, 52.1, 1.0),
                  "level": self.levels, "time": [np.datetime64(t)]}
        shape = tuple(len(v) for v in coords.values())
        data = np.full(shape, value)            # all NaN for a corrupt hour
        dims = tuple(coords)
        xr.Dataset({"air_temperature": (dims, data + 220.0),
                    "specific_humidity": (dims, data + 1e-5)},
                   coords=coords).to_netcdf(self.create_cachepath(t))

    def open_metdataset(self):
        for t in TIMES:
            if not met_module.Path(self.create_cachepath(t)).exists():
                FakeERA5.rebuilt.append(t)
                self._write_hour(t, 0.0)
        ds = xr.open_mfdataset([self.create_cachepath(t) for t in TIMES])
        return MetDataset(ds)


@pytest.fixture
def fake_era5(tmp_path, monkeypatch):
    FakeERA5.cache_dir, FakeERA5.rebuilt = tmp_path, []
    monkeypatch.setattr(met_module, "ERA5ARCO", FakeERA5)
    era5 = FakeERA5(None, None, [200, 250])
    for i, t in enumerate(TIMES):
        era5._write_hour(t, np.nan if i == 1 else 0.0)   # the middle hour is corrupt
    return FakeERA5


def test_bad_hour_is_detected_and_rebuilt(fake_era5):
    met, report = met_module.load_era5_checked(None, [200, 250], (-2, 2), (48, 52))
    assert report.loc[0, "bad_hours"] == ["2022-03-01 01:00"]
    assert report.loc[1, "bad_hours"] == []
    assert fake_era5.rebuilt == [TIMES[1]]
    assert not met.data["specific_humidity"].isnull().any()
