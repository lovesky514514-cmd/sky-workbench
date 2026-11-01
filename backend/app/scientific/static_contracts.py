"""Inputs consumed by the supported stationary, source-free equations."""

HEAT_REQUIRED = (
    "width",
    "height",
    "left_temperature",
    "right_temperature",
    "thermal_conductivity",
)
SOLID_REQUIRED = (
    "length",
    "width",
    "height",
    "youngs_modulus",
    "poissons_ratio",
    "traction",
)

# These properties remain validated when supplied, but are not invented to
# satisfy a stationary equation with no inertia, motion or body-force terms.
HEAT_OPTIONAL = ("density", "heat_capacity")
