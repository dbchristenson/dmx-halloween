# assets/

Sound files for the show live here (see docs/SHOW_AUTHORING.md). Drop them
in directly (subfolders fine — the corpus lives in `sounds/<phase>/`) or
import via the control page's Audio panel. Referenced from config/show.toml
by path relative to this directory: `[audio.phases.<phase>]` role pools
(start/loop/bg/accent), `[audio.transitions]` stingers, and
`[[cues.eruption]]` sound cues.

Corpus format: 44.1kHz / 16-bit / stereo WAV — matches the Pi's dmix slave
(deploy/asound.conf) so nothing resamples at show time. Convert imports:
`ffmpeg -i in.mp3 -ar 44100 -ac 2 -sample_fmt s16 out.wav`.
