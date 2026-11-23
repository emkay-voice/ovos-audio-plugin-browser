"""An ovos-audio backend that plays in a browser's <audio> element.

ovos-audio's classic AudioService (`mycroft.audio.service.*`, what OCP
uses in "legacy" mode and what skills' AudioServiceInterface talks to)
plays through backend plugins: mpv, vlc, simple. Those start a process
on the device. In a browser there is no process to start, but there is
the page's own media player, `<audio>`, which plays mp3, aac, ogg and
most internet radio straight from its URL.

This backend is that player, spoken to over the messagebus. It never
touches the page itself; it sends `ovos.audio.browser.*` messages and
listens for one, `ovos.audio.browser.state`, coming back. Any page that
answers them is the speaker:

- Emkay (OVOS in the browser, Pyodide) answers them from its own page;
- a browser connected to a normal OVOS device over the messagebus
  websocket could answer them as well.

The protocol is in README.md. Playlists, repeat, ducking while OVOS
speaks or listens, and the OCP status messages are the same as for any
other backend: AudioService and the AudioBackend template handle them,
this module only plays one track at a time and reports what happened.
"""
import sys
import time
from typing import List

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.audio import AudioBackend
from ovos_utils.log import LOG

#: The backend `type` in mycroft.conf's Audio.backends.
BACKEND_TYPE = "ovos_audio_browser"

#: Message types, page bound ...
PLAY = "ovos.audio.browser.play"        # {uri, position_ms}
PAUSE = "ovos.audio.browser.pause"
RESUME = "ovos.audio.browser.resume"
STOP = "ovos.audio.browser.stop"
SEEK = "ovos.audio.browser.seek"        # {position_ms}
VOLUME = "ovos.audio.browser.volume"    # {ducked, factor}
#: ... and the one coming back from the page.
STATE = "ovos.audio.browser.state"      # {uri, state, position_ms, length_ms, error}


