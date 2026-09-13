import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FlameEffect, CO2Effect } from '../src/dmx_volcano/web/static/pro-effects.mjs';

test('effect emitters start dark and stop immediately when output clears', () => {
  for (const Effect of [FlameEffect, CO2Effect]) {
    const effect = new Effect();
    assert.equal(effect.visible, false);
    effect.setLevel(1);
    effect.update(1, false);
    assert.equal(effect.visible, true);
    effect.setLevel(0);
    assert.equal(effect.visible, false);
    assert.equal(effect.level, 0);
  }
});

test('CO2 reaches a nine-meter column above its nozzle, distinct from the flame', () => {
  const co2 = new CO2Effect();
  const flame = new FlameEffect();
  assert.equal(co2.height, 9);
  assert.equal(flame.height, 2);
  co2.setLevel(1);
  co2.update(10, false);
  assert.ok(co2.reach < 1, 'The jet grows from its nozzle at burst start');
  co2.update(10.6, false);
  assert.equal(co2.reach, 1);
  co2.setLevel(0);
  co2.setLevel(1);
  co2.update(20, false);
  assert.ok(co2.reach < 1, 'A fresh burst must restart at the nozzle');
});

test('reduced motion keeps a fully visible static effect while retaining live on/off', () => {
  for (const Effect of [FlameEffect, CO2Effect]) {
    const effect = new Effect();
    effect.setLevel(.5);
    effect.update(1, true);
    const time = effect.material.uniforms.uTime.value;
    effect.update(100, true);
    assert.equal(effect.material.uniforms.uTime.value, time);
    assert.equal(effect.level, .5);
    if (effect instanceof CO2Effect) assert.equal(effect.reach, 1);
    effect.setLevel(0);
    assert.equal(effect.visible, false);
  }
});
