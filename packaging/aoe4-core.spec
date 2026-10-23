# PyInstaller spec for the Python core: pyinstaller packaging/aoe4-core.spec --distpath dist-core
# Gives dist-core/aoe4-core/aoe4-core.exe plus its files. The installer puts that folder in resources/core.
import pathlib

ROOT = pathlib.Path(SPECPATH).parent

# Modules imported inside functions; listed so they're always packed.
hidden = ["core", "analyst", "coach", "combat", "tracker", "hud_reader", "scoreboard", "units",
          "screen", "gamewindow", "review", "history", "minimap", "aoe4world", "buildorder", "production_math", "mss", "keyboard",
          "windows_capture", "windows_capture.windows_capture"]

a = Analysis(
    [str(ROOT / "packaging" / "core_entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "data"), "data")],          # glyphs, flags, unit data: read next to the modules
    hiddenimports=hidden,
    excludes=["tkinter", "dashboard", "matplotlib", "scipy", "pytest", "cv2"],   # OpenCV: see screen._import_windows_capture
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="aoe4-core",
          console=True,        # keeps stdin/stdout pipes; the app starts it hidden, so no window shows
          icon=str(ROOT / "build" / "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="aoe4-core")
