"""Example plugin code: a validator for the motorcycle settings.

register(api) is called once when the tool starts. See ape/plugins.py for the API.
"""


def register(api):
    api.add_validator("vehicle", check_motorcycle)


def check_motorcycle(data, entity_type):
    if entity_type != "motorcycleaddon:motorcycle":
        return []
    moto = data.get("motorcycle") or {}
    warnings = []
    if moto.get("enable_acrobatics") and float(moto.get("wheelie_angle_degrees", 35.0)) > 80:
        warnings.append(api_text("wheelie_too_steep"))
    if float(moto.get("max_lean_degrees", 35.0)) > 75:
        warnings.append(api_text("lean_too_steep"))
    return warnings


def api_text(key):
    from ape.i18n import t
    return t(f"motorcycle.{key}")
