"""With ovos-audio's own AudioService: found, chosen, ducked, stopped."""
import time

import pytest
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

AudioService = pytest.importorskip("ovos_audio.audio").AudioService
try:
    from ovos_spec_tools import SpecMessage
    SPEAKING = SpecMessage.AUDIO_OUTPUT_STARTED
except ImportError:
    SPEAKING = "recognizer_loop:audio_output_start"


def test_audioservice_plays_through_the_page():
    bus = FakeBus()
    seen = []
    bus.on("message", lambda m: seen.append(Message.deserialize(m) if isinstance(m, str) else m))
    service = AudioService(bus, disable_ocp=True)
    assert "browser" in [s.name for s in service.service]

    bus.emit(Message("mycroft.audio.service.play", {"tracks": ["https://radio.example/live.mp3"]}))
    bus.emit(Message("ovos.audio.browser.state", {"state": "playing", "position_ms": 0}))
    bus.emit(Message(SPEAKING))
    time.sleep(1.1)   # AudioService ignores a stop within 1 s of play
    bus.emit(Message("mycroft.audio.service.stop"))

    types = [m.msg_type for m in seen]
    plays = [m for m in seen if m.msg_type == "ovos.audio.browser.play"]
    assert plays and plays[0].data["uri"] == "https://radio.example/live.mp3"
    assert "mycroft.audio.playing_track" in types
    ducks = [m.data for m in seen if m.msg_type == "ovos.audio.browser.volume"]
    assert {"ducked": True, "factor": 0.3} in ducks
    assert "ovos.audio.browser.stop" in types
    assert "mycroft.stop.handled" in types
    service.shutdown()
