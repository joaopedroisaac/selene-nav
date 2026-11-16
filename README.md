# Selene Nav

**Where should we land on the Moon?** Selene Nav compares lunar landing sites and dates in seconds: when a site has **sunlight for power** and when **Earth is in view for direct communication**, using real NASA ephemeris and terrain data.

Built for the **NASA Space Apps Challenge 2026**, challenge **Lunar Landing Site Navigator**.

🔗 **Live demo:** https://joaopedroisaac.github.io/selene-nav/ (opens in any browser, no install or login)
🎬 **30-second video:** https://www.youtube.com/watch?v=PI8BOY8FTPk&feature=youtu.be

---

## The problem

Missions to the Moon's south pole face extreme lighting and communication constraints. There, the Sun never rises more than about 1.5° above the horizon, so a single distant mountain can cut a lander's power, and Earth sits so low that terrain can block the radio link. When a launch slips or a site changes, planners need to re-evaluate quickly.

The data to answer these questions exists (JPL Horizons, LROC Quickmap, Moon Trek), but each tool is built for detailed analysis by specialists. Comparing a few site and date combinations means jumping between interfaces and reading raw numbers.

## Our solution

Selene Nav turns ephemeris and topography into a few clear answers: **is this site good on this date, and why?**

- **3D Moon start screen:** spin the Moon, click a site (Apollo, commercial CLPS landers, other countries' missions, and the 9 Artemis candidate regions) and read its story and its result for the chosen date.
- **Side-by-side plans:** compare up to 4 site + date plans. Each one gets a verdict (good / caution / not recommended) with the reason, plus the share of time with sunlight, with Earth in view, and with both.
- **"What if the launch slips?"** Delay a plan by one week, two weeks or a month and see the before and after instantly.
- **Countdowns:** time until sunset/sunrise and Earthset/Earthrise at any moment after landing.
- **The sky seen from the site:** a 360° panorama of the real horizon with the Sun and Earth and their paths over the coming days, animated.
- **South pole map:** shaded LOLA relief with every site colored by its verdict for the same date, plus a ranking. It is the fastest way to find an alternative site in the same region.
- **Sun elevation chart:** the solar elevation angle compared with the terrain in the Sun's direction, the key metric for power generation.
- **Best-date calendar:** every possible landing day colored from poor to great, with the best dates listed.
- **Two audiences:** *Explore* mode explains everything in plain language for schools and the public, with a guided tour and a glossary. *Plan* mode shows detailed numbers, editable mission rules and CSV export for engineers.

## NASA data and resources used

| Source | How Selene Nav uses it |
|---|---|
| **NASA/JPL Horizons System** (API) | Apparent azimuth and elevation of the Sun and Earth, hour by hour, as seen from each landing site on the lunar surface. Libration and orbital variations are included. |
| **LRO Lunar Orbiter Laser Altimeter (LOLA)**, polar gridded DEMs (`LDEM_85S_20M`, `LDEM_60S_120M`) | Real terrain around each site. We compute a 360° horizon profile, including the curvature of the Moon, and use it to decide whether the Sun and Earth are actually visible. Also used for the shaded south pole map. |
| **LROC Quickmap** and **NASA Moon Trek** | Site selection and reference for the terrain and lighting around each site. |
| **CSA Moon Exploration** (partner agency) | Background and educational context about lunar exploration and the Artemis program. |

All numbers in the app come from these sources. The app shows a *Demo data* badge when real data is not loaded, and *Data: JPL Horizons + LOLA* when it is.

## How it works

```
locais.csv ──► baixar_efemerides.py ──► Sun & Earth az/el per hour (JPL Horizons)
                                              │
LOLA DEMs  ──► calcular_horizonte.py ──► 360° horizon per site ──► visibility with real terrain
           └─► gerar_mapa.py        ──► shaded south pole map
                                              │
               exportar_app.py      ──► dados/app_dados.json + validation tests
                                              │
               index.html (Selene Nav) ──► comparison, panorama, map, calendar, 3D Moon
```

Key modeling choices:

- **No atmosphere, no refraction.** The horizon is defined only by terrain and the curvature of the lunar sphere (R = 1737.4 km).
- **The Sun is a disk, not a point.** We compute the fraction of the solar disk above the terrain, which matters when the Sun skims the horizon.
- **Earth visibility rule is explicit and adjustable:** center of the disk, any part, or the whole disk above the terrain, plus an optional antenna margin.
- **Irradiance** is estimated as 1361 W/m² × (1 AU / distance)² × visible fraction of the solar disk.

## How we know it is right

The pipeline runs physical sanity checks, and the app shows the results in the section *How we know it is right*:

| Test | Expected |
|---|---|
| Sun at the south pole | Never above ~1.54° (lunar axial tilt) |
| Earth seen from the pole | Swings up to ~6.7° (libration) |
| Chang'e 4 site (far side) | Earth never visible |
| Apollo 11 site (near side) | Earth always in the sky |
| Near the equator | Sun above the horizon about 50% of the time |

A next step is to cross-check site elevations and sunlight percentages against LROC Quickmap and NASA's average illumination maps.

## Known limitations

- **Earth in view does not guarantee contact.** We do not model the schedule of the Deep Space Network antennas on Earth.
- **Terrain has uncertainty.** Near the pole, sunrise and sunset times can shift by hours with small terrain errors, so times are estimates.
- **Artemis regions are approximate references.** NASA published the names of the 9 candidate regions, not exact landing points.
- **Not modeled:** eclipses (Earth passing in front of the Sun), temperature, dust and the lander's own shadow.
- **W/m² is incoming light,** not generated power, which depends on panel orientation and efficiency.
- **The 3D Moon on the start screen is an illustration.** The real data is used in the map, panorama, charts and verdicts.

## Run it yourself

You only need this to regenerate the data. The live demo already includes it.

Requirements: Python 3.10+ and `numpy` (`pip install numpy`).

```bash
# 1. Sun and Earth positions from JPL Horizons (2027–2028, hourly)
python baixar_efemerides.py            # add --teste for a quick 30-day check

# 2. Download the LOLA polar DEMs (.IMG + .LBL) into a folder named dem/
#    https://imbrium.mit.edu/DATA/LOLA_GDR/POLAR/IMG/
#    LDEM_85S_20M (detail near the pole) and LDEM_60S_120M (wider coverage)

# 3. Real horizon for each site
python calcular_horizonte.py --dem dem/LDEM_85S_20M.LBL dem/LDEM_60S_120M.LBL

# 4. Shaded south pole map
python gerar_mapa.py --dem dem/LDEM_60S_120M.LBL --raio-km 320

# 5. Export for the app (prints the validation tests)
python exportar_app.py

# 6. Open the app
python -m http.server 8000             # then visit http://localhost:8000
```

The `dem/` folder is not included in this repository because the files are too large. The live app only needs `index.html` and `app_dados.json`.

## Project structure

| File | Purpose |
|---|---|
| `index.html` | The Selene Nav web app: a single file with no build step and no external libraries |
| `app_dados.json` | Data generated by the pipeline: Sun and Earth positions, real horizons and the south pole map |
| `locais.csv` | Landing sites: name, latitude, east longitude, category |
| `baixar_efemerides.py` | Step 1: downloads Sun and Earth positions from JPL Horizons |
| `calcular_horizonte.py` | Step 2: computes real horizons from LOLA terrain and applies them |
| `gerar_mapa.py` | Step 3: renders the shaded south pole map |
| `exportar_app.py` | Step 4: bundles everything for the app and runs the validation tests |

The script and variable names are in Portuguese because our team is from Brazil. The app itself is in English.

## Use of AI

Our team used an AI assistant (Claude, by Anthropic) throughout the project:

- **Research:** reading and summarizing the challenge, its resources and the NASA datasets.
- **Code:** the AI wrote most of the Python data pipeline and the web app, based on our requirements and feedback.
- **Design and text:** interface design iterations, plain-language explanations, the guided tour, English text, this README and the video script.

**What the team did:** chose the challenge and the product direction, named the project, decided which features to build and which to cut, reviewed every version and asked for changes, ran the full pipeline with real NASA data (JPL Horizons and LRO LOLA), checked the validation results, tested the app, published it and produced the video.

**All scientific data comes from NASA sources.** No numbers in the app were generated by AI: they are computed from JPL Horizons ephemeris and LOLA terrain data, and checked by the validation tests shown in the app.

## Team

Selene Nav - João Pedro Isaac, Kainã Aguiar, Alex Salles, Caio Goulart, Lara Brito

---

*Selene Nav is a student project for the NASA Space Apps Challenge 2026. It is not an official NASA product. NASA does not endorse any non-U.S. Government entity.*
