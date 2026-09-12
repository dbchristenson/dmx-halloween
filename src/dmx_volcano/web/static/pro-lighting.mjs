// The preview consumes the post-safety output frame, never phase guesses.
export function fixtureOutput(fixture, frame) {
  const channels = fixture.channels;
  const value = name => {
    const address = channels[name];
    if (!Number.isInteger(address) || address < 1 || address > 512) return 0;
    const byte = frame?.[address - 1];
    return Number.isFinite(byte) ? Math.max(0, Math.min(255, byte)) : 0;
  };
  const safety = value('safety');
  return {
    rgb: ['red', 'green', 'blue'].map(value),
    dimmer: channels.dimmer === undefined ? 1 : value('dimmer') / 255,
    fog: value('fog') / 255,
    jet: value('jet') / 255,
    fire: safety >= 102 && safety <= 153 ? value('fire') / 255 : 0,
  };
}
