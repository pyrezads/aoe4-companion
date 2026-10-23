"""
Builds data/units_db.json and data/unit_icons.npz from the aoe4world open data
repo (github.com/aoe4world/data). Run once when the game data updates:

    python tools/build_units_db.py path/to/aoe4world-data
"""

import json
import pathlib
import sys

import numpy as np
from PIL import Image

CIV_CODES = {
    "ab": "abbasid_dynasty", "ay": "ayyubids", "by": "byzantines", "ch": "chinese", "de": "delhi_sultanate",
    "en": "english", "fr": "french", "gol": "golden_horde", "hl": "house_of_lancaster", "hr": "holy_roman_empire",
    "ja": "japanese", "je": "jeanne_darc", "jin": "jin_dynasty", "kt": "knights_templar", "ma": "malians",
    "mac": "macedonian_dynasty", "mo": "mongols", "od": "order_of_the_dragon", "ot": "ottomans", "ru": "rus",
    "sen": "sengoku_daimyo", "tug": "tughlaq_dynasty", "zx": "zhu_xis_legacy",
}

ICON = 50                      # in-game card size at 2560x1440
CARD_BG = (120, 70, 44)        # card background colour


def main(src: pathlib.Path, out: pathlib.Path):
    units = json.loads((src / "units/all-unified.json").read_text())["data"]
    db = {}
    icon_files = {}
    for un in units:
        for v in un["variations"]:
            civs = sorted({CIV_CODES.get(c, c) for c in v.get("civs", [])})
            weapons = []
            for w in v.get("weapons") or []:
                mods = []
                for m in w.get("modifiers") or []:
                    if m.get("effect") != "change" or m.get("property") not in ("meleeAttack", "rangedAttack", "siegeAttack", "fireAttack"):
                        continue
                    tgt = (m.get("target") or {}).get("class") or []
                    mods.append({"classes": [list(c) for c in tgt], "value": m.get("value", 0)})
                weapons.append({"name": w.get("name"), "type": w.get("type"), "damage": w.get("damage", 0),
                                "speed": w.get("speed") or 1.0, "range": (w.get("range") or {}).get("max", 0),
                                "mods": mods})
            armor = {a["type"]: a["value"] for a in v.get("armor") or []}
            c = v.get("costs") or {}
            icon = (v.get("icon") or "").rsplit("/", 1)[-1]
            db.setdefault(v["id"], []).append({
                "base": v.get("baseId"), "name": v.get("name"), "age": v.get("age"), "civs": civs,
                "classes": v.get("classes") or [], "display": (v.get("displayClasses") or [""])[0],
                "hp": v.get("hitpoints") or 0, "armor": armor, "weapons": weapons,
                "speed": (v.get("movement") or {}).get("speed"),
                "cost": {k: c.get(k, 0) for k in ("food", "wood", "gold", "stone")},
                "time": c.get("time"), "pop": c.get("popcap", 1),
                "producedBy": v.get("producedBy") or [], "icon": icon,
            })
            if icon:
                icon_files[icon] = src / "images/units" / icon

    out.mkdir(parents=True, exist_ok=True)
    (out / "units_db.json").write_text(json.dumps(db, separators=(",", ":")))

    names, arrs = [], []
    for name, path in sorted(icon_files.items()):
        if not path.exists():
            continue
        im = Image.open(path).convert("RGBA").resize((ICON, ICON), Image.LANCZOS)
        bg = Image.new("RGBA", im.size, CARD_BG + (255,))
        bg.alpha_composite(im)
        arrs.append(np.asarray(bg.convert("RGB"), dtype=np.uint8))
        names.append(name)
    np.savez_compressed(out / "unit_icons.npz", names=np.array(names), icons=np.stack(arrs))
    print(f"{len(db)} unit ids, {sum(len(v) for v in db.values())} civ variants, {len(names)} icons")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]), pathlib.Path(__file__).resolve().parent.parent / "data")


def build_techs(src: pathlib.Path, out: pathlib.Path):
    """data/techs_db.json: military upgrades with what they change and who they affect."""
    keep_props = {"meleeAttack", "rangedAttack", "siegeAttack", "meleeArmor", "rangedArmor", "hitpoints",
                  "attackSpeed", "moveSpeed", "maxRange", "healingRate"}
    techs = json.loads((src / "technologies/all-unified.json").read_text())["data"]
    out_list = []
    for t in techs:
        for v in t["variations"]:
            effects = []
            for e in v.get("effects") or []:
                if e.get("property") not in keep_props:
                    continue
                sel = e.get("select") or {}
                effects.append({"property": e["property"], "effect": e.get("effect"), "value": e.get("value", 0),
                                "classes": [list(c) for c in sel.get("class") or []], "ids": sel.get("id") or []})
            if not effects:
                continue
            c = v.get("costs") or {}
            out_list.append({"id": v["id"], "base": v.get("baseId"), "name": v.get("name"), "age": v.get("age"),
                             "civs": sorted({CIV_CODES.get(x, x) for x in v.get("civs", [])}),
                             "cost": {k: c.get(k, 0) for k in ("food", "wood", "gold", "stone")},
                             "time": c.get("time"), "producedBy": v.get("producedBy") or [], "effects": effects})
    (out / "techs_db.json").write_text(json.dumps(out_list, separators=(",", ":")))
    print(f"{len(out_list)} military technology variants")


if __name__ == "__main__" and len(sys.argv) > 1:
    build_techs(pathlib.Path(sys.argv[1]), pathlib.Path(__file__).resolve().parent.parent / "data")
