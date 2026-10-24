"""
Records every spoken call ahead of time (app/renderer/voice/), in one voice.

The coach only says short, fixed sentences, so they're made once with an open-source voice model
and shipped as small Opus files: nothing runs during your game, and it never switches voices.
Uses Kokoro-82M (Apache 2.0) through kokoro-onnx.

    pip install kokoro-onnx soundfile
    # model files: github.com/thewh1teagle/kokoro-onnx/releases (model-files-v1.0)
    python tools/make_voice.py --model kokoro-v1.0.int8.onnx --voices voices-v1.0.bin [--voice bm_george]

Writes voice/<key>.ogg for the calls below, and voice/do/<id>.ogg ("Build a Mining Camp.",
"Research Wheelbarrow.", "Train a Spearman.") for every item in img/game/index.json
(run tools/make_icons.py first). Change a phrase here, run it again, commit the files.
"""
import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PHRASES = {
    # where the next villager goes (said when it changes)
    "next_food": "Next villager to food.",
    "next_wood": "Next villager to wood.",
    "next_gold": "Next villager to gold.",
    "next_stone": "Next villager to stone.",
    # the first seconds of a game
    "start_sheep": "Game on. Villagers to the sheep, and send your scout out to find more.",
    "start_berries": "Game on. Villagers to the berries.",
    "start_food": "Game on. Put your villagers on food.",
    "start_wood": "Game on. Villagers to wood.",
    "start_gold": "Game on. Villagers to gold.",
    "start": "Game on. Let's go.",
    # problems, most urgent first
    "housed": "You're housed. Build a house now.",
    "idle_1": "You have an idle villager.",
    "idle_2": "Two idle villagers. Get them working.",
    "idle_many": "Several idle villagers. Put them back to work.",
    "pop_flat": "Keep your town centre making villagers.",
    "floating": "You're floating resources. Spend them.",
    # ages
    "age_2": "Age up to Feudal now.",
    "age_3": "Age up to Castle now.",
    "age_4": "Age up to Imperial now.",
    "enemy_2": "The enemy reached Feudal.",
    "enemy_3": "The enemy reached Castle.",
    "enemy_4": "The enemy reached Imperial.",
    # build
    "build_done": "That's the end of the build. Keep making villagers.",
    "test": "Coach voice is on.",
}


def article(name: str) -> str:
    plural = name.endswith("s") and not name.endswith(("ss", "us"))
    if plural or name.lower().startswith("the "):
        return ""
    return "an " if name[0].lower() in "aeiou" else "a "


def item_phrase(it: dict) -> str:
    n = it["n"]
    if it["k"] == "b":
        return f"Build {article(n)}{n}."
    if it["k"] == "u":
        return f"Train {article(n)}{n}."
    return f"Research {n}."


ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--voices", required=True)
ap.add_argument("--voice", default="bm_george")
ap.add_argument("--speed", type=float, default=1.08)
ap.add_argument("--out", default=str(ROOT / "app" / "renderer" / "voice"))
ap.add_argument("--skip-items", action="store_true")
ap.add_argument("--force", action="store_true", help="record again even if the file exists")
args = ap.parse_args()

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

k = Kokoro(args.model, args.voices)
out = pathlib.Path(args.out)
(out / "do").mkdir(parents=True, exist_ok=True)
lang = "en-gb" if args.voice.startswith("b") else "en-us"


def record(text: str, path: pathlib.Path) -> float:
    samples, rate = k.create(text, voice=args.voice, speed=args.speed, lang=lang)
    loud = np.where(np.abs(samples) > 0.01)[0]                 # trim silence so calls feel immediate
    if len(loud):
        samples = samples[max(0, loud[0] - int(0.02 * rate)): loud[-1] + int(0.08 * rate)]
    sf.write(path, samples, rate, format="OGG", subtype="OPUS")
    return len(samples) / rate


for key, text in PHRASES.items():
    if (out / f"{key}.ogg").exists() and not args.force:
        continue
    print(f"{key:12s} {record(text, out / f'{key}.ogg'):4.1f}s  {text}")
items = {}
if not args.skip_items:
    idx = json.loads((ROOT / "app" / "renderer" / "img" / "game" / "index.json").read_text(encoding="utf-8"))
    order = {"b": 0, "t": 1, "u": 2, "g": 3}                      # buildings first: builds name them most
    for i, (iid, it) in enumerate(sorted(idx["items"].items(), key=lambda kv: order.get(kv[1]["k"], 9))):
        text = item_phrase(it)
        items[iid] = text
        if (out / "do" / f"{iid}.ogg").exists() and not args.force:
            continue
        record(text, out / "do" / f"{iid}.ogg")
        if i % 100 == 0:
            print(f"  items {i}/{len(idx['items'])}  {text}")
(out / "phrases.json").write_text(json.dumps({"voice": args.voice, "model": "Kokoro-82M (Apache-2.0)",
                                              "phrases": PHRASES, "items": len(items)}, indent=1))
size = sum(f.stat().st_size for f in out.rglob("*.ogg"))
print(f"{len(PHRASES)} calls, {len(items)} item calls, {size / 1e6:.1f} MB")
