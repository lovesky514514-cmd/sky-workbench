"""Report sampling diagnostics without certifying a material property."""

import math
from statistics import mean, stdev


def sampling_report(columns: list[str], series: list[list[float]], settings) -> dict:
    report = {"sampling": {"status": "NEEDS_REVIEW", "samples": len(series), "minimum_samples": 50},
              "temperature": {"status": "NOT_EVALUATED"}, "pressure_blocks": {"status": "NOT_EVALUATED"},
              "diffusion": {"status": "NOT_EVALUATED"},
              "reference_comparison": {"status": "NOT_EVALUATED", "reason": "No applicable, versioned reference supplied"}}
    tail = series[len(series) // 2:]
    report["sampling"]["status"] = "PASSED" if len(tail) >= 50 else "NEEDS_REVIEW"
    report["sampling"]["samples_used"] = len(tail)
    report["sampling"]["equilibration_steps"] = getattr(settings, "equilibration_steps", None)
    if not settings or not tail:
        return report
    if "Temp" in columns:
        observed = mean(row[columns.index("Temp")] for row in tail)
        error = abs(observed - settings.temperature) / settings.temperature
        report["temperature"] = {"status": "PASSED" if error <= 0.05 else "NEEDS_REVIEW",
                                 "mean": observed, "target": settings.temperature, "relative_error": error,
                                 "tolerance": 0.05}
    if "Press" in columns and len(tail) >= 20:
        values = [row[columns.index("Press")] for row in tail]
        size = len(values) // 5
        blocks = [mean(values[i * size:(i + 1) * size]) for i in range(5)]
        average = mean(blocks)
        relative = stdev(blocks) / abs(average) if abs(average) > 1e-12 else None
        report["pressure_blocks"] = {"status": "PASSED" if relative is not None and relative <= 0.2 else "NEEDS_REVIEW",
                                     "mean": average, "block_means": blocks, "relative_stddev": relative,
                                     "tolerance": 0.2, "independence_verified": False}
    if "c_sky_msd[4]" in columns and len(tail) >= 3:
        x = [row[0] * settings.timestep for row in tail]
        y = [row[columns.index("c_sky_msd[4]")] for row in tail]
        center_x, center_y = mean(x), mean(y)
        xx = sum((v - center_x) ** 2 for v in x)
        yy = sum((v - center_y) ** 2 for v in y)
        xy = sum((a - center_x) * (b - center_y) for a, b in zip(x, y))
        slope = xy / xx if xx else None
        r2 = xy ** 2 / (xx * yy) if xx and yy else None
        report["diffusion"] = {"status": "FIT_AVAILABLE" if slope is not None and slope >= 0 and r2 is not None else "NEEDS_REVIEW",
                               "estimate": slope / 6 if slope is not None else None, "r_squared": r2,
                               "units": "LJ reduced length^2/time", "fit_window": [x[0], x[-1]],
                               "physical_diffusive_regime_verified": False}
    # A stable block mean or a good fit does not establish reference agreement.
    return report
