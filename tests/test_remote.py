"""Remote holds must control actual output, including failed/reordered delivery."""
import pytest
from test_manual import engine, enable_test_mode


def start(engine, kind='flame'):
    enable_test_mode(engine)
    engine.gate.arm()
    token = engine.console.begin_hold(kind)
    engine.console.renew_hold(token)
    return token


def channels(engine, frame):
    f = engine.rig['flame']
    return [frame[f.channel(k) - 1] for k in ('fire', 'safety')]


def test_hold_release_stops_flame_and_cannot_be_revived(engine):
    token = start(engine)
    assert channels(engine, engine.step(0)) == [0, 130]
    assert channels(engine, engine.step(.6)) == [255, 130]
    engine.console.end_hold(token)
    assert channels(engine, engine.step(.625)) == [0, 0]
    with pytest.raises(ValueError):
        engine.console.renew_hold(token)
    assert channels(engine, engine.step(.65)) == [0, 0]


def test_release_before_first_frame_never_fires(engine):
    token = start(engine)
    engine.console.end_hold(token)
    assert channels(engine, engine.step(0)) == [0, 0]
    assert channels(engine, engine.step(.6)) == [0, 0]


def test_lost_connection_expires_even_when_sim_time_stops(engine, monkeypatch):
    wall = [100.0]
    monkeypatch.setattr('dmx_volcano.manual.time.monotonic', lambda: wall[0])
    start(engine)
    engine.step(0)
    assert channels(engine, engine.step(.6)) == [255, 130]
    wall[0] += 1
    assert channels(engine, engine.step(.6)) == [0, 0]


@pytest.mark.parametrize('action', ['disable', 'disarm'])
def test_exit_or_disarm_cancels_hold(engine, action):
    token = start(engine)
    engine.step(0)
    engine.step(.6)
    if action == 'disable':
        engine.console.disable()
    else:
        engine.gate.disarm()
    assert channels(engine, engine.step(.625)) == [0, 0]
    engine.gate.arm()
    assert channels(engine, engine.step(.65)) == [0, 0]
    with pytest.raises(ValueError):
        engine.console.renew_hold(token)


def test_hold_maximum_does_not_repeat_and_gap_is_kept(engine):
    token = start(engine)
    engine.step(0)
    assert channels(engine, engine.step(.6)) == [255, 130]
    assert channels(engine, engine.step(10.6)) == [0, 0]
    with pytest.raises(ValueError):
        engine.console.renew_hold(token)
    # 10s fired in [0.5, 10.5): no fire until the 13s window frees >= 1s.
    token2 = engine.console.begin_hold('flame')
    engine.console.renew_hold(token2)
    assert channels(engine, engine.step(10.7)) == [0, 0]
    assert channels(engine, engine.step(11.3)) == [0, 0]


def test_hold_cannot_bypass_rehearsal(engine):
    enable_test_mode(engine)
    engine.gate.enabled_by_config = False
    token = engine.console.begin_hold('flame')
    engine.console.renew_hold(token)
    assert channels(engine, engine.step(0)) == [0, 0]
    assert channels(engine, engine.step(.6)) == [0, 0]


def test_co2_hold_stops_on_release(engine):
    token = start(engine, 'co2')
    ch = engine.rig['co2'].channel('jet') - 1
    assert engine.step(0)[ch] == 255
    engine.console.end_hold(token)
    assert engine.step(.025)[ch] == 0


def test_unconfirmed_or_expired_begin_never_activates(engine, monkeypatch):
    wall = [0.0]
    monkeypatch.setattr('dmx_volcano.manual.time.monotonic', lambda: wall[0])
    enable_test_mode(engine)
    engine.gate.arm()
    token = engine.console.begin_hold('flame')
    assert channels(engine, engine.step(0)) == [0, 0]
    wall[0] = 2
    with pytest.raises(ValueError):
        engine.console.renew_hold(token)
    assert channels(engine, engine.step(.6)) == [0, 0]


def test_wall_time_maximum_with_continuing_heartbeats(engine, monkeypatch):
    wall = [100.0]
    monkeypatch.setattr('dmx_volcano.manual.time.monotonic', lambda: wall[0])
    token = start(engine)
    engine.step(0)
    for i in range(1, 105):
        wall[0] = 100 + i / 10
        engine.console.renew_hold(token)
        engine.step(.6)
    assert channels(engine, engine.step(.6)) == [255, 130]
    wall[0] = 110.6
    assert channels(engine, engine.step(.6)) == [0, 0]
    with pytest.raises(ValueError):
        engine.console.renew_hold(token)


def test_release_during_arm_lead_closes_safety_without_fire(engine):
    token = start(engine)
    assert channels(engine, engine.step(0)) == [0, 130]
    engine.console.end_hold(token)
    assert channels(engine, engine.step(.2)) == [0, 0]
    assert channels(engine, engine.step(.6)) == [0, 0]


def test_hold_excludes_other_clients_and_legacy_bursts(engine):
    token = start(engine)
    with pytest.raises(ValueError):
        engine.console.begin_hold('flame')
    with pytest.raises(ValueError):
        engine.console.request_burst('flame', .5)
    engine.console.end_hold(token)
    engine.step(0)
    engine.console.request_burst('flame', .5)
    with pytest.raises(ValueError):
        engine.console.begin_hold('flame')


