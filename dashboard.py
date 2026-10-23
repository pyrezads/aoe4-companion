"""
Second-screen dashboard (Tkinter). Reads the shared state twice a second.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

BG, PANEL, LINE = "#0d1014", "#161b22", "#2a2f37"
TEXT, MUTED, DIM = "#e8e2d4", "#9a9385", "#5c574e"
GOLD, GREEN, RED, AMBER, BLUE = "#d9b45a", "#5cc58a", "#e5655b", "#e8a13c", "#62a8e5"
RES_COLOR = {"food": "#e0705a", "wood": "#b98a52", "gold": "#e6c34f", "stone": "#a7adb5"}
AGE_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}
F = "Segoe UI"
WRAP = 580


def civ_label(civ):
    from aoe4world import civ_name
    return civ_name(civ) if civ else "?"


class Dashboard:
    def __init__(self, state, monitor: Optional[dict] = None, on_hotkey=None):
        self.state = state
        self.on_hotkey = on_hotkey
        self.root = tk.Tk()
        self.root.title("AoE4 Live Analyst")
        self.root.configure(bg=BG)
        mw = monitor["width"] if monitor else 1280
        mh = monitor["height"] if monitor else 1000
        self.two_col = mw >= 1250
        w = 1240 if self.two_col else 640
        h = min(1000 if self.two_col else 1400, mh - 80)
        if monitor:
            self.root.geometry(f"{w}x{h}+{monitor['left'] + 20}+{monitor['top'] + 20}")
        else:
            self.root.geometry(f"{w}x{h}")
        self.running = True
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()

    # ── layout helpers ────────────────────────────────────────────────
    def _card(self, title, col=None):
        outer = tk.Frame(col if col is not None else self.col_a, bg=LINE)
        outer.pack(fill="x", padx=10, pady=4)
        inner = tk.Frame(outer, bg=PANEL, padx=12, pady=7)
        inner.pack(fill="x", padx=1, pady=1)
        tk.Label(inner, text=title.upper(), font=(F, 8, "bold"), fg=GOLD, bg=PANEL).pack(anchor="w")
        return inner

    def _label(self, parent, size=10, bold=False, color=TEXT):
        lab = tk.Label(parent, text="", font=(F, size, "bold" if bold else "normal"), fg=color, bg=PANEL,
                       anchor="w", justify="left", wraplength=WRAP)
        lab.pack(anchor="w", fill="x")
        return lab

    def _lines(self, parent, n, size=10, bold=False):
        box = tk.Frame(parent, bg=PANEL)          # own frame so lines can be hidden and re-shown in order
        box.pack(fill="x")
        return [tk.Label(box, text="", font=(F, size, "bold" if bold else "normal"), fg=TEXT, bg=PANEL,
                         anchor="w", justify="left", wraplength=WRAP) for _ in range(n)]

    @staticmethod
    def _set_lines(labels, rows, empty=None, empty_color=MUTED):
        """rows: list of (text, color). Unused lines are hidden so cards stay compact."""
        if not rows and empty:
            rows = [(empty, empty_color)]
        for lab in labels:
            lab.pack_forget()
        for lab, (text, color) in zip(labels, rows):
            lab.config(text=text, fg=color)
            lab.pack(anchor="w", fill="x")

    def _next_build(self):
        choices = self.state.get("build_choices")
        if not choices:
            return
        from buildorder import choice_state
        n = min(len(choices), 5)
        self.state.set(**choice_state(choices[:n], (self.state.get("build_pick") or 0) + 1))

    def _build(self):
        self.foot = tk.Label(self.root, text="", font=(F, 8), fg=DIM, bg=BG)
        self.foot.pack(side="bottom", fill="x", pady=4)
        top = tk.Frame(self.root, bg=BG, padx=12, pady=6)
        top.pack(fill="x")
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        self.col_a = tk.Frame(body, bg=BG)
        self.col_b = tk.Frame(body, bg=BG) if self.two_col else self.col_a
        self.col_a.pack(side="left", fill="both", expand=True)
        if self.two_col:
            self.col_b.pack(side="left", fill="both", expand=True)
        self.status = tk.Label(top, text="● Waiting for the game", font=(F, 11, "bold"), fg=DIM, bg=BG)
        self.status.pack(side="left")
        self.clock = tk.Label(top, text="--:--", font=(F, 20, "bold"), fg=TEXT, bg=BG)
        self.clock.pack(side="right")

        c = self._card("Match & teams")
        self.brief = self._lines(c, 6, 9)
        self.team_live = self._lines(c, 1, 10, True)

        c = self._card("Opponents (aoe4world, ranked games)")
        self.scout = self._lines(c, 7, 9)

        c = self._card("Economy")
        grid = tk.Frame(c, bg=PANEL)
        grid.pack(fill="x", pady=(4, 0))
        self.res = {}
        for i, r in enumerate(("food", "wood", "gold", "stone")):
            col = tk.Frame(grid, bg=PANEL)
            col.grid(row=0, column=i, sticky="w", padx=(0, 26))
            tk.Label(col, text=r.capitalize(), font=(F, 9, "bold"), fg=RES_COLOR[r], bg=PANEL).pack(anchor="w")
            vil = tk.Label(col, text="–", font=(F, 22, "bold"), fg=TEXT, bg=PANEL)
            vil.pack(anchor="w")
            target = tk.Label(col, text="", font=(F, 9), fg=MUTED, bg=PANEL)
            target.pack(anchor="w")
            inc = tk.Label(col, text="", font=(F, 8), fg=DIM, bg=PANEL)
            inc.pack(anchor="w")
            self.res[r] = (vil, target, inc)
        row = tk.Frame(c, bg=PANEL)
        row.pack(fill="x", pady=(6, 0))
        self.pop = tk.Label(row, text="Pop –", font=(F, 11, "bold"), fg=TEXT, bg=PANEL)
        self.pop.pack(side="left")
        self.idle = tk.Label(row, text="Idle –", font=(F, 11, "bold"), fg=TEXT, bg=PANEL)
        self.idle.pack(side="left", padx=18)
        self.vtotal = tk.Label(row, text="", font=(F, 9), fg=MUTED, bg=PANEL)
        self.vtotal.pack(side="left")

        c = self._card("Alerts")
        self.alert_labels = self._lines(c, 5, 11, True)

        c = self._card("Build order", self.col_b)
        self.bo = self._lines(c, 7, 10, True)
        bh = tk.Frame(c, bg=PANEL)
        bh.pack(fill="x", pady=(4, 0))
        self.bo_pick = tk.Label(bh, text="", font=(F, 8), fg=DIM, bg=PANEL)
        self.bo_pick.pack(side="left")
        tk.Button(bh, text="Other build", font=(F, 8, "bold"), fg=BG, bg=GOLD, relief="flat",
                  command=self._next_build).pack(side="right")

        c = self._card("Your army (updates on Ctrl+C)", self.col_b)
        self.army = self._lines(c, 4, 10)

        c = self._card("Enemy & counters", self.col_b)
        self.enemy = self._lines(c, 1, 10)
        self.advice = self._lines(c, 3, 10, True)
        hint = tk.Frame(c, bg=PANEL)
        hint.pack(fill="x", pady=(4, 0))
        tk.Label(hint, text="Click an enemy unit to log it, or select enemies and press F9.",
                 font=(F, 8), fg=DIM, bg=PANEL).pack(side="left")
        tk.Button(hint, text="Pin selection as enemy", font=(F, 8, "bold"), fg=BG, bg=GOLD, relief="flat",
                  activebackground="#b8963f", cursor="hand2", command=self._hotkey).pack(side="right")

        c = self._card("Upgrades for your army", self.col_b)
        self.ups = self._lines(c, 4, 10)

    def _hotkey(self):
        if self.on_hotkey:
            self.on_hotkey()

    # ── refresh ───────────────────────────────────────────────────────
    def _refresh(self):
        if not self.running:
            return
        try:
            self._refresh_inner()
        except Exception:
            import traceback
            traceback.print_exc()
        self.root.after(500, self._refresh)

    def _refresh_inner(self):
        st = self.state.copy()
        snap = st.get("snapshot")
        live = bool(snap and snap.live)
        if live:
            self.status.config(text="● LIVE", fg=GREEN)
            self.clock.config(text=snap.clock or "--:--")
        elif st.get("game_seen"):
            self.status.config(text="● Paused / not readable", fg=AMBER)
        else:
            self.status.config(text="● Waiting for the game", fg=DIM)

        brief = st.get("briefing_lines") or ([st["match_note"]] if st.get("match_note") else [])
        self._set_lines(self.brief, [(t, TEXT if i else GOLD) for i, t in enumerate(brief)])

        teams = st.get("teams")
        if teams and teams.get("me"):
            def fmt(p):
                return f"{civ_label(p['civ'])} {AGE_ROMAN.get(p['age'], '?')}"
            parts = [f"You: {fmt(teams['me'])}"]
            if teams["allies"]:
                parts.append("Allies: " + " · ".join(fmt(a) for a in teams["allies"]))
            if teams["enemies"]:
                parts.append("Enemies: " + " · ".join(fmt(e) for e in teams["enemies"]))
            self._set_lines(self.team_live, [("   ".join(parts), TEXT)])
        else:
            self._set_lines(self.team_live, [])

        rep = st.get("scout")
        rows = []
        if rep:
            rows += [(t, GOLD) for t in rep.get("summary", [])]
            for t in rep.get("enemies", []):
                hot = any(k in t for k in ("hot streak", "strong on this civ", "climbing fast", "Biggest threat"))
                rows.append((t, RED if hot else TEXT))
            if rep.get("allies"):
                rows.append(("Allies: " + "  |  ".join(rep["allies"]), MUTED))
        self._set_lines(self.scout, rows, "Appears when aoe4world shows you in a ranked match (20-40 s in).")

        cmp = st.get("build_compare") or {}
        step = cmp.get("step")
        if snap:
            for r, (vil, target, inc) in self.res.items():
                v = snap.villagers.get(r)
                vil.config(text="–" if v is None else str(v))
                want = step.targets.get(r) if step else None
                gap = (cmp.get("gaps") or {}).get(r)
                if want is not None:
                    color = GREEN if not gap else (RED if gap > 0 else AMBER)
                    target.config(text=f"build {want}" + (f" ({'+' if gap > 0 else ''}{gap})" if gap else " ✓"), fg=color)
                else:
                    target.config(text="")
                rate = snap.income_est.get(r)
                b = snap.bank.get(r)
                inc.config(text=(f"~{rate:.0f}/min" if rate else "") + (f" · {b}" if b is not None and b < 50000 else ""))
            if snap.pop is not None:
                self.pop.config(text=f"Pop {snap.pop}/{snap.pop_cap}")
            if snap.idle is not None:
                self.idle.config(text=f"Idle {snap.idle}", fg=RED if snap.idle >= 2 else (AMBER if snap.idle else GREEN))
            if snap.villagers_total is not None:
                self.vtotal.config(text=f"{snap.villagers_total} villagers working/idle")

        alerts = list(st.get("alerts") or []) if live else []
        self._set_lines(self.alert_labels,
                        [(("▲ " if lvl == "warn" else "• ") + t, RED if lvl == "warn" else AMBER) for lvl, t in alerts],
                        "All good.", GREEN)

        bo = st.get("build")
        if bo:
            rows = [(bo.title, GOLD)]
            co = st.get("coach") if live else None
            if co:
                r = co["next"]["resource"]
                txt = f"Villager {co['villagers'] + 1} → {r.upper()}" + (f", then {co['next']['then']}" if co["next"]["then"] else "")
                rows.append((txt, RES_COLOR.get(r, TEXT)))
                if co["idle_to"]:
                    rows.append((f"{len(co['idle_to'])} idle → send to " + ", ".join(dict.fromkeys(co["idle_to"])), RED))
                p = co["pace_seconds"]
                if abs(p) >= 5:
                    rows.append((f"{abs(p)} s {'behind' if p > 0 else 'ahead of'} the build", AMBER if p > 0 else GREEN))
            if st.get("build_why"):
                rows.append(("Why: " + st.get("build_why"), MUTED))
            if co:                                         # the coach follows by villager count
                rows.append(("Now: " + co["step"]["text"], TEXT))
                up = co.get("upcoming")
                if up:
                    rows.append((f"Next ({up['villagers']} villagers, {up['at']}): {up['text']}", MUTED))
            else:
                rows.append((cmp.get("summary", ""), GREEN if not cmp.get("gaps") else AMBER))
                if step and step.text:
                    rows.append(("Now: " + step.text, TEXT))
                nxt = cmp.get("next")
                if nxt:
                    when = f"{nxt.seconds // 60}:{nxt.seconds % 60:02d}" if nxt.seconds is not None else "next"
                    rows.append((f"Next ({when}): {nxt.text}", MUTED))
            self._set_lines(self.bo, [r for r in rows if r[0]])
            n = st.get("build_count")
            self.bo_pick.config(text=f"Pick {st.get('build_pick', 0) + 1} of {n} for this game" if n else "From your config.json")
        else:
            self.bo_pick.config(text="")
            self._set_lines(self.bo, [(st.get("build_note") or "No build loaded", MUTED)])

        army = st.get("army_lines") or []
        rows = [(t, TEXT if i < 2 else (RED if t.startswith("Lost") else MUTED)) for i, t in enumerate(army)]
        self._set_lines(self.army, rows, "Press Ctrl+C in game to read your army.")

        self._set_lines(self.enemy, [(t, TEXT) for t in (st.get("enemy_lines") or [])],
                        "Waiting for the player list or an enemy unit.")
        color = {"good": GREEN, "bad": RED, "even": AMBER, "info": TEXT}
        self._set_lines(self.advice, [(t, color.get(k, TEXT)) for k, t in (st.get("advice_lines") or [])])

        self._set_lines(self.ups, [(t, TEXT) for t in (st.get("upgrade_lines") or [])],
                        "Shown after your first Ctrl+C. Research what you haven't yet.")

        q = st.get("read_quality")
        missed = st.get("missed") or []
        self.foot.config(text=(f"reading {q:.0%}" if q is not None else "")
                         + (f" (missed: {', '.join(missed[:3])})" if missed and live else "")
                         + "   ·   Ctrl+C = your army   ·   F9 = pin enemy   ·   reads your game screen only")

    def run(self):
        self.root.after(300, self._refresh)
        self.root.mainloop()

    def close(self):
        self.running = False
        self.root.destroy()
