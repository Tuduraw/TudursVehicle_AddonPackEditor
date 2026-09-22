"""UI strings. Every visible string goes through t(); the language files are flat
key -> text JSON maps under lang/. A plugin may ship its own lang/<code>.json, merged
on top. Missing keys fall back to English, then to the key itself."""
import json
from pathlib import Path

LANG_DIR = Path(__file__).resolve().parent.parent / "lang"
SUPPORTED = {"ja": "日本語", "en": "English"}

_strings = {}
_fallback = {}
_current = "ja"
_extra_dirs = []


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_language(code):
    global _strings, _fallback, _current
    _current = code if code in SUPPORTED else "en"
    _fallback = _load(LANG_DIR / "en.json")
    _strings = _load(LANG_DIR / f"{_current}.json")
    for d in _extra_dirs:
        _merge_dir(d)


def _merge_dir(d):
    _fallback.update(_load(Path(d) / "en.json"))
    _strings.update(_load(Path(d) / f"{_current}.json"))


def add_lang_dir(d):
    """Lets a plugin contribute its own strings."""
    _extra_dirs.append(d)
    _merge_dir(d)


def current():
    return _current


def t(key, **kwargs):
    text = _strings.get(key) or _fallback.get(key) or key
    return text.format(**kwargs) if kwargs else text


def pick(localized):
    """Chooses the current-language entry from a {"ja": ..., "en": ...} dict."""
    if not isinstance(localized, dict):
        return localized or ""
    return localized.get(_current) or localized.get("en") or next(iter(localized.values()), "")
