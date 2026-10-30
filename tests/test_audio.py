"""SoundBank role pools, LayeredPlayer layering/looping/accents, engine
wiring, and the import-name sanitizer behind /api/audio/upload."""

import random
from pathlib import Path

import pytest

from dmx_volcano.audio import (
    LayeredPlayer,
    SoundBank,
    list_assets,
    resolve_asset,
    safe_asset_name,
)
from dmx_volcano.driver.fake import FakeDriver
from dmx_volcano.show import build_engine

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"


@pytest.fixture
def assets(tmp_path) -> Path:
    for name in ("a.wav", "b.wav", "c.wav", "stir.wav"):
        (tmp_path / name).write_bytes(b"RIFF")
    return tmp_path


class FakeProc:
    """Stands in for a player subprocess; tests control when it 'ends'."""

    def __init__(self, name: str):
        self.name = name
        self.ended = False
        self.terminated = False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.terminated = self.ended = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def player_log(monkeypatch):
    """Patch audio.play to record picks and hand back controllable procs."""
    procs: list[FakeProc] = []

    def fake_play(path: Path):
        proc = FakeProc(path.name)
        procs.append(proc)
        return proc

    monkeypatch.setattr("dmx_volcano.audio.play", fake_play)
    return procs


# ---- config parsing -----------------------------------------------------

def test_role_pools_parse(assets):
    bank = SoundBank.from_config(
        {
            "phases": {
                "active": {
                    "start": "a.wav",
                    "loop": ["b.wav", "c.wav"],
                    "accent": ["stir.wav"],
                    "accent_interval": [5.0, 9.0],
                },
            },
            "transitions": {"dormant->active": ["stir.wav"]},
        },
        assets,
    )
    active = bank.phase("active")
    assert active.start == ["a.wav"]
    assert active.loop == ["b.wav", "c.wav"]
    assert active.accent == ["stir.wav"]
    assert active.accent_interval == (5.0, 9.0)
    assert active.bg == []
    assert bank.transitions == {("dormant", "active"): ["stir.wav"]}


def test_bare_pool_is_start_shorthand(assets):
    """Legacy forms — flat [audio] and bare lists — mean one-shot on entry."""
    bank = SoundBank.from_config(
        {"active": "a.wav", "phases": {"imminent": ["b.wav"]}}, assets
    )
    assert bank.phase("active").start == ["a.wav"]
    assert bank.phase("imminent").start == ["b.wav"]


def test_unknown_names_fail_loud(assets):
    with pytest.raises(ValueError, match="unknown phase 'actve'"):
        SoundBank.from_config({"phases": {"actve": ["a.wav"]}}, assets)
    with pytest.raises(ValueError, match="unknown phase"):
        SoundBank.from_config({"transitions": {"dormant->lava": ["a.wav"]}}, assets)
    with pytest.raises(ValueError, match="unknown role 'lop'"):
        SoundBank.from_config({"phases": {"active": {"lop": ["a.wav"]}}}, assets)
    with pytest.raises(ValueError, match="dormant->active"):
        SoundBank.from_config({"transitions": {"dormant": ["a.wav"]}}, assets)
    with pytest.raises(ValueError, match="accent_interval"):
        SoundBank.from_config(
            {"phases": {"active": {"accent_interval": [9.0, 5.0]}}}, assets
        )


# ---- selection ----------------------------------------------------------

def test_pick_is_random_but_never_repeats_back_to_back(assets):
    bank = SoundBank.from_config(
        {"phases": {"active": {"loop": ["a.wav", "b.wav", "c.wav"]}}}, assets
    )
    bank.rng = random.Random(7)
    picks = [bank.phase_sound("active", "loop").name for _ in range(60)]
    assert set(picks) == {"a.wav", "b.wav", "c.wav"}  # everything gets airtime
    assert all(x != y for x, y in zip(picks, picks[1:]))  # no doubles


def test_roles_and_phases_draw_from_separate_pools(assets):
    bank = SoundBank.from_config(
        {
            "phases": {
                "active": {"loop": ["a.wav"], "accent": ["b.wav"]},
                "imminent": {"loop": ["c.wav"]},
            }
        },
        assets,
    )
    assert bank.phase_sound("active", "loop") == assets / "a.wav"
    assert bank.phase_sound("active", "accent") == assets / "b.wav"
    assert bank.phase_sound("imminent", "loop") == assets / "c.wav"
    assert bank.phase_sound("active", "bg") is None
    assert bank.phase_sound("dormant", "loop") is None
    assert bank.transition_sound("dormant", "active") is None