def test_remote_api_interlocks_and_disarm_invalidate_tokens(engine):
    from dmx_volcano.web.app import ArmRequest, create_app, HoldBeginRequest, HoldTokenRequest
    engine.state.arm_pin = '4242'
    app = create_app(engine.state)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, 'endpoint')}
    begin = routes['/api/remote/begin']
    renew = routes['/api/remote/renew']
    req = HoldBeginRequest(kind='flame')
    assert begin(req)['ok'] is False
    enable_test_mode(engine)
    assert begin(req)['ok'] is False
    engine.gate.arm()
    result = begin(req)
    token = HoldTokenRequest(token=result['token'])
    assert renew(token)['ok'] is True
    engine.step(0)
    assert channels(engine, engine.step(.6)) == [255, 130]
    routes['/api/disarm']()
    assert routes['/api/arm'](ArmRequest(pin='4242'))['ok']  # even rearming before the next frame must not revive it
    assert renew(token)['ok'] is False
    assert channels(engine, engine.step(.625)) == [0, 0]
    assert routes['/api/remote/release'](token)['ok'] is True


def test_co2_hold_accounts_for_actual_duration_not_maximum(engine):
    token = start(engine, 'co2')
    engine.step(0)
    engine.step(.1)
    engine.console.end_hold(token)
    engine.step(.125)
    assert engine.co2.used_kg == pytest.approx(.125 * engine.co2.rate_kg_per_s)


def test_co2_hold_at_limit_accounts_for_maximum(engine):
    start(engine, 'co2')
    engine.step(0)
    engine.step(4.1)
    assert engine.co2.used_kg == pytest.approx(4 * engine.co2.rate_kg_per_s)


def test_release_sends_zero_before_tank_persistence_and_survives_io_error(engine, monkeypatch):
    token = start(engine, 'co2')
    engine.step(0)
    ch = engine.rig['co2'].channel('jet') - 1
    def failed_save():
        assert engine.driver.last_frame[ch] == 0
        raise OSError('disk full')
    monkeypatch.setattr(engine.co2, '_save', failed_save)
    engine.console.end_hold(token)
    assert engine.step(.1)[ch] == 0
    assert engine.step(.125)[ch] == 0


def test_shutdown_blackouts_before_final_hold_accounting(engine, monkeypatch):
    start(engine, 'co2')
    engine.step(0)
    save = engine.co2._save
    def check_save():
        assert not any(engine.driver.last_frame)
        save()
    monkeypatch.setattr(engine.co2, '_save', check_save)
    engine.shutdown(.25)
    assert not any(engine.driver.last_frame)
    assert engine.co2.used_kg == pytest.approx(.25 * engine.co2.rate_kg_per_s)
    assert not engine.gate.pyro_enabled
    assert not engine.console.enabled


def arm_routes(engine, pin):
    from dmx_volcano.web.app import ArmRequest, create_app
    engine.state.arm_pin = pin
    routes = {r.path: r.endpoint for r in create_app(engine.state).routes if hasattr(r, 'endpoint')}
    return (lambda p: routes['/api/arm'](ArmRequest(pin=p))), routes['/api/disarm']


def test_arm_requires_the_pin_but_disarm_never_does(engine):
    arm, disarm = arm_routes(engine, '4242')
    assert arm('')['ok'] is False
    assert arm('0000')['reason'] == 'wrong PIN'
    assert not engine.gate.pyro_enabled
    assert arm('4242')['ok'] is True
    assert engine.gate.pyro_enabled
    assert disarm()['ok'] is True  # no PIN: anyone can always make it safe
    assert not engine.gate.pyro_enabled


def test_arming_is_refused_when_no_pin_is_configured(engine):
    arm, _ = arm_routes(engine, None)
    assert 'DMX_ARM_PIN' in arm('')['reason']
    assert not engine.gate.pyro_enabled


def test_repeated_wrong_pins_lock_out_even_the_right_one(engine, monkeypatch):
    from dmx_volcano.web import app as web
    clock = [1000.0]
    monkeypatch.setattr(web.time, 'monotonic', lambda: clock[0])
    arm, _ = arm_routes(engine, '4242')
    for guess in range(web.MAX_PIN_FAILURES):
        assert arm(f'{guess:04d}')['ok'] is False
    assert 'locked' in arm('4242')['reason']
    assert not engine.gate.pyro_enabled
    clock[0] += web.PIN_LOCKOUT_S
    assert arm('4242')['ok'] is True


def test_flame_hold_after_short_tap_gets_remaining_budget_without_cooldown(engine):
    token = start(engine)
    engine.step(0)
    assert channels(engine, engine.step(.6)) == [255, 130]
    engine.console.end_hold(token)
    assert channels(engine, engine.step(1.5)) == [0, 0]  # fired [0.5, 1.5)
    assert engine.gate.gap_remaining('flame', 1.5) == 0
    token = engine.console.begin_hold('flame')
    engine.console.renew_hold(token)
    engine.step(1.525)
    assert engine.console.last_burst.granted
    # 1s already fired in the window, so this hold gets the other 9s.
    assert engine.console.last_burst.duration == pytest.approx(9.0, abs=1e-6)
