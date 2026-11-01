"""Small, fail-closed whitelist quantity parser. No implicit/default units."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import math
import re


@dataclass(frozen=True)
class Quantity:
    original: str
    original_span: tuple[int, int]
    original_unit: str
    original_value: float | None
    normalized_value: float | None
    si_unit: str
    issue: str | None = None


SI = {
    "diffusion": "m²/s",
    "length": "m",
    "concentration": "mol/m³",
    "temperature": "K",
    "temperature_difference": "K",
    "potential": "V",
    "thermal_conductivity": "W/(m*K)",
    "density": "kg/m³",
    "heat_capacity": "J/(kg*K)",
    "time": "s",
    "dimensionless": "1",
    "heat_transfer_coefficient": "W/(m²*K)",
}
SI.update(energy="J", pressure="Pa", inverse_temperature="1/K")
FACTORS = {
    "diffusion": {"m²/s": "1", "cm²/s": "0.0001", "mm²/s": "0.000001"},
    "length": {"m": "1", "cm": "0.01", "mm": "0.001"},
    "concentration": {"mol/m³": "1"},
    "temperature": {"K": "1", "°C": "1"},
    "temperature_difference": {"K": "1", "°C": "1"},
    "potential": {"V": "1"},
    "thermal_conductivity": {"W/(m*K)": "1"},
    "density": {"kg/m³": "1"},
    "heat_capacity": {"J/(kg*K)": "1"},
    "time": {"s": "1", "min": "60"},
    "dimensionless": {"1": "1"},
    "heat_transfer_coefficient": {"W/(m²*K)": "1"},
}
FACTORS["length"].update(
    {
        "um": "0.000001",
        "µm": "0.000001",
        "nm": "0.000000001",
        "angstrom": "0.0000000001",
    }
)
FACTORS.update(
    energy={"J": "1", "eV": "1.602176634e-19"},
    pressure={"Pa": "1", "kPa": "1000", "MPa": "1000000", "GPa": "1000000000"},
    inverse_temperature={"1/K": "1"},
)
NUMBER = r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?"
# Intentionally capture malformed numeric tokens in full; never parse a prefix.
TOKEN = (
    r"[+\-−]?(?:\d[\d.]*|\.[\d.]+)(?:\s*[×*]\s*10\s*\^\s*[+\-−]?\d*|[eE][+\-−]?\d*)?"
)
UNIT = r"(?:毫米|厘米|纳米|微米|摄氏度|开尔文|分钟|秒|米|[A-Za-z°℃µμ][A-Za-z°℃²³µμ/\^0-9_()*]*(?:\s*[/\^]\s*[A-Za-z0-9²³]+)*)"


def parse_quantity(raw: str, kind: str, *, start: int = 0) -> Quantity:
    si = SI.get(kind, "")

    def invalid(issue: str, unit: str = "") -> Quantity:
        return Quantity(raw, (start, start + len(raw)), unit, None, None, si, issue)

    unit_pattern = (
        r"1"
        if kind == "dimensionless"
        else r"1/K"
        if kind == "inverse_temperature"
        else UNIT
    )
    optional = "?" if kind == "dimensionless" else ""
    match = re.fullmatch(rf"\s*({TOKEN})\s*\[?({unit_pattern}){optional}\]?\s*", raw)
    if not match:
        return invalid("invalid_quantity")
    literal, original_unit = match.groups()
    # A named dimensionless property may omit its unit; dimensional quantities
    # still require an explicit unit. The original text/span remains untouched.
    original_unit = original_unit or "1"
    literal = re.sub(r"\s*[×*]\s*10\s*\^\s*", "e", literal.replace("−", "-"))
    if not re.fullmatch(NUMBER, literal):
        return invalid("invalid_number", original_unit)
    unit = re.sub(r"\s+", "", original_unit).replace("^2", "²").replace("^3", "³")
    unit = {
        "m2/s": "m²/s",
        "cm2/s": "cm²/s",
        "mm2/s": "mm²/s",
        "mol/m3": "mol/m³",
        "毫米": "mm",
        "厘米": "cm",
        "微米": "um",
        "纳米": "nm",
        "米": "m",
        "摄氏度": "°C",
        "开尔文": "K",
        "秒": "s",
        "分钟": "min",
        "C": "°C",
        "c": "°C",
        "°c": "°C",
        "℃": "°C",
        "Celsius": "°C",
        "Kelvin": "K",
    }.get(unit, unit)
    factor = FACTORS.get(kind, {}).get(unit)
    if factor is None:
        return invalid("unsupported_unit", original_unit)
    try:
        value = Decimal(literal)
        normalized = value * Decimal(factor)
        if kind == "temperature" and unit == "°C":
            normalized += Decimal("273.15")
        number, original_number = float(normalized), float(value)
        if (
            not math.isfinite(number)
            or not math.isfinite(original_number)
            or (normalized != 0 and number == 0)
        ):
            return invalid("nonfinite_or_underflow", original_unit)
    except (InvalidOperation, ValueError, OverflowError):
        return invalid("invalid_number", original_unit)
    return Quantity(
        raw, (start, start + len(raw)), original_unit, original_number, number, si
    )


def convert_quantity(
    value: float, source_unit: str, target_unit: str, kind: str
) -> float:
    """Convert only a declared physical kind; reduced MD units need a separate model."""
    parsed = parse_quantity(f"{value} {source_unit}", kind)
    if parsed.issue:
        raise ValueError(f"Unresolved or incompatible source unit: {source_unit}")
    zero = parse_quantity(f"0 {target_unit}", kind)
    one = parse_quantity(f"1 {target_unit}", kind)
    if zero.issue or one.issue:
        raise ValueError(f"Unresolved or incompatible target unit: {target_unit}")
    return (parsed.normalized_value - zero.normalized_value) / (
        one.normalized_value - zero.normalized_value
    )


def geometry_quantities(text: str, dimension: int = 2) -> tuple[Quantity, ...]:
    """Parse complete geometry atoms, never a suffix of an exponent or bad token.

    Keep original spans for both intent and planning; reject unknown whole units
    and nonpositive lengths before any workflow can become runnable.
    """
    if dimension not in (2, 3):
        raise ValueError("geometry dimension must be 2 or 3")
    atom = rf"{TOKEN}\s*\[?{UNIT}\]?(?![A-Za-z0-9_/^+\-−]|\.\d)"
    boundary = r"(?<![A-Za-z0-9_.+\-−^×*])"
    captures = [rf"(?P<q{i}>{atom})" for i in range(dimension)]
    separator = r"(?:\s*[×xX*]\s*|\s+by\s+)(?:高(?:度)?\s*)?"
    patterns = [boundary + separator.join(captures)]
    if dimension == 2:
        patterns += [
            boundary
            + captures[0]
            + r"\s+wide\s+(?:and|by)\s+"
            + captures[1]
            + r"\s+high\b",
            boundary + captures[0] + r"\s*[,，;；]?\s*高(?:度)?\s*" + captures[1],
        ]
    matches = []
    shared_spans = {}
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            # Whitespace must not turn a malformed exponent into a new length.
            prefix = text[: match.start()].rstrip()
            if re.search(r"(?:[×*^+\-−]|\d\S*[eE^×*]\S*)$", prefix):
                return ()
            matches = [
                (match.group(f"q{i}"), match.start(f"q{i}")) for i in range(dimension)
            ]
            break
    if not matches:
        # A trailing explicit unit applies to every axis in "3 x 3 x 20 mm".
        shared = boundary + separator.join(
            rf"(?P<n{i}>{TOKEN})" for i in range(dimension)
        )
        shared += rf"\s*(?P<unit>{UNIT})(?![A-Za-z0-9_/^])"
        match = re.search(shared, text, re.I)
        if match and not re.search(r"[\d×xX*^+\-−]$", text[: match.start()].rstrip()):
            matches = [
                (match.group(f"n{i}") + " " + match.group("unit"), match.start(f"n{i}"))
                for i in range(dimension)
            ]
            shared_spans = {
                match.start(f"n{i}"): (
                    match.start(f"n{i}"),
                    match.end("unit") if i == dimension - 1 else match.end(f"n{i}"),
                )
                for i in range(dimension)
            }
    if not matches and dimension == 3:
        separated = _length_and_section(text, atom)
        if separated:
            return separated
    if not matches:
        axes = [
            r"\bL\b|\blength\b|长度|长",
            r"\bW\b|\bwidth\b|宽度|宽",
            r"\bH\b|\bheight\b|高度|高",
        ]
        if dimension == 2:
            axes = [axes[0] + "|" + axes[1], axes[2]]
        for labels in axes:
            match = re.search(
                rf"(?:{labels})\s*(?:=|:|：|为|is)?\s*(?P<q>{atom})", text, re.I
            )
            if not match:
                return ()
            matches.append((match.group("q"), match.start("q")))
    quantities = []
    for raw, start in matches:
        quantity = parse_quantity(raw, "length", start=start)
        if start in shared_spans:
            span = shared_spans[start]
            quantity = replace(
                quantity, original=text[span[0] : span[1]], original_span=span
            )
        if quantity.issue is None and quantity.normalized_value <= 0:
            quantity = replace(
                quantity, normalized_value=None, issue="nonpositive_length"
            )
        quantities.append(quantity)
    return tuple(quantities)


def _length_and_section(text: str, atom: str) -> tuple[Quantity, ...]:
    """Bind one explicit length and one rectangular section without losing spans."""
    lengths = list(
        re.finditer(
            rf"(?:\blength\b|长度|长)\s*(?:=|:|：|为|is)?\s*(?P<q>{atom})", text, re.I
        )
    )
    sections = list(
        re.finditer(
            r"(?:矩形截面|截面|cross[- ]section)\s*(?:为|尺寸|=|:|：)?", text, re.I
        )
    )
    if len(lengths) != 1 or len(sections) != 1:
        return ()
    section = sections[0]
    if re.search(
        r"圆|直径|circle|ellipse|diameter",
        text[max(0, section.start() - 12) : section.end()],
        re.I,
    ):
        return ()
    end = re.search(r"[,，;；。\n]", text[section.end() :])
    stop = section.end() + end.start() if end else len(text)
    dimensions = geometry_quantities(text[section.end() : stop], 2)
    if not dimensions:
        return ()
    length = parse_quantity(
        lengths[0].group("q"), "length", start=lengths[0].start("q")
    )
    if length.issue is None and length.normalized_value <= 0:
        length = replace(length, normalized_value=None, issue="nonpositive_length")
    return (
        length,
        *(
            replace(
                q,
                original_span=tuple(index + section.end() for index in q.original_span),
            )
            for q in dimensions
        ),
    )


def valid_geometry(text: str, dimension: int = 2) -> tuple[Quantity, ...] | None:
    quantities = geometry_quantities(text, dimension)
    return (
        quantities if quantities and all(q.issue is None for q in quantities) else None
    )


def find_quantity(
    text: str, labels: str, kind: str, *, gap: int = 32, allow_before: bool = False
) -> Quantity | None:
    """Find a labeled value without crossing the next boundary/statement.

    Labels are caller-owned regex, not user-provided expressions. Whole unit
    tokens are consumed before whitelist checking (m²/suffix is not m²/s).
    """
    for label in re.finditer(labels, text, re.IGNORECASE):
        tail = text[label.end() :]
        tail = re.split(
            r"[,，;；\n。]|\b(?:left|right)\b|左(?:侧|边)|右(?:侧|边)",
            tail,
            maxsplit=1,
            flags=re.I,
        )[0]
        unit_pattern = r"1/K" if kind == "inverse_temperature" else UNIT
        match = re.search(rf"({TOKEN})\s*\[?({unit_pattern})?\]?", tail[: gap + 80])
        if match and match.start() <= gap:
            start = label.end() + match.start()
            return parse_quantity(match.group(0), kind, start=start)
    if allow_before:
        for label in re.finditer(labels, text, re.IGNORECASE):
            candidates = list(
                re.finditer(rf"({TOKEN})\s*\[?({UNIT})\]?", text[: label.start()])
            )
            if candidates:
                match = candidates[-1]
                between = text[match.end() : label.start()]
                if len(between) <= gap and not re.search(r"[,，;；\n。\d]", between):
                    return parse_quantity(match.group(0), kind, start=match.start())
    return None
