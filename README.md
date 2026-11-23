# ovos-audio-plugin-browser

An [OpenVoiceOS](https://openvoiceos.org) audio backend that plays music,
internet radio and podcasts in a **browser's `<audio>` element**, over the
messagebus.

It was made for [Emkay](https://github.com/emkay-voice/emkay), OpenVoiceOS in
the browser (Pyodide), where it is the player behind ovos-audio's classic
`AudioService`. There it takes the place of
[ovos-audio-plugin-mpv](https://github.com/OpenVoiceOS/ovos-audio-plugin-mpv)
and vlc, which start a program on the device, which a browser cannot do.

## How it works

ovos-audio's `AudioService` (the `mycroft.audio.service.*` API that OCP uses
in legacy mode, and that skills reach through `AudioServiceInterface`) picks
a backend for each URI and tells it to play, pause, seek and so on. This
backend does not play anything itself: it hands each command to the page as
a bus message, and the page answers with what its player did.

So it never touches a browser API, and the same backend works

- in Emkay, where OVOS runs in a Web Worker and the page answers
  ([`web/media.js`](https://github.com/emkay-voice/emkay/blob/main/web/media.js)),
- on a normal OVOS device, with a browser connected to its messagebus
  websocket as the speaker.

Playlists, repeat, ducking while OVOS speaks or listens
(`ovos.audio.output.started`/`ended`, `recognizer_loop:record_begin`/`end`)
and the OCP status messages are handled by `AudioService` and OPM's
`AudioBackend` template, the same as for any other backend.

## Install and configure

```bash
pip install ovos-audio-plugin-browser
```

```json
{
  "Audio": {
    "default-backend": "browser",
    "backends": {
      "browser": {"type": "ovos_audio_browser", "active": true, "duck_factor": 0.3}
    }
  },
  "intents": {
    "ovos-ocp-pipeline-plugin": {"legacy": true}
  }
}
```

- `duck_factor`: the music's volume while OVOS speaks or listens, 0..1.
- `file`: whether the page can play `file://` tracks. A page can't open the
  device's files by itself, so this is off, except where OVOS runs in the
  page (Emkay, in Pyodide), which hands the page a track's bytes, or plays
  it from a folder the page was given.
- Without a `backends` entry of this type, one called `browser` is made, so
  installing the plugin is enough; set `default-backend` to make it the
  first choice.
- `"legacy": true` makes OCP play through `AudioService` (and this backend)
  instead of waiting for ovos-media.

## The protocol

To the page, one track at a time:

| message                       | data                       |
|-------------------------------|----------------------------|
| `ovos.audio.browser.play`     | `uri`, `position_ms`       |
| `ovos.audio.browser.pause`    |                            |
| `ovos.audio.browser.resume`   |                            |
| `ovos.audio.browser.stop`     |                            |
| `ovos.audio.browser.seek`     | `position_ms`              |
| `ovos.audio.browser.volume`   | `ducked` (bool), `factor` (0..1) |

From the page, whenever something happens, and every few seconds while
playing:

| message                       | data |
|-------------------------------|------|
| `ovos.audio.browser.state`    | `uri`, `state` (`playing`, `paused`, `ended`, `error`), `position_ms`, `length_ms` (0 for a live stream), `error` (a plain reason) |

On `ended` the backend goes on with the next track (or starts over with
repeat) and at the end of the list `AudioService` reports
`mycroft.audio.queue_end`. On `error` it reports the error to OCP the same
way.

A page that answers should play the stream in an `<audio>` element, not
through Web Audio: `<audio>` plays a stream from another site without that
site's CORS permission, while a Web Audio graph turns such a stream silent.

## Limits

- **https pages cannot play `http://` streams.** Browsers block this "mixed
  content". Many internet radio stations have an https address; those
  that do not fail with a clear reason, and OCP moves on to its next result.
- **Formats** are whatever the browser plays: mp3, aac, ogg/opus, and HLS
  in Safari. YouTube and the like need a stream extractor in OCP, which
  needs a site's permission to read it from a browser.
- The backend uses ovos-audio's classic audio service (`mycroft.plugin.audioservice`),
  which OPM marks as deprecated in favour of ovos-media. It is what OCP's
  legacy mode uses today; an ovos-media plugin can follow when Emkay moves.

## License

Apache-2.0