# ---- layered playback ---------------------------------------------------

def bank_for_player(assets) -> SoundBank:
    bank = SoundBank.from_config(
        {
            "phases": {
                "active": {
                    "start": ["a.wav"],
                    "loop": ["b.wav", "c.wav"],
                    "accent": ["stir.wav"],
                    "accent_interval": [5.0, 5.0],  # deterministic spacing
                },
            },
            "transitions": {"dormant->active": ["stir.wav"]},
        },
        assets,
    )
    bank.rng = random.Random(3)
    return bank


def test_enter_phase_layers_stinger_start_and_loop(assets, player_log):
    player = LayeredPlayer(bank_for_player(assets))
    player.enter_phase("dormant", "active")
    names = [p.name for p in player_log]
    assert names[:2] == ["stir.wav", "a.wav"]  # stinger, then the start one-shot
    assert names[2] in {"b.wav", "c.wav"} and len(names) == 3  # the loop bed


def test_loop_respawns_with_a_fresh_pick_when_it_ends(assets, player_log):
    player = LayeredPlayer(bank_for_player(assets))
    player.enter_phase("dormant", "active")
    loop = player_log[-1]
    player.tick(1.0)
    assert player_log[-1] is loop  # still playing: nothing respawned
    loop.ended = True
    player.tick(2.0)
    # Two-file pool + no-repeat: the respawned bed must be the other file.
    assert player_log[-1].name == ("c.wav" if loop.name == "b.wav" else "b.wav")


def test_accents_fire_on_schedule_and_are_capped(assets, player_log):
    player = LayeredPlayer(bank_for_player(assets))
    player.enter_phase("dormant", "active")
    baseline = len(player_log)
    player.tick(0.0)  # schedules first accent at t=5
    player.tick(4.9)
    assert len(player_log) == baseline
    player.tick(5.0)
    assert player_log[-1].name == "stir.wav" and len(player_log) == baseline + 1
    # Never-ending accents pile up only to the cap.
    for t in (10.0, 15.0, 20.0, 25.0, 30.0):
        player.tick(t)
    assert len(player_log) == baseline + 3  # MAX_CONCURRENT_ACCENTS


def test_phase_exit_stops_loops_but_one_shots_ring_out(assets, player_log):
    player = LayeredPlayer(bank_for_player(assets))
    player.enter_phase("dormant", "active")
    player.tick(0.0)
    player.tick(5.0)  # an accent is sounding
    stinger, start, loop, accent = player_log
    player.enter_phase("active", "dormant")  # fizzle: no dormant audio at all
    assert loop.terminated
    assert not any(p.terminated for p in (stinger, start, accent))
    player.stop_all()  # shutdown: nothing survives
    assert all(p.terminated for p in player_log)


def test_ringing_accents_leave_the_new_phase_its_full_accent_cap(assets, player_log):
    player = LayeredPlayer(bank_for_player(assets))
    player.enter_phase("dormant", "active")
    player.tick(0.0)
    for t in (5.0, 10.0, 15.0):
        player.tick(t)  # three never-ending accents: the cap
    player.enter_phase("active", "active")
    baseline = len(player_log)
    player.tick(20.0)
    player.tick(25.0)
    assert len(player_log) == baseline + 1  # the old phase's accents don't count


def test_cue_pool_picks_without_back_to_back_repeats(assets, player_log):
    bank = bank_for_player(assets)
    bank.add_cue(6.5, ["a.wav", "b.wav"])
    bank.add_cue(2.5, "c.wav")
    picks = [bank.cue_sound(6.5).name for _ in range(10)]
    assert all(x != y for x, y in zip(picks, picks[1:]))
    assert bank.cue_sound(2.5).name == "c.wav"
    assert bank.cue_sound(9.0) is None
    assert {"a.wav", "b.wav", "c.wav"} <= bank.referenced()
    player = LayeredPlayer(bank)
    player.play_one_shot(bank.cue_sound(2.5))
    player.enter_phase("eruption", "dormant")
    assert not player_log[0].terminated  # cue sounds ring out too
    player.stop_all()
    assert player_log[0].terminated


# ---- validation & description -------------------------------------------

