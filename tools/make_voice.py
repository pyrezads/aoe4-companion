"""
Records the coach's spoken calls ahead of time (app/renderer/voice/*.ogg).

The coach only says a fixed set of short phrases, so they're made once with an
open-source voice model and shipped as small audio files: nothing runs during
your game. Uses Kokoro-82M (Apache 2.0) through kokoro-onnx.

    pip install kokoro-onnx soundfile
    # model files: github.com/thewh1teagle/kokoro-onnx/releases (model-files-v1.0)
    python tools/make_voice.py --model kokoro-v1.0.int8.onnx --voices voices-v1.0.bin [--voice bm_george]

Change a phrase here, run it again, and commit the new .ogg files.
"""
import argparse
import json
import pathlib

PHRASES = {
    # next villager
    "vil_food": "Villager to food.",
    "vil_wood": "Villager to wood.",
    "vil_gold": "Villager to gold.",
    "vil_stone": "Villager to stone.",
    # idle villagers
    "idle_1": "Idle villager.",
    "idle_2": "Two idle villagers.",
    "idle_many": "Idle villagers. Put them to work.",
    # economy
    "housed": "You're housed. Build a house.",
    "floating": "Floating resources. Spend them.",
    "pop_flat": "Keep making villagers.",
    # age-up (when the build's age-up time comes)
    "age_2": "Time to age up to Feudal.",
    "age_3": "Time to age up to Castle.",
    "age_4": "Time to age up to Imperial.",
    # enemies (from the player list)
    "enemy_2": "Enemy reached Feudal Age.",
    "enemy_3": "Enemy reached Castle Age.",
    "enemy_4": "Enemy reached Imperial Age.",
    # build
    "build_done": "Build order complete. Keep villagers coming.",
    "test": "Coach voice is on.",
}

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--voices", required=True)
ap.add_argument("--voice", default="bm_george")
ap.add_argument("--speed", type=float, default=1.08)
ap.add_argument("--out", default=str(pathlib.Path(__file__).parent.parent / "app" / "renderer" / "voice"))
args = ap.parse_args()

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

k = Kokoro(args.model, args.voices)
out = pathlib.Path(args.out)
out.mkdir(parents=True, exist_ok=True)
for key, text in PHRASES.items():
    samples, rate = k.create(text, voice=args.voice, speed=args.speed, lang="en-gb" if args.voice.startswith("b") else "en-us")
    # trim silence at the ends so calls feel immediate
    loud = np.where(np.abs(samples) > 0.01)[0]
    if len(loud):
        samples = samples[max(0, loud[0] - int(0.02 * rate)): loud[-1] + int(0.08 * rate)]
    sf.write(out / f"{key}.ogg", samples, rate, format="OGG", subtype="VORBIS")
    print(f"{key:10s} {len(samples) / rate:4.1f}s  {text}")
(out / "phrases.json").write_text(json.dumps({"voice": args.voice, "model": "Kokoro-82M (Apache-2.0)", "phrases": PHRASES}, indent=1))
