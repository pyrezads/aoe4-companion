"""
Bundles the game's icons (units, technologies, buildings, upgrades) for the app, from a clone
of aoe4world/data (icons extracted from Age of Empires IV's own files).

    git clone --depth 1 https://github.com/aoe4world/data ../aoe4world-data
    python tools/make_icons.py ../aoe4world-data

Writes app/renderer/img/game/<kind>/<id>.webp (96 px) and app/renderer/img/game/index.json.
Run again after a game patch. Age of Empires IV © Microsoft Corporation, used under Microsoft's
Game Content Usage Rules (https://www.xbox.com/en-US/developers/rules); the app must stay free.
"""
import json
import pathlib
import re
import sys

from PIL import Image

SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "../aoe4world-data")
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "renderer" / "img" / "game"
KINDS = {"units": "u", "technologies": "t", "buildings": "b", "upgrades": "g"}
CIVS = {"ab": "abbasid_dynasty", "ay": "ayyubids", "by": "byzantines", "ch": "chinese", "de": "delhi_sultanate",
        "en": "english", "fr": "french", "gol": "golden_horde", "hr": "holy_roman_empire", "hl": "house_of_lancaster",
        "ja": "japanese", "je": "jeanne_darc", "jin": "jin_dynasty", "kt": "knights_templar", "mac": "macedonian_dynasty",
        "ma": "malians", "mo": "mongols", "od": "order_of_the_dragon", "ot": "ottomans", "ru": "rus",
        "sen": "sengoku_daimyo", "tug": "tughlaq_dynasty", "zx": "zhu_xis_legacy", "sc": "scots", "vi": "vikings"}


def key(s):
    """How names are matched: lower case, letters and digits only ("Zhuge Nu" -> "zhugenu")."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


items, names, landmarks = {}, {}, {}
for folder, k in KINDS.items():
    data = json.loads((SRC / folder / "all-unified.json").read_text(encoding="utf-8"))["data"]
    (OUT / folder).mkdir(parents=True, exist_ok=True)
    for it in data:
        url = it.get("icon") or ""
        src = SRC / "images" / folder / url.rsplit("/", 1)[-1]
        if not src.exists():
            continue
        rel = f"{folder}/{it['id']}.webp"
        im = Image.open(src).convert("RGBA")
        im.thumbnail((96, 96), Image.LANCZOS)
        im.save(OUT / rel, "WEBP", quality=86, method=6)
        items[it["id"]] = {"n": it["name"], "k": k, "f": rel, "a": it.get("minAge")}
        for n in {it["name"], it["id"].replace("-", " ")}:
            names.setdefault(key(n), it["id"])            # first one wins: units before techs before buildings
        if folder == "buildings" and "landmark" in it.get("classes", []):
            for c in it.get("civs", []):
                landmarks.setdefault(CIVS.get(c, c), {}).setdefault(str(it.get("minAge") or 0), []).append(it["id"])
(OUT / "index.json").write_text(json.dumps({"items": items, "names": names, "landmarks": landmarks}, separators=(",", ":")))
size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
print(f"{len(items)} icons, {len(names)} names, {len(landmarks)} civs with landmarks, {size / 1e6:.1f} MB")
