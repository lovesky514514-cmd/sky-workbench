"""Explicit, user-triggered COMSOL add-on availability probe."""

from __future__ import annotations

from typing import Any


PRODUCTS = {
    "structural_mechanics": "STRUCTURALMECHANICS",
    "electrochemistry": "ELECTROCHEMISTRY",
    "battery_design": "BATTERYDESIGN",
}


def probe_optional_modules() -> dict[str, Any]:
    client = None
    try:
        import jpype
        import mph

        client = mph.Client()
        model_util = jpype.JClass("com.comsol.model.util.ModelUtil")
        modules = {
            name: {
                "product": product,
                "available": bool(model_util.hasProduct(product)),
                "status": "AVAILABLE" if bool(model_util.hasProduct(product)) else "MODULE_MISSING",
            }
            for name, product in PRODUCTS.items()
        }
        return {"success": True, "real_comsol": True, "comsol_version": str(client.version), "modules": modules}
    except Exception as exc:
        return {"success": False, "real_comsol": False, "status": "UNVERIFIED", "detail": f"{type(exc).__name__}: {exc}", "modules": {}}
    finally:
        if client is not None:
            try:
                client.clear()
            except Exception:
                pass
