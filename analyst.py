"""
The analyst: turns screen reads into advice. One instance per app run.

  on_frame(frame)      every second: HUD economy, player list every 3 s, enemy units you click
  on_army_key(frame)   after Ctrl+C: your army by unit type
  on_pin(frame)        F9: treat the current selection as enemy units

Publishes everything the dashboard shows into the shared state.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from aoe4world import civ_name
from coach import Coach
from combat import TechAdvisor, best_counters, fight, needed_to_win, villagers_to_produce
from hud_reader import HudReader
from scoreboard import ScoreboardReader, TeamTracker
from tracker import ArmyTracker, EcoTracker
from units import CARD, CARD_STEP, CARD_X0, CARD_Y0, UnitDB, UnitRecognizer
import production_math as pm

AGE_NAME = {1: "Dark", 2: "Feudal", 3: "Castle", 4: "Imperial"}


def _plain(name: str) -> str:
    for p in ("Early ", "Hardened ", "Veteran ", "Elite "):
        name = name.replace(p, "")
    return name


class Analyst:
    def __init__(self, state, cfg: dict):
        self.state = state
        self.cfg = cfg
        self.hud = HudReader()
        self.eco = EcoTracker()
        self.army = ArmyTracker()
        self.sbr = ScoreboardReader()
        self.teams = TeamTracker()
        self.db = UnitDB()
        self.rec = UnitRecognizer(self.db)
        self.techs = TechAdvisor(self.db)
        self.last_sb = 0.0
        self.last_sel_sig = None
        self.last_army_key = 0.0
        self.my_army: List[Tuple[dict, int, str]] = []          # (variant, count, unit id)
        self.enemy_seen: Dict[str, Dict] = {}                     # base -> {variant, civ, clock, t}
        self.team_events: List[Tuple[float, str]] = []
        self.last_seconds: Optional[int] = None
        self.coach: Optional[Coach] = None
        self.coach_for = None                                     # the BuildOrder the coach follows

    # ── who am I ──────────────────────────────────────────────────────
    def my_civ(self) -> Optional[str]:
        m = self.state.get("match")
        if m:
            me = next((p for p in m.players if p.profile_id == int(self.cfg.get("aoe4world_profile_id", 0))), None)
            if me:
                return me.civ
        t = self.teams.summary().get("me") or {}
        return t.get("civ") or self.cfg.get("default_civ") or None

    def my_age(self) -> int:
        t = self.teams.summary().get("me") or {}
        return t.get("age") or 2

    def enemy_civs(self) -> List[str]:
        m = self.state.get("match")
        if m:
            return [p.civ for p in m.enemies()]
        return [e["civ"] for e in self.teams.summary()["enemies"] if e["civ"]]

    def enemy_age(self, civ: Optional[str] = None) -> int:
        es = self.teams.summary()["enemies"]
        ages = [e["age"] for e in es if e["age"] and (civ is None or e["civ"] == civ)]
        return max(ages) if ages else self.my_age()

    # ── per frame ─────────────────────────────────────────────────────
    def on_frame(self, frame, now: Optional[float] = None) -> None:
        now = now or time.time()
        h = self.hud.read(frame)
        snap = self.eco.update(h, now)
        if snap.live and self.last_seconds is not None and snap.seconds is not None and snap.seconds + 30 < self.last_seconds:
            self.new_game()
        if snap.live:
            self.last_seconds = snap.seconds
        alerts = [(a.level, a.text) for a in snap.alerts] if snap.live else []

        if snap.live and now - self.last_sb >= 3:
            self.last_sb = now
            for ev in self.teams.update(self.sbr.read(frame), snap.clock):
                self.team_events.append((now, ev))
                print(f"  [{snap.clock}] {ev}")
            self.state.set(teams=self.teams.summary())
        self.team_events = [(t, e) for t, e in self.team_events if now - t < 45]
        alerts += [("warn", e) for _, e in self.team_events if e.startswith("Enemy")][:3]

        # enemy units: a selection that isn't your Ctrl+C army
        if snap.live and h.selection_total and now - self.last_army_key > 2.5:
            sig = (h.selection_total, tuple(h.selection_counts))
            if sig != self.last_sel_sig:
                self.last_sel_sig = sig
                self._maybe_enemy_selection(frame, h, snap.clock, now, forced=False)
        elif not h.selection_total:
            self.last_sel_sig = None

        self.state.set(snapshot=snap, last_hud=h, read_quality=h.confidence, missed=list(h.missed), alerts=alerts)
        if snap.live:
            self.state.set(game_seen=True)
            bo = self.state.get("build")
            if bo and snap.seconds is not None:
                self.state.set(build_compare=bo.compare(snap.seconds, snap.villagers))
            if bo is not self.coach_for:                       # a build was picked or changed
                self.coach_for = bo
                self.coach = Coach(bo, by=self.cfg.get("coach_follow_by", "villagers")) if bo else None
                self.state.set(coach=None)
            if self.coach:
                age = (self.teams.summary().get("me") or {}).get("age")
                self.state.set(coach=self.coach.update(snap.seconds, snap.villagers, snap.idle, age))

    def new_game(self):
        self.army.reset()
        self.teams.reset()
        self.my_army = []
        self.enemy_seen.clear()
        self.team_events.clear()
        if self.coach:
            self.coach.reset()
        self.state.set(coach=None, army_lines=None, enemy_lines=None, advice_lines=None, upgrade_lines=None, teams=None)

    def coach_skip(self, n: int = 1) -> None:
        """F10: move the coach one step on (n=-1 goes back)."""
        if self.coach:
            self.coach.skip(n)

    # ── your army (Ctrl+C) ────────────────────────────────────────────
    def on_army_key(self, frame) -> None:
        self.last_army_key = time.time()
        h = self.hud.read(frame)
        if h.confidence < 0.6 or not h.selection_total:
            return
        civ = self.my_civ()
        age = self.my_age()
        matches = self.rec.read_cards(frame, h.selection_counts, [civ] if civ else None)
        army = []
        for m in matches:
            if m.unit_id == "?":
                continue
            v = self.db.best_variant(m.unit_id, civ, age)
            if v and "military" in (v.get("classes") or []):
                army.append((v, m.count, m.unit_id))
        self.my_army = army
        named = [(n, self.db.base_name(uid, civ)) for _, n, uid in army]
        unknown = sum(m.count for m in matches if m.unit_id == "?")
        self.army.record(h.seconds, h.clock, h.selection_total, h.selection_counts)
        lines = self.army.summary()
        if named:
            lines.insert(1, " · ".join(f"{n} {name}" for n, name in sorted(named, key=lambda t: -t[0]))
                         + (f" · {unknown} other" if unknown else ""))
        self.state.set(army_lines=lines)
        self._advise()

    # ── enemy units ───────────────────────────────────────────────────
    def on_pin(self, frame) -> None:
        h = self.hud.read(frame)
        if h.selection_total:
            self._maybe_enemy_selection(frame, h, h.clock, time.time(), forced=True)

    def _maybe_enemy_selection(self, frame, h, clock, now, forced: bool):
        enemies = self.enemy_civs()
        mine = self.my_civ()
        if not enemies and not forced:
            return
        found = []
        for i, n in enumerate(h.selection_counts):
            x = CARD_X0 + i * CARD_STEP
            card = frame[CARD_Y0:CARD_Y0 + CARD, x:x + CARD]
            icon_e, s_e = self.rec.classify_card(card, enemies or None)
            icon_m, s_m = self.rec.classify_card(card, [mine]) if mine else (None, -1.0)
            if s_e < 0.8:
                continue
            uid = self.rec.unit_for_icon(icon_e, enemies or None)
            # a unit your civ can also have looks the same; only F9 says it's theirs
            if not forced and mine and s_m >= s_e - 0.01:
                continue
            civ = next((c for c in enemies if c in self.db.civs_of(uid)), enemies[0] if enemies else None)
            v = self.db.best_variant(uid, civ, self.enemy_age(civ))
            if v and "military" in (v.get("classes") or []):
                found.append((v, uid, civ))
        for v, uid, civ in found:
            self.enemy_seen[v.get("base") or uid] = {"variant": v, "uid": uid, "civ": civ, "clock": clock, "t": now}
        if found:
            print(f"  [{clock}] Enemy units seen: " + ", ".join(self.db.base_name(u, c) for _, u, c in found))
            self._advise()

    # ── advice ────────────────────────────────────────────────────────
    def _enemy_mix(self) -> Tuple[List[Tuple[dict, int]], str]:
        """Enemy units recently seen; otherwise the enemy civs' signature units."""
        now = time.time()
        recent = [e for e in self.enemy_seen.values() if now - e["t"] < 300]
        if recent:
            return [(e["variant"], 1) for e in recent], "seen"
        mix = []
        for civ in dict.fromkeys(self.enemy_civs()):
            for uid in self.db.signature_units(civ, self.enemy_age(civ)):
                v = self.db.best_variant(uid, civ, self.enemy_age(civ))
                if v:
                    mix.append((v, 1))
        return mix, "expected"

    def _advise(self):
        civ = self.my_civ()
        age = self.my_age()
        mix, kind = self._enemy_mix()
        enemy_lines, advice = [], []
        if mix:
            names = ", ".join(dict.fromkeys(_plain(v["name"]) for v, _ in mix))
            enemy_lines.append(("Seen: " if kind == "seen" else "Expect (enemy civs' signature units): ") + names)
        if civ and mix:
            # scale the enemy mix to the same population as your army
            my_pop = sum(n * (v.get("pop") or 1) for v, n, _ in self.my_army) or 40
            per = max(1, round(my_pop / max(len(mix), 1)))
            theirs = [(v, per) for v, _ in mix]
            if self.my_army:
                f = fight([(v, n) for v, n, _ in self.my_army], theirs)
                if f:
                    advice.append(("good" if f.you_win and f.ratio > 1.18 else "bad" if not f.you_win else "even",
                                   f"Your army vs an equal-size army of those: {f.text}"))
            counters = best_counters(self.db, civ, age, theirs)
            if counters:
                advice.append(("info", "Best counters for you: " + ", ".join(self.db.base_name(u, civ) for _, u, _ in counters)))
                top_v, top_uid, _ = counters[0]
                mine_now = [(v, n) for v, n, _ in self.my_army]
                n = needed_to_win(self.db, mine_now, theirs, top_v, 1.3, max_n=120) if mine_now else None
                rates = pm.gathering_rates("farm", wheelbarrow=True)
                vill = villagers_to_produce(top_v, 2, rates)
                need = ", ".join(f"{v:.0f} on {r}" for r, v in vill.items() if v >= 0.5)
                name = self.db.base_name(top_uid, civ)
                if n:
                    advice.append(("info", f"Add ~{n} {name} to be safe. Two buildings non-stop need about {need}."))
                elif need:
                    advice.append(("info", f"Keep {name} coming: two buildings non-stop need about {need}."))
        ups = self.techs.for_army(civ, age, [(v, n) for v, n, _ in self.my_army]) if civ and self.my_army else []
        upgrade_lines = [f"{name} ({b}, {AGE_NAME.get(a, a)}) · helps {n} units" for name, b, a, n in ups[:4]]
        self.state.set(enemy_lines=enemy_lines, advice_lines=advice, upgrade_lines=upgrade_lines)

    def refresh_advice_if_needed(self, now: float):
        """Recompute expected threats when the teams change (no clicks needed)."""
        sig = (self.my_civ(), tuple(self.enemy_civs()), self.my_age(), self.enemy_age())
        if sig != getattr(self, "_adv_sig", None) and sig[1]:
            self._adv_sig = sig
            self._advise()
