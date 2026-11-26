# robust-contrail-routing

[![tests](https://github.com/bunluy/robust-contrail-routing/actions/workflows/tests.yml/badge.svg)](https://github.com/bunluy/robust-contrail-routing/actions/workflows/tests.yml)

**How should an aircraft's cruise level and path be chosen to avoid creating persistent contrails, when the weather data behind that choice is uncertain, and what does planning for that uncertainty cost?**

**In brief**
- **Method:** 45 candidate routes for an A320-class night flight, evaluated on 24 nights of 2022 with ERA5 reanalysis, the pycontrails contrail model and the Poll–Schumann fuel model; route choices that still work when the weather data is wrong; and ERA5 itself tested against 786,845 humidity measurements made on board airliners (IAGOS).
- **Results:** cutting contrail-forming flight by at least 80% cost a median 1.2% extra fuel (0.05–4.8%, the 10 nights where it was needed and possible). ERA5 found 47% of the measured ice-supersaturated air (false alarm ratio 0.46). At 195–270 hPa, a one-number correction tested on unseen months raised the share found from 46% to 71% (false alarm ratio from 0.46 to 0.50), and it changed 79 of 288 route choices. Over 639 measured flights, a calibrated probability got the total contrail-forming distance right (96% of measured), where plain ERA5 found 85%, though no method was more accurate for single flights.
- **Biggest caveat:** a simplified world: one synthetic city pair, cruise only, no wind, reanalysis rather than forecast data, and distance in contrail-forming air as a stand-in for climate impact.
- **Who did what:** a self-directed learning project. The first two stages are my own code; later stages were written largely by an AI assistant to my direction. The questions, the decisions, and every run and check are mine ([details](#how-this-was-made)).

> **Status:** Sessions 1–14 complete; next, a short technical report. See [Roadmap](#roadmap).

---

## Main findings

*Case study: a ~1,800 km night flight from (−10°, 45°) to (10°, 55°), A320, constant true airspeed 230 m/s, no wind, 45 candidate routes. Repeated on 24 nights of 2022 (the 1st and 15th of each month, 00:00 UTC), a rule fixed before any results were seen; 2022-03-01 is studied in the most detail.*

**1. Persistent-contrail conditions at cruise levels are common, and their height changes from night to night.** Every night had persistent-contrail (PCR) conditions on at least one route. On all six summer nights they reached FL370 or higher; on no spring night did they rise above FL360; winter and autumn were mixed. Climbing is therefore not a universal fix: the great-circle route at FL390 was itself exposed on 6 nights.

![Exposure on the great-circle route by flight level and night](results/figures/session8_exposure_by_night.png)

*Distance flown in persistent-contrail conditions on the great-circle route, by flight level (rows) and night (columns).*

**2. Aircraft weight decides how often there is a trade-off.** Heavier aircraft cruise most efficiently lower down, closer to or inside the moist layer:

| Start-of-cruise mass | Most fuel-efficient level | Nights its route is exposed | ...or would be under a 150–600 m error in the layer's height |
|---|---|---|---|
| 56 t | FL390 | 6 of 24 | 11 of 24 |
| ≈64 t (model estimate) | FL370–FL380 | 11 of 24 | 17 of 24 |
| 68 t | FL360–FL370 | 12 of 24 | 19 of 24 |
| 72 t (near maximum) | FL350–FL360 | 17 of 24 | 19 of 24 |

**3. Avoidance is often affordable, but its cost varies widely between nights.** At the model-estimate mass, cutting exposure by at least 80% cost a median 1.2% extra fuel (0.05–4.8%) on the 10 nights where the minimum-fuel route was exposed and such a cut was possible (not possible on 1 more). With the aircraft held at or below FL360, it cost a median 4.3% (1.6–13.1%, 12 nights; not possible on 3 more). The single-night figure that started this (an 83% cut for 1.6% on 2022-03-01, under the same FL360 ceiling) was at the cheap end.

![Contrail exposure vs extra fuel, 2022-03-01 (Session 7 version: corrected fuel model, 5 ERA5 pressure levels)](results/figures/trade_off_fronts.png)

*Contrail exposure against extra fuel for the 45 routes on 2022-03-01 (the Session 7 version of this figure: corrected fuel model, 5 ERA5 pressure levels; later sessions use 10).*

**4. Uncertainty in the *height* of the moist layer changes route choices on most nights.** With the weather field shifted up or down by 150–600 m, deterministic, worst-case and minimum-regret rules chose different routes in 142 of 288 cases, on 19 of 24 nights. Where the worst-case choice differed from the deterministic one (135 cases), it typically spent about 20 kg more fuel to cut the worst-case contrail distance by about 110 km (medians). On 2022-03-01, height errors changed decisions far more often (9 of 12 cases) than uniform humidity errors (1 of 9) or horizontal and timing errors (1 of 18).

**5. A robust plan is usually cheap, but not always available.** The worst-case choice cost no extra fuel on a median night for every mass, and at most 1.6–6.3%. A route clear of contrail conditions under every height error existed on only 16 of 24 nights (13 for a 72 t aircraft); when it existed it cost a median 0–1.5% extra fuel, up to about 7%.

![Exposure of the minimum-fuel route and cost of a robust route, by mass](results/figures/session8_mass_and_robust_cost.png)

*Left: contrail exposure of the minimum-fuel route on each night, by aircraft mass. Right: extra fuel for the cheapest route that is clear in every height scenario (crosses: no such route).*

**6. A free-form route planner rarely beats the fixed route family.** A planner choosing any path through a grid of 25 lateral offsets × 9 flight levels × 60 stages found nothing better than the best of the 45 fixed routes on most nights (median gain 0). Freedom to change level helped heavier aircraft through step climbs (72 t: median 40 kg at λ = 1); large gains, up to ~1,900 kg fuel-equivalent at λ = 10, came from a few nights such as 1 January, when moist air sat at every level and only a long detour helped.

![Planned paths on 2022-01-01 and the planner's gain on each night](results/figures/session9_planner.png)

*Left: planned paths on 2022-01-01. Right: the planner's gain over the best fixed route on each night (kg fuel-equivalent).*

**7. Planning against height errors beats the fixed routes, by small margins, and robustness is usually free.** An exact search for the path whose worst case over the seven height scenarios is best (C++ core) beat the fixed routes' worst-case choice at λ = 1 on 16–18 of 24 nights for 64–72 t aircraft (median 23–45 kg fuel-equivalent, under 1% of trip fuel) and on 7 nights for 56 t; it was worse on 1–2 nights per mass, by up to 76 kg. Against an ordinary plan for the unshifted ERA5 field, it cut worst-case contrail distance in 176 of 288 cases (median 55–117 km for 64–72 t) for a median 0.12% extra fuel, 69 of them at no extra fuel. 97% of the searches were solved exactly, half of them proved optimal by bounds alone; 16 of 576 hit the 2-minute limit and returned a path with a certified gap of 1.7–13.9% of fuel. "Exact" means exact on the planner's grid and its own cost estimate, and the search plans against height errors, which Session 12 later found are not ERA5's main weakness.

![Robust planned paths against the fixed routes, and the cost of robustness](results/figures/session11_robust_planner.png)

*Left: gain of the worst-case planned path over the fixed routes' worst-case choice on each night, λ = 1 (mass codes: light_56t = 56 t, model_default ≈ 64 t, heavy_68t = 68 t, very_heavy_72t = 72 t). Right: extra fuel against the cut in worst-case contrail distance, worst-case plans vs ordinary plans.*

**8. Measured on board airliners, ERA5's main weakness is humidity, not height.** Against 786,845 measurements from 833 IAGOS flights over the study region in 2022 (175–300 hPa), ERA5 found 47% of the ice-supersaturated air, and 46% of what it showed was not measured (equitable threat score 0.29; best in winter, 0.44). Where the air was supersaturated, ERA5's relative humidity over ice was a median 0.99 against a measured 1.10: usually close, but just below saturation. Moving the ERA5 field up or down did not improve the agreement on average; no shift was the most common best fit (30–39% of 10-minute blocks), while shifts of about 600 m fitted best in at most about 30% (an upper bound: that share also includes moist layers ERA5 shows that are not there). So the height scenarios of Sessions 7–11 are realistic in size, but equal weights overstate the 150–300 m errors, and the results point to a humidity correction mattering more than a height correction.

![ERA5 against IAGOS: scores by height shift, best-fitting shift, and humidity](results/figures/session12_era5_vs_iagos.png)

*Left: hit rate and false alarm ratio of ERA5 ice supersaturation when the ERA5 field is moved up or down. Middle: which height shift agrees best with the measurements, against the equal weights assumed before. Right: ERA5 against measured relative humidity over ice.*

**9. A one-number humidity correction finds far more of the supersaturated air and changes a quarter of route choices.** Tested on months it was not fitted to, calling ERA5 air supersaturated above 91% relative humidity over ice (instead of 100%) found 71% of the measured ice-supersaturated air instead of 46%, with the false alarm ratio rising only from 0.46 to 0.50 (ETS 0.35 against 0.28). The fitted value was 0.91–0.93 whichever month was left out, and lies inside the 85–95% range reported by Hildebrandt et al. (2026) for 2011–2022. A logistic model gave a probability that is reliable up to about 60% but no better yes/no answers. With the correction, contrail distance over the 45 routes rose 62%, and the lowest-cost route changed in 79 of 288 night × mass × λ cases (up to 11 of 24 nights for a 72 t aircraft). The plain-ERA5 choice would have flown up to 690 km through contrail conditions it did not see, while switching usually cost no extra fuel.

![Corrections on unseen months, reliability of the probability, and what the plain-ERA5 choice misses](results/figures/session13_correcting_humidity.png)

*Left: skill of four ways of calling ERA5 air supersaturated, on unseen months. Middle: reliability of the probability (diagonal = perfect). Right: contrail distance the plain-ERA5 route choice misses if the corrected humidity is right, λ = 1 (mass codes as above).*

**10. A calibrated probability is right on average, and planning on it changes most heavy-aircraft routes cheaply.** On 639 IAGOS flights, the probability's expected contrail-forming distance totalled 96% of what was measured (interval 87–108%), where plain ERA5 gave 85% and the 0.91 threshold 143%; for single flights none of the three was more accurate (mean error 60–66 km per flight, rank correlation 0.66–0.70). Planning on the expected distance, with eastbound flight levels (2,000 ft steps) and at most two level changes, changed 158 of 288 route choices, mostly for 64–72 t aircraft at λ = 1–10, cutting a median 21–94 km of expected contrail-forming flight for a median 2–58 kg of extra fuel. In 189 of 288 cases the plain-ERA5 route looked clean by its own measure but not by the probability's. The gain is reliable on average, over many flights, not for any single flight.

![Predicted vs measured contrail distance on IAGOS flights, per-flight scores, and planning on the expected distance](results/figures/session14_planning_with_uncertainty.png)

*Left: predicted against measured km in contrail-forming air for each IAGOS flight (cruise, 195–270 hPa), plain ERA5 and expected distance. Middle: per-flight scores with 95% intervals. Right: expected contrail distance cut by planning on the expected distance instead of plain ERA5, against extra fuel, λ = 1, each night and mass.*

These results cover one city pair, one departure time and one year, and the corrections are fitted to four aircraft over one region.

---

## Why this matters

Persistent contrails form in thin, patchy layers of cold, ice-supersaturated air and can spread into cirrus-like clouds lasting hours. Their warming effect is estimated to be comparable to that of aviation's CO₂ (Lee et al., 2021), which makes small altitude or path changes a potentially cheap way to reduce aviation's climate impact (Teoh et al., 2020).

Two things make this an engineering decision problem rather than a physics one:

1. **Avoidance has a cost:** more distance, more fuel, more CO₂.
2. **The atmosphere is uncertain:** weather models struggle to locate ice-supersaturated air precisely, so a route that avoids contrails on paper may not do so in reality.

Operational trials have shown avoidance can work. EUROCONTROL's Maastricht centre used vertical clearances of up to 2,000 ft and identified the precision of meteorological tools as a key challenge ([EUROCONTROL, 2021](https://www.eurocontrol.int/press-release/mitigating-climate-impact-non-co2-emissions); Sausen et al., 2024), and Google Research, Breakthrough Energy and American Airlines reported large reductions in observed contrails ([Google Research](https://sites.research.google/gr/contrails/)). This project studies the decision those trials face, on a small and fully transparent scale.

---

## Method

```
candidate route (lateral offset, flight level)
  → ERA5 temperature and humidity (10 pressure levels, 175–300 hPa) along the path
  → pycontrails PCR model
       Schmidt–Appleman criterion: can a contrail form?
       ice supersaturation (RHi > 1): can it persist?
  → distance flown in persistent-contrail (PCR) conditions
  → Poll–Schumann model: fuel burn for the route
  → objective J = fuel + λ × PCR distance, over weather-error scenarios
  → deterministic vs worst-case vs minimax-regret route choice
  → repeated on 24 nights
  → route planner: dynamic programming over a stage × offset × flight-level grid,
    every planned path re-flown through the steps above
  → robust planner: exact search for the path that is best in the worst of the
    weather scenarios (C++ core, called from Python through pybind11), on 24 nights
  → ERA5 checked against IAGOS: the same steps evaluated at 786,845 points measured
    on board airliners, in the field as given and in every height-shifted field
  → humidity corrected against IAGOS, and routes planned on the expected contrail
    distance, checked against the distance measured on real flights
```

- **Routes:** the great circle and smooth lateral deviations of ±150 and ±300 km, at FL310–FL390 (45 routes). The set was fixed before any results were seen, so routes are not tuned to the data.

![Humidity over ice at three cruise heights on 2022-03-01, 01:00 UTC, with the five lateral paths of the route family; black contours mark ice saturation](results/figures/session3_candidate_routes.png)

*Relative humidity over ice at three cruise heights on 2022-03-01, 01:00 UTC, with the five lateral paths of the route family; black contours mark ice saturation (RHi = 1).*

- **Uncertainty scenarios:** on 2022-03-01, 18 scenarios: humidity scaled by −5% to +11%; the whole weather field displaced 50–100 km sideways, 100 km along-track or 1 h in time; and moved 150–600 m up or down. On all 24 nights, the vertical shifts (the ones that changed decisions).
- **λ** is the fuel (kg) an operator would spend to avoid 1 km of contrail-forming flight. It is kept as an explicit setting (0.1, 1, 10 kg/km) because converting contrail distance to climate impact needs a contrail climate model, which is future work.
- **Robust search:** labels (partial paths) that are not beaten on fuel and on contrail distance in every scenario, pruned by a lower bound per scenario and a Lagrangian bound (a weighted average of the scenarios never exceeds the worst one). When the bounds meet, the path is proved optimal without searching; when a time limit is reached, the best path found is returned with a certified gap. A pure-Python copy of the same algorithm checks the C++ step for step.
- **IAGOS comparison:** IAGOS-CORE L2 relative humidity over ice and temperature (every 4 s, quality-flagged good and final) inside the study region at 175–300 hPa, 2022. ERA5 is matched to each point through the route evaluation's own loader, levels, interpolation and PCR model; scores use whole flights as the independent units (bootstrap intervals). The rules were fixed in the notebook before any comparison was made.
- **Humidity correction:** four ways of calling ERA5 air ice-supersaturated (RHi > 1, RHi > 0.97, a fitted threshold, and a logistic probability from ERA5's humidity 0–600 m above and below the point, temperature and pressure), each tested on months it was not fitted to (leave-one-month-out), with paired comparisons over whole flights. The fitted threshold is applied to the routes through pycontrails' constant humidity scaling.
- **Planning with the probability:** the Session 9 planner minimises fuel + λ × expected contrail distance, the expected distance being ERA5's Schmidt–Appleman criterion times the Session 13 probability; restricted to FL330–FL390 (eastbound levels, 2,000 ft steps) with at most two level changes. The expected distance is checked first against the distance measured on IAGOS flights.
- **Contrail metric:** distance in PCR conditions is a proxy for climate impact. It is more defensible here than in general because the flights are at night, when persistent contrails warm.

---

## Checks and corrections

The project's own checks caught several errors; each is documented in the notebook where it was found, and the evidence is kept.

| Problem | How it was caught | Fix |
|---|---|---|
| A cached ERA5 hour came back empty (twice) | Missing-value check before every evaluation | Cache file rebuilt; now automatic (`load_era5_checked`) |
| Relative humidity over water was read as if over ice | Recomputed RHi did not match the ISSR flag | RHi computed explicitly and checked against ISSR |
| First synthetic flight was ~3× too slow | Sanity check of speed | Realistic 230 m/s; exposure changed from 317 to 800 km |
| "Direct" route was not the shortest path | Left/right route lengths were asymmetric | Routes rebuilt around the great circle |
| Fuel model used a fixed engine efficiency left by the contrail model | New fuel code disagreed with the old by up to 171 kg | Fixed; Sessions 4–6 decisions recomputed (none changed) |
| Vertical shift also changed humidity | Physically implausible result | Relative humidity now moves with the air; covered by a test |
| Route planner underestimated contrail distance by up to 308 km | Every planned path re-flown through the full pipeline | Planner re-times the weather along its own path and samples each step at 3 points; worst error now 70 km |
| Robust search blew up for heavy aircraft (millions of partial paths) | Search size and time logged for every search on real data | Tighter joint and Lagrangian bounds (a stress case went from over 2 minutes to 0.01 s); a time limit that returns a certified gap instead of failing |

The IAGOS matching is checked by temperature: ERA5 and the measured temperature agree to −0.48 K on average (sd 0.80 K) in every season, which rules out a mismatch in time, position or pressure before any humidity result is read.

Each new step is also checked against the previous one: the multi-night code reproduces the single-night results exactly before it is run on other nights.

The package has **60 automated tests (143 test cases, counting repeated runs with different inputs)**, including PCR on artificial dry and supersaturated atmospheres, route geometry, the decision rules, the cache repair, the route planner checked against brute force over every possible path on small grids, and the C++ robust search checked against brute force, against a pure-Python version of the same algorithm, and for the validity of the gap it reports when stopped early, the IAGOS reading, quality filters and ERA5 matching on artificial files and atmospheres, the calibration tools (including a check that a month's own measurements can never affect its out-of-month prediction), and the probability-based planning (including the level-change limit against brute force). They run on GitHub on every push (badge at the top); locally, run `python -m pytest`.

---

## Limitations

- One city pair (synthetic endpoints), one departure time (00:00 UTC) and 24 nights of one year: too few for firm seasonal conclusions.
- Cruise only: the extra fuel to climb to higher levels is not included, which favours high levels.
- Constant true airspeed rather than constant Mach number; no wind.
- ERA5 is a reanalysis, the best reconstruction of the weather after the fact, not the forecast a flight planner would have. Forecasts differ from it: ice supersaturation in forecasts and in ERA5 agrees only moderately point by point (an ETS of about 0.4 at short lead times, falling by about 0.1 per day; Dean et al., 2025), so these results are a best case for the weather data.
- The planners of Sessions 9–11 may use every 1,000 ft level and change level often (91 of 576 robust plans change level five times or more in a two-hour cruise, up to 16 times), and charge nothing for descents; the Session 14 planner uses eastbound levels only, with at most two changes. In practice eastbound flights are normally allocated alternate levels, level changes are limited, and the 2021 Maastricht trial used changes of up to 2,000 ft (Sausen et al., 2024).
- The route planner's grid is limited to ±300 km, and its fuel per km does not fall as the aircraft gets lighter. Robust searches are exact on that grid only; 16 of 576 stopped at the time limit with a certified gap of up to 13.9% of fuel.
- Planned paths optimise the planner's own estimate, which differs from the full pipeline by up to 70 km of contrail distance; at λ = 10 this caused some of the cases where a planned path lost to a fixed route.
- Scenario sizes are sensitivity cases weighted equally. The IAGOS comparison (Session 12) supports their range but not equal weights, and suggests ERA5's humidity errors near saturation matter more than its height errors; the routing does not yet use these findings. Only the vertical scenarios were repeated on all 24 nights.
- The IAGOS sample comes from 4 aircraft based at Frankfurt and Paris, with few flights in March and April; measured humidity has its own uncertainty (about ±0.13 near saturation), and climbs and descents disagree in a way that suggests a sensor effect.
- PCR distance is a proxy for climate impact, not a radiative-forcing calculation, and it counts every kilometre equally, whereas over Japan 2.2% of flights caused 80% of contrail energy forcing (Teoh et al., 2020). λ is a setting, not derived from climate physics.
- ERA5 humidity is used unscaled in Sessions 1–11. Session 13 shows a measured correction (RHi > 0.91) changes about a quarter of route choices; it was fitted at about FL340–FL390 and assumes one threshold for all seasons, and with the threshold chosen for the best ETS it marks more air as supersaturated than was measured (17% against 12%), so its effect on routes is an upper estimate.

---

## Roadmap

- [x] Persistent-contrail modelling baseline and along-track variation (Sessions 1–2)
- [x] Candidate routes and altitude sweep (Session 3)
- [x] Fuel model, Pareto fronts and routing objective (Session 4)
- [x] Robustness to humidity, position and timing errors (Sessions 5–6)
- [x] Vertical resolution, vertical errors and aircraft mass (Session 7)
- [x] The same analysis on 24 nights of 2022 (Session 8)
- [x] Route planner over the airspace, compared with the fixed route set (Session 9)
- [x] Robust planner: an exact search core in C++ (pybind11), validated against brute force and a pure-Python version (Session 10)
- [x] Robust planned routes under vertical weather errors on 24 nights, and a benchmark against the pure-Python version (Session 11)
- [x] How often does ERA5 miss ice supersaturation? A comparison with humidity measured on board airliners (IAGOS) (Session 12)
- [x] A statistical correction of ERA5 humidity giving a calibrated probability of ice supersaturation, compared with existing humidity scalings, and its effect on route choices (Session 13)
- [x] Route planning on the measured probability of contrail conditions, checked against the distance measured on IAGOS flights (Session 14)
- [ ] Short technical report (Session 15)

**Future work, beyond this project:** a contrail climate model (pycontrails' CoCiP) to turn λ into a physical quantity; ERA5's 10-member ensemble; wind, climb and real city pairs.

---

## Repository structure

```
src/contrail_routing/      package: geometry, met (ERA5 loading and repair), evaluate (PCR),
                           performance (fuel), scenarios, decisions, planner, robust,
                           iagos (reading IAGOS and matching ERA5), calibration (humidity
                           corrections tested on unseen months), probabilistic (expected
                           contrail distance for checking and planning), config
cpp/                       C++ core of the robust search (robust_search.cpp) and its build files
tests/                     automated tests (pytest)
notebooks/
  01_baseline_and_along_track_pcr.ipynb   Sessions 1-2
  02_candidate_routes.ipynb               Session 3
  03_routing_objective.ipynb              Session 4
  04_humidity_uncertainty.ipynb           Session 5
  05_position_errors.ipynb                Session 6
  06_vertical_and_mass.ipynb              Session 7 (and corrected Session 4-6 fuel)
  07_multiple_dates.ipynb                 Session 8: 24 nights of 2022
  08_route_planner.ipynb                  Session 9: route planner vs the fixed route set
  09_robust_search.ipynb                  Session 10: robust search, checks and speed on one night
  10_robust_24_nights.ipynb               Session 11: robust planned routes on 24 nights
  11_era5_vs_iagos.ipynb                  Session 12: ERA5 checked against IAGOS measurements
  12_correcting_era5_humidity.ipynb       Session 13: humidity corrections and their effect on routes
  13_planning_with_measured_uncertainty.ipynb  Session 14: the probability on real flights, and planning on it
scripts/download_era5_dates.py            downloads and checks ERA5 for the 24 nights
scripts/match_era5_to_iagos.py            matches ERA5 to every IAGOS measurement (resumable)
results/                   CSV outputs per session (IAGOS: summary tables only); figures/ holds all figures
```

## Reproducing

Developed with Python 3.13 on Windows; `requirements.txt` lists the key packages and versions (`requirements-lock-windows.txt` is the exact environment). Building the C++ core needs a C++ compiler: on Windows, the Microsoft C++ Build Tools ("Desktop development with C++"); on Linux or macOS, the system compiler. Without it everything else still runs, and the robust search falls back to its (much slower) pure-Python version.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install --no-build-isolation ./cpp
.venv\Scripts\python.exe -m pytest
```

The first ERA5 download per configuration takes 10–20 minutes and is cached on disk afterwards; for notebooks 07–10, run `scripts/download_era5_dates.py` first (about 2 hours and 13 GB for the 24 nights). Notebook 10 then takes about 75 minutes and saves each night as it finishes, so it can be stopped and resumed. Keep the cache enabled: without it, converting ERA5's 137 model levels to pressure levels happens in memory and exceeds a typical laptop's RAM.

For notebook 11, IAGOS data are needed; they are not included here, to keep the repository small (about 650 MB for 2022). Register at the [IAGOS Data Portal](https://iagos.aeris-data.fr/), then download IAGOS-CORE L2 time series in NetCDF for 2022-01-01 to 2023-01-01 over 40–60°N, 20°W–20°E (the optional ECMWF ancillary data must be left unticked, or the download fails). Put the files in `iagos_data` in your home folder (or set `IAGOS_DATA`) and run `scripts/match_era5_to_iagos.py <that folder>`: about 24 hours, resumable, deleting each ERA5 hour once matched. Notebooks 12 and 13 then use those matched points and the cached ERA5 of the 24 study nights (a few minutes each).

---

## Related work

This project is a small, transparent case study, not a new method: each of its steps has been studied before, mostly at larger scale.

- **ERA5 against airliner humidity.** Gierens et al. (2020) found contrail formation predictable from ERA5 but persistence much less so; Reutter et al. (2020) found ERA-Interim underestimates ice supersaturation in number and strength; Hildebrandt et al. (2026), over 2011–2022 and eight regions, found an ETS of 0.2–0.4 and that lowering ERA5's threshold helps most between 85% and 95%, with the best value varying by region, season and distance from the tropopause; Driver et al. (2025) showed the errors follow the weather situation around depressions, and that a 90% threshold roughly doubles the share of measured ice supersaturation found for a few points of precision.
- **Correcting ERA5's humidity.** Antonopoulos et al. (2026) trained a machine-learning correction on IAGOS data over the North Atlantic in 2022, raising ETS from 0.36 to 0.44.
- **Planning routes under weather uncertainty.** Simorgh et al. (2023, 2024) planned climate-optimal trajectories that are robust to forecast uncertainty from weather ensembles; Hanst et al. (2025) built ensemble forecasts of ice supersaturation, verified against radiosondes and IAGOS, that have been used to reroute flights in an avoidance trial.
- **Risk and uncertainty in avoidance decisions.** Platt et al. (2024) propagated humidity and model uncertainty through a contrail model and found per-flight predictions still clearly better than climatology; Borella et al. (2026) showed that, with ensemble forecasts, 55% of reroutings planned on a best estimate carry more than a 5% risk of harming the climate, and argued that risk belongs in avoidance decisions.
- **Avoidance in practice.** The 2021 Maastricht trial changed flight levels by up to 2,000 ft and found avoidance successful on average (Sausen et al., 2024).

Where this project overlaps with these studies, its results agree: ERA5's ETS of about 0.3, a best threshold near 0.9, and lowered thresholds finding far more supersaturated air at a small cost in false alarms. What it adds is one end-to-end chain, from measured humidity to the route a flight would choose, with every step checked and its limits stated.

---

## References

- Borella, A., Steer, C., Bellouin, N., & Boucher, O. (2026). Concept of risk-aware contrail avoidance strategies. *Journal of Environmentally Compatible Air Transport System*, 1, 3. https://jecats.copernicus.org/articles/1/3/2026/
- Antonopoulos, M., Juvin-Quarroz, J., & Boucher, O. (2026). Technical note: Hybrid machine learning model for bias correction of UTLS relative humidity against IAGOS observations in ERA5 reanalysis. *Atmospheric Chemistry and Physics*. https://doi.org/10.5194/acp-26-4771-2026
- Driver, O. G. A., Stettler, M. E. J., & Gryspeerdt, E. (2025). The ice supersaturation biases limiting contrail modelling are structured around extratropical depressions. *Atmospheric Chemistry and Physics*. https://doi.org/10.5194/acp-25-16411-2025
- Dean, T. R., Abbott, T. H., Engberg, Z., Masson, N., Teoh, R., Itcovitz, J. P., Stettler, M. E. J., & Shapiro, M. L. (2025). Impact of forecast stability on navigational contrail avoidance. arXiv:2507.13529 (preprint). https://arxiv.org/abs/2507.13529
- Gierens, K., Matthes, S., & Rohs, S. (2020). How well can persistent contrails be predicted? *Aerospace*, 7(12), 169. https://doi.org/10.3390/aerospace7120169
- Hanst, M., Köhler, C. G., Seifert, A., & Schlemmer, L. (2025). Predicting ice supersaturation for contrail avoidance: ensemble forecasting using ICON with two-moment ice microphysics. *Atmospheric Chemistry and Physics*, 25, 17253–17274. https://doi.org/10.5194/acp-25-17253-2025
- Hersbach, H. et al. (2020). The ERA5 global reanalysis. *Quarterly Journal of the Royal Meteorological Society*, 146, 1999–2049.
- Hildebrandt, K. G., Castino, F., Meijer, V., & Yin, F. (2026). Variability of ice supersaturated regions at flight altitudes: evaluation of ERA5 reanalysis using IAGOS in situ measurements. *Atmospheric Chemistry and Physics*. https://doi.org/10.5194/acp-26-6449-2026
- Lee, D. S. et al. (2021). The contribution of global aviation to anthropogenic climate forcing for 2000 to 2018. *Atmospheric Environment*, 244, 117834.
- Platt, J., Shapiro, M., Engberg, Z., McCloskey, K., Geraedts, S., Sankar, T., Stettler, M. E. J., Teoh, R., Schumann, U., Rohs, S., Brand, E., & Van Arsdale, C. (2024). The effect of uncertainty in humidity and model parameters on the prediction of contrail energy forcing. *Environmental Research Communications*, 6, 095015.
- Poll, D. I. A. and Schumann, U. (2021). An estimation method for the fuel burn and other performance characteristics of civil transport aircraft in the cruise. *The Aeronautical Journal*, 125.
- Reutter, P., Neis, P., Rohs, S., & Sauvage, B. (2020). Ice supersaturated regions: properties and validation of ERA-Interim reanalysis with IAGOS in situ water vapour measurements. *Atmospheric Chemistry and Physics*, 20. https://doi.org/10.5194/acp-20-787-2020
- Sausen, R., Hofer, S., Gierens, K., Bugliaro, L., Ehrmanntraut, R., Sitova, I., Walczak, K., Burridge-Diesing, A., Bowman, M., & Miller, N. (2024). Can we successfully avoid persistent contrails by small altitude adjustments of flights in the real world? *Meteorologische Zeitschrift*, 33(1), 83–98. https://doi.org/10.1127/metz/2023/1157
- Schumann, U. (1996). On conditions for contrail formation from aircraft exhausts. *Meteorologische Zeitschrift*, 5, 4–23.
- Simorgh, A., Soler, M., González-Arribas, D., Linke, F., Lührs, B., et al. (2023). Robust 4D climate-optimal flight planning in structured airspace using parallelized simulation on GPUs: ROOST V1.0. *Geoscientific Model Development*, 16, 3723–3748. https://doi.org/10.5194/gmd-16-3723-2023
- Simorgh, A., Soler, M., Dietmüller, S., Matthes, S., Yamashita, H., Castino, F., & Yin, F. (2024). Robust 4D climate-optimal aircraft trajectory planning under weather-induced uncertainties: free-routing airspace. *Transportation Research Part D*, 131, 104196. https://doi.org/10.1016/j.trd.2024.104196
- Teoh, R. et al. (2020). Mitigating the climate forcing of aircraft contrails by small-scale diversions and technology adoption. *Environmental Science & Technology*, 54, 2941–2950.
- [pycontrails](https://github.com/contrailcirrus/pycontrails) (v0.63.3), Breakthrough Energy and Imperial College London.
- ERA5 accessed through Google's Analysis-Ready, Cloud-Optimized ERA5 dataset. Contains modified Copernicus Climate Change Service information.
- Boulanger, D., Blot, R., Bundke, U., Gerbig, C., Hermann, M., Nédélec, P., Rohs, S., & Ziereis, H. (2018). *IAGOS final quality controlled Observational Data L2 – Time series* [Dataset]. Aeris. https://doi.org/10.25326/06. From the [IAGOS Data Portal](https://iagos.aeris-data.fr/) (see there for updates), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Changes made here: quality-filtered, cut to the study region and 175–300 hPa, and compared with ERA5; only summary tables are published. See the [IAGOS data policy](https://iagos.aeris-data.fr/data-policy/).

---

## Acknowledgements

“MOZAIC/CARIBIC/IAGOS data were created with support from the European Commission, national agencies in Germany (BMBF), France (MESR), and the UK (NERC), and the IAGOS member institutions (http://www.iagos.org/partners). The participating airlines (Lufthansa, Air France, Austrian, China Airlines, Hawaiian Airlines, Air Canada, Iberia, Eurowings Discover, Cathay Pacific, Air Namibia, Sabena) supported IAGOS by carrying the measurement equipment free of charge since 1994. The data are available at http://www.iagos.fr thanks to additional support from AERIS.”

---

## How this was made

I started this project to learn scientific programming on a real aerospace problem. The first two stages (the baseline contrail calculation) are my own code; my original working notebook is in the history at [commit 4dc80b2](https://github.com/bunluy/robust-contrail-routing/blob/4dc80b2/notebooks/01_pycontrails_hello_world.ipynb). From the third stage on, the code was written in collaboration with an AI assistant, which wrote most of it to my direction; I typed out and worked through much of it by hand to understand it.

My part was the direction and the judgement: choosing the question and where to take it next (repeating it over 24 nights, planning for weather uncertainty, the C++ core, and testing ERA5 against airliner measurements), applying for and handling the IAGOS data, running every analysis on my own laptop, questioning results that looked wrong, and deciding what to fix, keep and report. The errors caught along the way are listed under [Checks and corrections](#checks-and-corrections).

---

## Author

Self-directed project by Baris Unluyildiz, BEng Aerospace Engineering student at the University of Manchester.
[LinkedIn](https://www.linkedin.com/in/baris-unluyildiz/) · Released under the MIT License.
