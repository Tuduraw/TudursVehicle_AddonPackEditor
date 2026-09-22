# Addon Pack Editor

A GUI tool for creating and editing addon packs for Tudur's Vehicle Mod.
Available in Japanese and English (Settings → Language).


## Requirements / starting

- Python 3.9+ with tkinter; no other libraries needed
  (bundled with the python.org installers on Windows/macOS; install `python3-tk` on Linux)
- Run `python main.py`

## Usage

1. File → New addon pack: choose a pack name and a namespace. If you wish to try your pack quickly, you can choose the
   `tudursvehiclemod-addons` folder of your game directory.
2. "New vehicle": enter a name (`/` creates subfolders, e.g. `tanks/t90`) and an entity type.
3. Fill in the form and save.

- Only fields the chosen entity type uses are shown. `(default=…)` is what the mod uses when a
  field is left empty; **empty fields are not written to the JSON**.
- Hover a field name for its description and an example.
- Unlimited fields (`seats`, `runways`, ...) grow with "+ Add row".
- "Edit JSON manually" switches to raw JSON with live syntax checking; the error line (and, for a
  missing comma, the line before it) is highlighted. The form can't be shown while there's a
  syntax error. Fields the form can't show are kept and saved unchanged.
- "…" buttons copy a chosen file into the pack and fill in its reference. With `model` pointing at
  an OBJ in the pack, its parts are listed on the right, unused ones first.
- Weapons, HUD scripts, sounds and HUD textures are referenced by file name alone, so names must be
  unique within the pack.

## Weapon files ("Weapon" tab)

- Pick a weapon file or create one ("New weapon"; `guns/m2` creates subfolders). A vehicle's
  `weapon_name` uses only the file-name part (`m2`), and the vehicle form suggests the pack's weapons.
- Only keys the chosen `Type` uses are shown, and only keys the mod actually reads (107); MC Heli keys
  this mod ignores are not offered.
- Repeatable keys (`CasWaypoint`, ...) are edited row by row; comma-separated values
  (`AddMuzzleFlash`, ...) column by column.
- "Edit text manually" checks each line exactly the way the mod's loader treats it and highlights
  lines that are dropped or silently replaced by defaults - e.g. a decimal waypoint coordinate drops
  the whole row, an unreadable number becomes the default, a repeated key only keeps its last line,
  and CAS/Carrier without their required lines don't work.
- Saving from the form keeps hand-written comments, line order and unknown keys.

## HUD scripts ("HUD" tab)

- "New HUD" starts from a default template: compass tape, crosshair, horizon, speed/altitude/throttle,
  health/fuel bars, the selected weapon, lock-on, and status indicators for features MC Heli lacks
  (stall, low fuel, manual mode, gear, free look). "Insert template" puts it back at any time.
- Lines are checked as you type, the way the mod's loader treats them: unknown directives and missing
  arguments are skipped by the mod; an unclosed `If` swallows the rest of the file and a nested `If`
  closes the outer one early; unknown variables silently evaluate to 0; format/argument count
  mismatches, and textures or called scripts not in the pack, are flagged.
- The preview draws the script with sample values; click an element to jump to its line. "All
  indicators on" shows every state at once to check for overlaps. Elements outside the screen get a
  red dashed box and a warning (saving still works). Screen sizes are GUI-scaled (720p auto = 427×240,
  1080p auto = 480×270, ...).
- With Pillow installed (`pip install pillow`), `DrawTexture` shows the real image; without it, an
  outline with the texture name.

## Plugins

Plugins in `plugins/` or `~/.tudursvehiclemod_addon_editor/plugins/` load automatically; they can add
entity types, fields and records (schema files) and Python validators / save hooks.
See `plugins/motorcycleaddon/` for an example, and the module docs of `ape/plugins.py` and
`ape/schema.py` for the format.

## Following mod updates (developers)

`schemas/vehicle.json` and `schemas/weapon.json` are generated from the mod's source and Readme
files: `python devtools/gen_schema.py` and `python devtools/gen_weapon_schema.py`. Manual
corrections go in `devtools/schema_overrides.json` (vehicles) and `devtools/weapon_meta.json`
(weapons: canonical spelling, groups, types, columns, and descriptions for undocumented keys);
the weapon generator stops with a diff whenever the mod starts or stops reading a key.
Plugins can extend weapons the same way (`"kind": "weapon"`), e.g. adding an addon's custom
`Type = namespace:name` and its own keys.
