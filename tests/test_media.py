"""The ovos-media backend against a FakeBus, with a pretend page answering it
and a pretend daemon collecting what the backend reports."""
import pytest
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

pytest.importorskip("ovos_plugin_manager.templates.media",
                    reason="needs ovos-plugin-manager with MediaBackend v2")
from ovos_plugin_manager.templates.media import PlaybackEvent  # noqa: E402

import ovos_audio_plugin_browser as plugin  # noqa: E402
from ovos_audio_plugin_browser.media import BrowserMediaBackend  # noqa: E402

URI = "https://radio.example/stream.mp3"


def make():
    bus = FakeBus()
    sent = []
    bus.on("message", lambda raw: sent.append(Message.deserialize(raw)
                                              if isinstance(raw, str) else raw))
    backend = BrowserMediaBackend({}, bus)
    events = []
    backend.bind_event_reporter(lambda event, **data: events.append((event, data)))
    return bus, backend, sent, events


def page(bus, **data):
    bus.emit(Message(plugin.STATE, data))


def of(sent, msg_type):
    return [m for m in sent if m.msg_type == msg_type]


def kinds(events):
    return [e for e, _ in events]


def test_load_reports_nothing_and_play_asks_the_page():
    bus, b, sent, events = make()
    assert b.load_track(URI) is True
    assert events == []
    assert not of(sent, plugin.PLAY)          # loading is not playing
    b.play()
    assert of(sent, plugin.PLAY)[-1].data == {"uri": URI, "position_ms": 0}
    assert events == []                       # nothing played yet
    page(bus, uri=URI, state="playing", position_ms=0)
    assert events == [(PlaybackEvent.TRACK_START, {"uri": URI})]
    page(bus, uri=URI, state="playing", position_ms=5000)
    assert kinds(events) == [PlaybackEvent.TRACK_START]   # once per track


def test_load_without_uri_fails():
    _, b, _, _ = make()
    assert b.load_track("") is False


def test_natural_end():
    bus, b, _, events = make()
    b.load_track(URI)
    b.play()
    page(bus, uri=URI, state="playing")
    page(bus, uri=URI, state="ended")
    assert events[-1] == (PlaybackEvent.END_OF_MEDIA, {"uri": URI})
    assert b.get_track_position() is None


def test_stop_reports_stopped_not_the_end():
    bus, b, sent, events = make()
    b.load_track(URI)
    b.play()
    page(bus, uri=URI, state="playing")
    assert b.stop() is True
    assert of(sent, plugin.STOP)
    assert events[-1] == (PlaybackEvent.STOPPED, {"uri": URI})
    assert b.stop() is False                  # nothing to stop any more
    # a late report from the page about the stopped track is dropped
    page(bus, uri=URI, state="ended")
    assert events[-1][0] == PlaybackEvent.STOPPED


def test_error_from_the_page():
    bus, b, _, events = make()
    b.load_track("http://insecure.example/stream")
    b.play()
    page(bus, uri="http://insecure.example/stream", state="error",
         error="mixed content")
    assert events[-1] == (PlaybackEvent.ERROR,
                          {"error": "mixed content",
                           "uri": "http://insecure.example/stream"})


def test_pause_and_resume_are_observed():
    bus, b, sent, events = make()
    b.load_track(URI)
    b.play()
    page(bus, uri=URI, state="playing", position_ms=1000)
    b.pause()
    assert of(sent, plugin.PAUSE)
    assert kinds(events) == [PlaybackEvent.TRACK_START]   # an ask, not a fact
    page(bus, uri=URI, state="paused", position_ms=2000)
    assert events[-1] == (PlaybackEvent.PAUSED, {"uri": URI})
    assert b.get_track_position() == 2000
    b.resume()
    assert of(sent, plugin.RESUME)
    page(bus, uri=URI, state="playing", position_ms=2000)
    assert events[-1] == (PlaybackEvent.RESUMED, {"uri": URI})


def test_pause_from_the_page_itself():
    """The phone's media keys pause the <audio>: still reported."""
    bus, b, _, events = make()
    b.load_track(URI)
    b.play()
    page(bus, uri=URI, state="playing")
    page(bus, uri=URI, state="paused")
    assert events[-1][0] == PlaybackEvent.PAUSED


def test_reports_about_another_track_are_dropped():
    bus, b, _, events = make()
    b.load_track(URI)
    b.play()
    page(bus, uri="https://old.example/1.mp3", state="ended")
    assert events == []


def test_length_position_and_seek():
    bus, b, sent, _ = make()
    assert b.get_track_length() is None       # nothing loaded
    b.load_track(URI)
    b.play()
    page(bus, uri=URI, state="playing", position_ms=0, length_ms=0)
    assert b.get_track_length() == -1         # a live stream
    page(bus, uri=URI, state="playing", position_ms=1000, length_ms=60000)
    assert b.get_track_length() == 60000
    b.set_track_position(90000)
    assert of(sent, plugin.SEEK)[-1].data == {"position_ms": 60000}


def test_ducking():
    _, b, sent, _ = make()
    b.lower_volume()
    assert of(sent, plugin.VOLUME)[-1].data == {"ducked": True, "factor": 0.3}
    b.restore_volume()
    assert of(sent, plugin.VOLUME)[-1].data == {"ducked": False, "factor": 1.0}


def test_no_state_messages_of_its_own():
    """The daemon owns ovos.common_play.*; the backend never emits it."""
    bus, b, sent, _ = make()
    b.load_track(URI)
    b.play()
    page(bus, uri=URI, state="playing")
    b.pause()
    page(bus, uri=URI, state="paused")
    b.stop()
    assert not [m for m in sent if m.msg_type.startswith("ovos.common_play.")]