class BrowserAudioBackend(AudioBackend):
    """Plays one track at a time in the page's <audio> element.

    Config (Audio.backends.<name>):
        type: "ovos_audio_browser"
        active: true
        duck_factor: volume while OVOS speaks or listens, 0..1 (default 0.3)
    """

    def __init__(self, config=None, bus=None, name="browser"):
        super().__init__(config=config, bus=bus, name=name)
        self.duck_factor = float(self.config.get("duck_factor", 0.3))
        self._repeat = False
        self._state = "stopped"     # stopped | loading | playing | paused
        self._position_ms = 0
        self._length_ms = 0         # 0: not known yet, or a live stream
        self._stamp = time.monotonic()
        self._announced = False
        self.bus.on(STATE, self._on_state)

    # ------------------------------------------------------------ to the page
    def _send(self, msg_type: str, data: dict = None):
        """Hand a command to the page, keeping the utterance's context, so
        it reaches the same client (session) the request came from."""
        self.bus.emit(self._format_msg(msg_type, data or {}))

    def supported_uris(self) -> List[str]:
        # what <audio> fetches by itself. A file:// track is not reachable
        # from a page, except where OVOS runs in the page itself (Emkay, in
        # Pyodide): there the page is handed the track's bytes, or plays it
        # from a folder it was given. "file" in the config says so elsewhere.
        uris = ["http", "https", "blob", "data"]
        if self.config.get("file", sys.platform == "emscripten"):
            uris.append("file")
        return uris

    def play(self, repeat=None):
        """Start the current track (the template's load_track set it).

        `repeat` is given by AudioService for the whole playlist; the
        template's next()/previous() call play() without it, so None keeps
        what was asked for at the start."""
        if repeat is not None:
            self._repeat = bool(repeat)
        uri = self._now_playing
        if isinstance(uri, list):    # (uri, mime) when mime hints are on
            uri = uri[0]
        if not uri:
            LOG.warning("browser audio: nothing to play")
            return
        self._state = "loading"
        self._announced = False     # track start not reported yet
        self._position_ms = 0
        self._length_ms = 0
        self._stamp = time.monotonic()
        LOG.info(f"browser audio: playing {uri}")
        self._send(PLAY, {"uri": uri, "position_ms": 0})

    def pause(self):
        if self._state in ("playing", "loading"):
            self._position_ms = self.get_track_position()
            self._state = "paused"
            self._send(PAUSE)

    def resume(self):
        if self._state == "paused":
            self._state = "playing"
            self._stamp = time.monotonic()
            self._send(RESUME)

    def stop(self) -> bool:
        """Stop; True when something was playing (AudioService then
        reports mycroft.stop.handled)."""
        was_playing = self._state != "stopped"
        self._state = "stopped"
        self._send(STOP)
        return was_playing

    def lower_volume(self):
        self._send(VOLUME, {"ducked": True, "factor": self.duck_factor})

    def restore_volume(self):
        self._send(VOLUME, {"ducked": False, "factor": 1.0})

    def get_track_length(self) -> int:
        """In milliseconds; 0 for a live stream or before the page knows."""
        return int(self._length_ms)

    def get_track_position(self) -> int:
        """In milliseconds. The page reports the position now and then;
        between reports it is counted on from the last one."""
        if self._state == "playing":
            return int(self._position_ms + (time.monotonic() - self._stamp) * 1000)
        return int(self._position_ms)

    def set_track_position(self, milliseconds):
        milliseconds = max(0, int(milliseconds))
        if self._length_ms:
            milliseconds = min(milliseconds, int(self._length_ms))
        self._position_ms = milliseconds
        self._stamp = time.monotonic()
        self._send(SEEK, {"position_ms": milliseconds})

    def shutdown(self):
        self.bus.remove(STATE, self._on_state)
        super().shutdown()

    # ---------------------------------------------------------- from the page
    def _on_state(self, message: Message):
        """What the page's player did: started, paused, ended, failed, or
        just where it is now."""
        data = message.data or {}
        if self._state == "stopped" and data.get("state") != "stopped":
            return   # a late report about a track that was stopped
        if "position_ms" in data:
            self._position_ms = float(data["position_ms"] or 0)
            self._stamp = time.monotonic()
        if data.get("length_ms"):
            self._length_ms = float(data["length_ms"])
        state = data.get("state")
        if state == "playing":
            self._state = "playing"
            if not self._announced:
                self._announced = True
                if self._track_start_callback:
                    # AudioService: mycroft.audio.playing_track + OCP "playing"
                    self._track_start_callback(self._now_playing)
        elif state == "paused" and self._state != "loading":
            # (a page switching streams may report the old one paused first)
            self._state = "paused"
        elif state == "ended":
            self._track_done()
        elif state == "error":
            LOG.error(f"browser audio: the page could not play "
                      f"{self._now_playing}: {data.get('error')}")
            self.ocp_error()
            self._track_done()
        if state in ("playing", "paused"):
            self.ocp_sync_playback(self.get_track_position())

    def _track_done(self):
        """Go on with the playlist, start it over (repeat), or end it."""
        if self._idx + 1 < len(self._tracks):
            self.next()          # template: load_track + play
            return
        if self._repeat and self._tracks:
            self._idx = 0
            self.load_track(self._tracks[0])
            self.play(self._repeat)
            return
        self._state = "stopped"
        if self._track_start_callback:
            self._track_start_callback(None)   # mycroft.audio.queue_end


def load_service(base_config, bus):
    """OPM's entry point: one backend per Audio.backends entry of our type.

    If mycroft.conf has none, one called "browser" is made, so installing
    the plugin is enough; ovos-audio still picks Audio.default-backend
    first when it can play the URI.
    """
    backends = (base_config or {}).get("backends", {}) or {}
    mine = [(name, conf) for name, conf in backends.items()
            if isinstance(conf, dict) and conf.get("type") == BACKEND_TYPE
            and conf.get("active", True)]
    if not mine:
        mine = [("browser", {"type": BACKEND_TYPE, "active": True})]
    return [BrowserAudioBackend(conf, bus, name) for name, conf in mine]
