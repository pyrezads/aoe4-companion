"""Starts a packaged core, replays a screenshot, and checks it answers like the app expects.

    python packaging/smoke_core.py dist-core/aoe4-core/aoe4-core.exe
"""
import json
import subprocess
import sys
import tempfile
import threading
import time

exe = sys.argv[1]
cfg = tempfile.mkdtemp() + "/config.json"
p = subprocess.Popen([exe, "--core", "--offline", "--config", cfg, "--replay", "calib/s07.png"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
types, lines = set(), []


def read():
    for line in p.stdout:
        lines.append(line)
        types.add(json.loads(line)["type"])


threading.Thread(target=read, daemon=True).start()
time.sleep(8)
p.stdin.write('{"cmd": "reading_check"}\n')
p.stdin.flush()
time.sleep(3)
p.stdin.write('{"cmd": "quit"}\n')
p.stdin.flush()
try:
    p.wait(10)
except subprocess.TimeoutExpired:
    p.kill()
need = {"ready", "status", "hud", "reading_check"}
print("messages:", sorted(types))
check = next((json.loads(l)["data"] for l in lines if '"reading_check"' in l), None)
print("reading check:", check and round(check["confidence"], 2))
if not need <= types or not check or check["confidence"] < 0.9:
    print("FAIL: missing", need - types, "\n--- stderr ---\n", p.stderr.read()[-3000:])
    sys.exit(1)
print("OK: the packaged core runs and reads the screen")
