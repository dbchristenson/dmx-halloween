import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fixtureOutput } from '../src/dmx_volcano/web/static/pro-lighting.mjs';

test('reads configured one-based DMX addresses, preserving RGB and dimmer separately', () => {
  const fixture = { channels: { dimmer: 30, red: 31, green: 32, blue: 33 } };
  const frame = Array(512).fill(0);
  frame.splice(29, 4, 128, 255, 80, 0);
  assert.deepEqual(fixtureOutput(fixture, frame), {
    rgb: [255, 80, 0], dimmer: 128 / 255, fog: 0, jet: 0, fire: 0,
  });
  frame[29] = 0;
  assert.equal(fixtureOutput(fixture, frame).dimmer, 0);
});

test('fixtures without master dimmers still render their RGB channels', () => {
  const fixture = { channels: { fog: 1, red: 2, green: 3, blue: 4 } };
  assert.deepEqual(fixtureOutput(fixture, [128, 20, 100, 255]), {
    rgb: [20, 100, 255], dimmer: 1, fog: 128 / 255, jet: 0, fire: 0,
  });
});

test('flame preview requires fire output AND an open safety channel', () => {
  const fixture = { channels: { safety: 1, fire: 2 } };
  for (const safety of [0, 101, 154, 255]) {
    assert.equal(fixtureOutput(fixture, [safety, 255]).fire, 0);
  }
  for (const safety of [102, 127, 153]) {
    assert.equal(fixtureOutput(fixture, [safety, 128]).fire, 128 / 255);
  }
  assert.equal(fixtureOutput(fixture, [127, 0]).fire, 0);
});

test('missing frames and invalid channels do not invent output', () => {
  const fixture = { channels: { dimmer: 0, red: 513, green: 1, blue: 2, jet: 3 } };
  assert.deepEqual(fixtureOutput(fixture, [NaN, -10, 999]), {
    rgb: [0, 0, 0], dimmer: 0, fog: 0, jet: 1, fire: 0,
  });
  assert.deepEqual(fixtureOutput(fixture, null), {
    rgb: [0, 0, 0], dimmer: 0, fog: 0, jet: 0, fire: 0,
  });
});
