# NVPrecinctMap

Nevada precinct- and district-level election data pipeline + atlas frontend.

This project:

- normalizes raw Nevada election CSVs into OpenElections-like files
- builds contest JSON slices used by the map UI
- aggregates results for county, congressional, legislative, and neutral-congressional views
- wires those outputs into a single interactive `index.html` atlas

## Project Structure

Top-level folders:

- `data/openelections/`: normalized OpenElections-style precinct CSVs
- `data/contests/`: per-contest/per-year JSON slices + `manifest.json`
- `data/district_contests/`: district contest slices used by district views
- `data/crosswalks/`: CD/legislative/neutral crosswalk inputs
- `data/census/`: geometry and supporting census files
- `scripts/`: build and conversion scripts

Key generated aggregate files:

- `data/nv_elections_aggregated.json` (county/statewide-style aggregate)
- `data/nv_congressional_aggregated.json`
- `data/nv_legislative_aggregated.json`
- `data/nv_neutral_congressional_aggregated.json`

Frontend:

- `index.html` (single-page atlas app)

## Data Standards

### OpenElections-style precinct CSV

Generated files use:

- `county`
- `precinct`
- `office`
- `district`
- `party`
- `candidate`
- `votes`

### Candidate and party cleanup

The conversion pipeline applies:

- party normalization/backfill from reference files and overrides
- candidate name cleanup and variant matching
- district extraction where available

## Contest Rules Used

### County/contests build (`data/contests`)

Year rules:

- `2022`: `us_house`, `us_senate`, `governor`, `lieutenant_governor`, `attorney_general`, `treasurer`, `controller`, `state_senate`, `state_assembly`
- `2024`: `president`, `us_senate`, `us_house`, `state_senate`, `state_assembly`
- other years: `president`, `us_senate`, `governor`, `lieutenant_governor`, `attorney_general`, `treasurer`, `controller`

Notes:

- Nevada uses `controller` (not `comptroller`) as output key.
- `comptroller` is accepted as an input alias in office parsing.

### Congressional + Neutral aggregation

`scripts/build_nv_scope_aggregates.py` now aggregates, across all available years:

- `president` (when present)
- `us_senate`
- `governor`
- `lieutenant_governor`
- `attorney_general`
- `treasurer`
- `controller`
- `us_house`

For non-house contests, precincts are mapped to CDs using same-year US House district rows.

Neutral aggregation then maps CD to neutral IDs via crosswalks:

- `data/crosswalks/vtd20_to_cd118.csv`
- `data/crosswalks/neutral_to_vtd20.csv`

## Scripts

Main scripts:

- `scripts/convert_nv_to_openelections.py`
- `scripts/build_contest_jsons.py`
- `scripts/build_nv_elections_aggregated.py`
- `scripts/build_nv_scope_aggregates.py`
- `scripts/build_congressional_district_contests.py`

Auxiliary crosswalk scripts:

- `build_crosswalk_chains.py`
- `build_district_crosswalks.py`
- `build_neutral_crosswalks.py`

## Build Steps

From repository root:

```powershell
py scripts/convert_nv_to_openelections.py
py scripts/build_contest_jsons.py
py scripts/build_nv_elections_aggregated.py
py scripts/build_nv_scope_aggregates.py
```

Optional district contest rebuild:

```powershell
py scripts/build_congressional_district_contests.py
```

## Frontend Wiring Notes

`index.html` is configured to use:

- `./data/nv_elections_aggregated.json` for county/statewide-like aggregates
- `./data/nv_congressional_aggregated.json` for congressional district fallback
- `./data/district_contests` manifests/slices for district contest loading
- `./data/crosswalks/...` for district carryover/crosswalk logic

Additional behavior:

- county view contest list excludes legislative/district-only contest types
- district lines are currently locked to `2022` in UI
- congressional neutral toggle switches to `nv_neutral_congressional_aggregated.json`

## Source and Attribution Notes

Primary data comes from Nevada election result files and supporting crosswalk/census geometry in this repo.

Party backfills and candidate cleanup may include reference/override inputs in:

- `data/openelections/reference/`
- `data/openelections/party_overrides.csv`
- `data/openelections/_oe_reference_2022.csv`

## Status

Current branch target is `main`.

If this is your first push:

```powershell
git remote add origin https://github.com/Tenjin25/NVPrecinctMap.git
git push -u origin main
```
