"""Verified small COMSOL geometry/selection/mesh/study construction helpers."""

from __future__ import annotations

from typing import Any

from app.scientific.schemas import GeometryFeature, MeshDefinition, SelectionDefinition, StudyDefinition


PRIMITIVE_TYPES = {
    "rectangle": "Rectangle", "circle": "Circle", "ellipse": "Ellipse", "polygon": "Polygon",
    "block": "Block", "cylinder": "Cylinder", "sphere": "Sphere",
}
OPERATION_TYPES = {"move": "Move", "rotate": "Rotate", "scale": "Scale", "union": "Union", "difference": "Difference", "intersection": "Intersection"}


def build_geometry(component: Any, features: list[GeometryFeature], *, tag: str = "geom1") -> tuple[Any, dict[str, Any]]:
    if not features:
        raise ValueError("At least one geometry feature is required")
    dimensions = {item.dimension for item in features}
    if len(dimensions) != 1:
        raise ValueError("One COMSOL geometry sequence cannot mix 2D and 3D features")
    dimension = dimensions.pop()
    geometry = component.geom().create(tag, dimension)
    geometry.lengthUnit("m")
    built: set[str] = set()
    readback: dict[str, Any] = {}
    for definition in features:
        if any(source not in built for source in definition.inputs):
            raise ValueError(f"Geometry feature {definition.tag} references an unavailable input")
        comsol_type = PRIMITIVE_TYPES.get(definition.kind) or OPERATION_TYPES[definition.kind]
        feature = geometry.feature().create(definition.tag, comsol_type)
        parameters = definition.parameters
        if definition.kind in PRIMITIVE_TYPES:
            for name, value in parameters.items():
                feature.set(name, value)
        else:
            if definition.kind == "difference":
                if len(definition.inputs) < 2:
                    raise ValueError("Difference requires one add input and at least one subtract input")
                feature.selection("input").set([definition.inputs[0]])
                feature.selection("input2").set(definition.inputs[1:])
            else:
                if not definition.inputs:
                    raise ValueError(f"{definition.kind} requires input geometry objects")
                feature.selection("input").set(definition.inputs)
            if definition.kind == "move":
                displacement = list(parameters.get("displacement", []))
                for axis, value in zip(("displx", "disply", "displz")[:dimension], displacement):
                    feature.set(axis, value)
            elif definition.kind == "rotate":
                feature.set("rot", parameters.get("angle", 0))
                if dimension == 2 and "center" in parameters:
                    feature.set("pos", parameters["center"])
                if dimension == 3 and "axis" in parameters:
                    feature.set("axis", parameters["axis"])
            elif definition.kind == "scale":
                feature.set("factor", parameters.get("factor", 1))
                if "center" in parameters:
                    feature.set("pos", parameters["center"])
            for name, value in parameters.items():
                if name not in {"displacement", "angle", "center", "axis", "factor"}:
                    feature.set(name, value)
        built.add(definition.tag)
        readback[definition.tag] = {"type": str(feature.getType()), "inputs": list(definition.inputs)}
        try:
            geometry.run(definition.tag)
        except Exception as exc:
            raise RuntimeError(f"COMSOL geometry build failed at {definition.tag} ({definition.kind}): {exc}") from exc
    geometry.run()
    readback["geometry"] = {
        "tag": str(geometry.tag()), "dimension": dimension,
        "feature_tags": [str(value) for value in geometry.feature().tags()],
        "domains": int(geometry.getNDomains()), "boundaries": int(geometry.getNBoundaries()),
        "edges": int(geometry.getNEdges()), "vertices": int(geometry.getNVertices()),
    }
    return geometry, readback


def build_selections(component: Any, definitions: list[SelectionDefinition], *, geometry_tag: str = "geom1") -> dict[str, Any]:
    readback: dict[str, Any] = {}
    for definition in definitions:
        selection_type = {"explicit": "Explicit", "box": "Box", "ball": "Ball", "adjacent": "Adjacent", "named": "Explicit"}[definition.method]
        selection = component.selection().create(definition.tag, selection_type)
        selection.geom(geometry_tag, definition.entity_dimension)
        if definition.method in {"explicit", "named"}:
            selection.set(definition.entities)
        else:
            selection.set("entitydim", str(definition.entity_dimension))
            for name, value in definition.criteria.items():
                selection.set(name, float(value) if isinstance(value, int) and not isinstance(value, bool) else value)
        entities = [int(value) for value in selection.entities()]
        if not definition.allow_empty and not entities:
            raise RuntimeError(f"Selection {definition.tag} resolved to no entities")
        readback[definition.tag] = {"type": str(selection.getType()), "entity_dimension": definition.entity_dimension, "entities": entities}
    return readback


def build_mesh(component: Any, definition: MeshDefinition | None, *, tag: str = "mesh1") -> tuple[Any, dict[str, Any]]:
    resolved = definition or MeshDefinition()
    mesh = component.mesh().create(tag)
    size = mesh.feature().create("size1", "Size")
    size.set("hauto", str({"coarse": 7, "normal": 5, "fine": 3, "custom": 5}[resolved.level]))
    for name, value in resolved.custom.items():
        size.set(name, value)
    mesh.run()
    return mesh, {"tag": str(mesh.tag()), "level": resolved.level, "hauto": str(size.getString("hauto"))}


def build_studies(model: Any, definitions: list[StudyDefinition]) -> dict[str, Any]:
    readback: dict[str, Any] = {}
    for definition in definitions:
        study = model.study().create(definition.tag)
        if definition.kind == "stationary":
            feature = study.feature().create("stat", "Stationary")
        elif definition.kind == "time_dependent":
            feature = study.feature().create("time", "Transient")
            feature.set("tlist", " ".join(str(value) for value in definition.times))
        else:
            feature = study.feature().create("stat", "Stationary")
            sweep = study.feature().create("param", "Parametric")
            names = list(definition.sweep_parameters)
            sweep.set("pname", names)
            sweep.set("plistarr", [" ".join(str(value) for value in definition.sweep_parameters[name]) for name in names])
        readback[definition.tag] = {"kind": definition.kind, "features": [str(value) for value in study.feature().tags()], "case_count": definition.case_count()}
    return readback
