"""The backend against a FakeBus, with a pretend page answering it."""
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

import ovos_audio_plugin_browser as plugin


def make():
    bus = FakeBus()
    sent = []
    bus.on("message", lambda raw: sent.append(Message.deserialize(raw)
                                              if isinstance(raw, str) else raw))
    (backend,) = plugin.load_service({}, bus)
    started = []
    backend.set_track_start_callback(started.append)
    return bus, backend, sent, started


def page(bus, **data):
    bus.emit(Message(plugin.STATE, data))


def of(sent, msg_type):
    return [m for m in sent if m.msg_type == msg_type]


def test_default_backend_without_config():
    bus = FakeBus()
    (b,) = plugin.load_service({}, bus)
    assert b.name == "browser"
    assert "https" in b.supported_uris()


def test_config_names_the_backends():
    conf = {"backends": {"tab": {"type": plugin.BACKEND_TYPE, "active": True},
                         "off": {"type": plugin.BACKEND_TYPE, "active": False},
                         "mpv": {"type": "ovos_mpv"}}}
    names = [b.name for b in plugin.load_service(conf, FakeBus())]
    assert names == ["tab"]


def test_play_start_and_end():
    bus, b, sent, started = make()
    b.add_list(["https://radio.example/stream.mp3"])
    b.play()
    assert of(sent, plugin.PLAY)[-1].data["uri"] == "https://radio.example/stream.mp3"
    page(bus, state="playing", position_ms=0)
    assert started == ["https://radio.example/stream.mp3"]
    page(bus, state="ended", position_ms=1000)
    assert started[-1] is None          # end of the queue


def test_playlist_goes_on_and_repeats():
    bus, b, sent, started = make()
    b.add_list(["https://a.example/1.mp3", "https://a.example/2.mp3"])
    b.play(repeat=True)
    page(bus, state="playing")
    page(bus, state="ended")
    assert of(sent, plugin.PLAY)[-1].data["uri"].endswith("2.mp3")
    page(bus, state="playing")
    page(bus, state="ended")
    assert of(sent, plugin.PLAY)[-1].data["uri"].endswith("1.mp3")   # repeat


def test_error_ends_the_queue():
    bus, b, sent, started = make()
    b.add_list(["http://insecure.example/stream"])
    b.play()
    page(bus, state="error", error="mixed content")
    assert started == [None]
    assert b.stop() is False            # nothing playing any more


def test_pause_resume_seek_and_duck():
    bus, b, sent, started = make()
    b.add_list(["https://a.example/1.mp3"])
    b.play()
    page(bus, state="playing", position_ms=5000, length_ms=60000)
    b.pause()
    assert of(sent, plugin.PAUSE)
    assert 5000 <= b.get_track_position() < 6000
    b.resume()
    assert of(sent, plugin.RESUME)
    b.set_track_position(90000)
    assert of(sent, plugin.SEEK)[-1].data["position_ms"] == 60000   # kept in the track
    b.lower_volume()
    assert of(sent, plugin.VOLUME)[-1].data == {"ducked": True, "factor": 0.3}
    b.restore_volume()
    assert of(sent, plugin.VOLUME)[-1].data["ducked"] is False
    assert b.stop() is True


def test_late_report_after_stop_is_ignored():
    bus, b, sent, started = make()
    b.add_list(["https://a.example/1.mp3"])
    b.play()
    b.stop()
    page(bus, state="playing")
    assert started == []


def test_file_tracks_only_where_the_page_can_play_them(monkeypatch):
    bus = FakeBus()
    (b,) = plugin.load_service({}, bus)
    assert "file" not in b.supported_uris()
    monkeypatch.setattr(plugin.sys, "platform", "emscripten")
    assert "file" in b.supported_uris()
    (c,) = plugin.load_service({"backends": {"browser": {"type": "ovos_audio_browser", "file": True}}}, bus)
    monkeypatch.setattr(plugin.sys, "platform", "linux")
    assert "file" in c.supported_uris()
