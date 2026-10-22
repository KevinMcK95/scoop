# Smoke tests

Scripts that run against the live archives, by hand (the unit tests in `tests/` never
touch the network).

- `pm_expectations.py`: proper-motion expectations for an LVDB object (expected sizes,
  solar reflex, Galactocentric velocities, internal dispersion, how reflex, perspective
  and secular aberration change across the region, and the precision a measurement
  needs):

  ```bash
  python smoke_tests/pm_expectations.py --lvdb-key leo_1 --n-stars 200 --star-error 0.03
  ```

- `<survey>_probe.py`: written by the [dataset scout](../docs/dataset_scout.md) with each
  draft data source; re-checks a survey's counts, timings and coverage in a known field.

The field pipeline the workspace runs is in [`scoop/pipeline/`](../scoop/pipeline/README.md).