def test_missing_files_reported_not_fatal(assets):
    bank = SoundBank.from_config(
        {
            "phases": {"active": {"loop": ["a.wav", "ghost.wav"]}},
            "transitions": {"dormant->active": ["phantom.wav"]},
        },
        assets,
    )
    assert bank.missing_files() == ["ghost.wav", "phantom.wav"]
    d = bank.describe()
    assert {"file": "ghost.wav", "missing": True} in d["phases"]["active"]["roles"]["loop"]
    assert d["phases"]["active"]["accent_interval"] == [10.0, 30.0]
    assert d["transitions"]["dormant->active"] == [
        {"file": "phantom.wav", "missing": True}
    ]


def test_list_assets_recurses_and_skips_non_audio(assets):
    (assets / "notes.txt").write_text("not audio")
    sub = assets / "sounds" / "active"
    sub.mkdir(parents=True)
    (sub / "z.wav").write_bytes(b"RIFF")
    assert list_assets(assets) == [
        "a.wav", "b.wav", "c.wav", "sounds/active/z.wav", "stir.wav"
    ]
    raw = assets / "raw" / "active"
    raw.mkdir(parents=True)
    (raw / "download.wav").write_bytes(b"RIFF")
    assert "raw/active/download.wav" not in list_assets(assets)  # unprocessed
    assert list_assets(assets / "nope") == []


def test_resolve_asset_allows_subpaths_but_never_escapes(assets):
    assert resolve_asset(assets, "sounds/active/z.wav") == assets / "sounds/active/z.wav"
    assert resolve_asset(assets, "a.wav") == assets / "a.wav"
    for bad in ["../a.wav", "/etc/a.wav", "x/../a.wav", ".hidden/a.wav", "a.sh", "",
                "raw/active/download.wav"]:
        with pytest.raises(ValueError):
            resolve_asset(assets, bad)


# ---- engine wiring (against the real repo config + corpus) ---------------

def test_repo_corpus_is_complete(tmp_path, monkeypatch):
    """Every file show.toml references must exist in assets/."""
    monkeypatch.chdir(tmp_path)
    eng, _, _ = build_engine(CONFIG, FakeDriver())
    assert eng.sounds.missing_files() == []


def test_engine_starts_dormant_bed_and_swaps_on_transition(
    tmp_path, monkeypatch, player_log
):
    monkeypatch.chdir(tmp_path)
    eng, _, _ = build_engine(CONFIG, FakeDriver())
    eng.machine.start(0.0)
    dormant = eng.sounds.phase("dormant")
    active = eng.sounds.phase("active")
    eng.step(0.0)  # startup: no transition, but the dormant bed must begin
    bed = player_log[0]
    assert f"sounds/dormant/{bed.name}" in dormant.loop
    eng.step(781.0)  # past the dormant dwell ceiling: dormant -> active
    assert bed.terminated  # old bed stopped
    assert f"sounds/active/{player_log[-2].name}" in active.loop
    assert f"sounds/active/{player_log[-1].name}" in active.bg


def test_engine_manual_trigger_starts_imminent_audio(tmp_path, monkeypatch, player_log):
    monkeypatch.chdir(tmp_path)
    eng, _, _ = build_engine(CONFIG, FakeDriver())
    eng.machine.start(0.0)
    eng.machine.request_eruption()
    eng.step(0.1)  # dormant -> imminent, before any startup bed
    stinger, bed, bg = (p.name for p in player_log)
    assert f"sounds/transitions/{stinger}" in eng.sounds.transitions[("dormant", "imminent")]
    assert f"sounds/imminent/{bed}" in eng.sounds.phase("imminent").loop
    assert f"sounds/imminent/{bg}" in eng.sounds.phase("imminent").bg
    eng.step(30.2)  # imminent runs its fixed 30s, then -> eruption
    assert f"sounds/eruption/{player_log[-2].name}" in eng.sounds.phase("eruption").start
    fired = len(player_log)
    eng.step(32.8)  # 2.6s into the eruption: the aftershocks cue
    assert player_log[fired].name == "cue_aftershocks.wav"


# ---- import sanitization ------------------------------------------------

def test_safe_asset_name_strips_paths():
    assert safe_asset_name("../../etc/evil.wav") == "evil.wav"
    assert safe_asset_name("  roar.MP3 ") == "roar.MP3"


@pytest.mark.parametrize("bad", ["", "   ", ".hidden.wav", "script.sh", "noext"])
def test_safe_asset_name_rejects_junk(bad):
    with pytest.raises(ValueError):
        safe_asset_name(bad)
