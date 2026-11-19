"""The same browser player as an ovos-media backend (MediaBackend v2).

ovos-media is OpenVoiceOS' newer media service, the one OCP hands its
results to when it is not in "legacy" mode. Its players are MediaBackend
plugins (ovos-plugin-manager's MediaBackend v2, ovos-plugin-manager >= 3):
the daemon owns the playlist and every `ovos.common_play.*` state message,
and a backend only plays one track and *reports* what physically happened
(track started, paused, resumed, stopped, ended, failed).

This backend speaks the same page protocol as the classic one in
`__init__.py`: it sends `ovos.audio.browser.*` commands on its bus and
listens for `ovos.audio.browser.state` from the page that plays them in an
`<audio>` element. That bus is plugin-private transport, as the template
allows; it never emits playback state itself.

Who reports what (the template's "one reporting path" rule): the page is
the observer. Its `playing`, `paused`, `ended` and `error` become
TRACK_START, PAUSED/RESUMED, END_OF_MEDIA and ERROR, so a pause from the
page's own controls (or the phone's media keys) surfaces as well. The
verbs only ask the page. The one exception is stop: the page says nothing
once it has stopped, so `_stop` reports STOPPED itself.
"""
import time
from typing import List, Optional

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.media import AudioPlayerBackend, PlaybackEvent
from ovos_utils.log import LOG

from ovos_audio_plugin_browser import PAUSE, PLAY, RESUME, SEEK, STATE, STOP, VOLUME


class BrowserMediaBackend(AudioPlayerBackend):
    """Plays one track at a time in the page's <audio> element, for ovos-media.

    Config (media.audio_players.<name>):
        module: "ovos-media-audio-plugin-browser"
        duck_factor: volume while OVOS speaks or listens, 0..1 (default 0.3)
    """

    can_seek = True
    can_pause = True
    is_remote = False

    def __init__(self, config=None, bus=None):
        super().__init__(config=config, bus=bus)
        self.duck_factor = float(self.config.get("duck_factor", 0.3))
        self._uri: Optional[str] = None       # the loaded track
        self._state = "stopped"     # stopped | loading | playing | paused
        self._started = False       # TRACK_START reported for this track
        self._position_ms = 0
        self._length_ms = 0         # 0: not known yet, or a live stream
        self._stamp = time.monotonic()
        self.bus.on(STATE, self._on_state)

    def _send(self, msg_type: str, data: dict = None):
        self.bus.emit(Message(msg_type, data or {}))

    def supported_uris(self) -> List[str]:
        # what <audio> fetches by itself; file:// is not reachable from a page
        return ["http", "https", "blob", "data"]

    # ------------------------------------------------------------- the verbs
    def load_track(self, uri: str, metadata: dict = None) -> bool:
        """Remember the track; the page loads it when play() asks."""
        if not uri:
            return False
        self._uri = uri
        self.meta = dict(metadata or {})
        self._state = "stopped"
        self._started = False
        self._position_ms = 0
        self._length_ms = 0
        return True

    def play(self):
        if not self._uri:
            LOG.warning("browser media: nothing loaded to play")
            return
        self._state = "loading"
        self._started = False
        self._position_ms = 0
        self._length_ms = 0
        self._stamp = time.monotonic()
        LOG.info(f"browser media: playing {self._uri}")
        self._send(PLAY, {"uri": self._uri, "position_ms": 0})

    def pause(self):
        if self._state in ("playing", "loading"):
            self._position_ms = self._position()
            self._send(PAUSE)

    def resume(self):
        if self._state == "paused":
            self._send(RESUME)

    def _stop(self) -> bool:
        """Ask the page to stop and report it: the page does not answer a
        stop, so this is the only place that sees it."""
        if self._state == "stopped":
            return False
        uri = self._uri
        self._state = "stopped"
        self._send(STOP)
        self.report_track_end(uri=uri)   # STOPPED: stop() set the flag
        return True

    def lower_volume(self):
        self._send(VOLUME, {"ducked": True, "factor": self.duck_factor})

    def restore_volume(self):
        self._send(VOLUME, {"ducked": False, "factor": 1.0})

    def get_track_length(self) -> Optional[int]:
        """None when nothing plays, -1 for a live stream (or before the
        page knows the length), otherwise milliseconds."""
        if self._state == "stopped":
            return None
        return int(self._length_ms) if self._length_ms else -1

    def get_track_position(self) -> Optional[int]:
        if self._state == "stopped":
            return None
        return self._position()

    def set_track_position(self, milliseconds: int):
        milliseconds = max(0, int(milliseconds))
        if self._length_ms:
            milliseconds = min(milliseconds, int(self._length_ms))
        self._position_ms = milliseconds
        self._stamp = time.monotonic()
        self._send(SEEK, {"position_ms": milliseconds})

    def shutdown(self):
        self.bus.remove(STATE, self._on_state)
        super().shutdown()

    def _position(self) -> int:
        """The page reports the position now and then; between reports it
        is counted on from the last one."""
        if self._state == "playing":
            return int(self._position_ms + (time.monotonic() - self._stamp) * 1000)
        return int(self._position_ms)

    # ---------------------------------------------------------- the observer
    def _on_state(self, message: Message):
        """What the page's player did, reported to ovos-media."""
        data = message.data or {}
        if self._state == "stopped" or data.get("uri") != self._uri:
            return   # a late report about a track that was stopped or replaced
        if "position_ms" in data:
            self._position_ms = float(data["position_ms"] or 0)
            self._stamp = time.monotonic()
        if data.get("length_ms"):
            self._length_ms = float(data["length_ms"])
        state = data.get("state")
        if state == "playing":
            was = self._state
            self._state = "playing"
            if not self._started:
                self._started = True
                self.report(PlaybackEvent.TRACK_START, uri=self._uri)
            elif was == "paused":
                self.report(PlaybackEvent.RESUMED, uri=self._uri)
        elif state == "paused" and self._state == "playing":
            # (a page switching streams may report the old one paused first,
            # while this track is still loading: not a pause)
            self._state = "paused"
            self.report(PlaybackEvent.PAUSED, uri=self._uri)
        elif state == "ended":
            self._state = "stopped"
            self.report_track_end(uri=self._uri)
        elif state == "error":
            LOG.error(f"browser media: the page could not play "
                      f"{self._uri}: {data.get('error')}")
            self._state = "stopped"
            self.report_track_end(uri=self._uri,
                                  error=data.get("error") or "unknown")
