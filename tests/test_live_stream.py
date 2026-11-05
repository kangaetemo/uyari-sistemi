import asyncio
import json
import logging
from decimal import Decimal

import pytest

from app.api.live_stream import LiveScoreStream, match_rows
from app.core.monitor import Monitor
from app.db.repository import Repository
from tests.test_third_set import CONFIG, FakeAPI, live_payload, score_fixture


@pytest.mark.parametrize("message,keys", [
    ({"event_key": 1, "event_status": "Set 1"}, ["1"]),
    ([{"event_key": 1}, {"event_key": 2}, {"x": 3}], ["1", "2"]),
    ({"result": [{"event_key": 5}]}, ["5"]),
    ({"7": {"event_key": 7}, "8": {"event_key": 8}}, ["7", "8"]),
    ("text", []),
])
def test_message_shapes(message, keys):
    assert [str(r["event_key"]) for r in match_rows(message)] == keys


def test_cache_tracks_live_and_drops_finished_and_stale():
    now = [0.0]
    stream = LiveScoreStream("k", "Europe/Istanbul", monotonic=lambda: now[0])
    stream.handle(json.dumps([{"event_key": 1, "event_status": "Set 1"}, {"event_key": 2, "event_status": "Set 2"}]))
    assert set(stream.matches) == {"1", "2"}
    stream.handle(json.dumps({"event_key": 1, "event_status": "Finished"}))
    assert set(stream.matches) == {"2"}
    now[0] = 31 * 60
    stream.handle(json.dumps({"event_key": 3, "event_status": "Set 1"}))
    assert set(stream.matches) == {"3"}
    assert stream.handle("not json") == []


def test_url_has_key_and_timezone():
    assert LiveScoreStream("abc", "Europe/Istanbul").url() == "wss://wss.api-tennis.com/live?APIkey=abc&timezone=Europe%2FIstanbul"


class FakeSocket:
    def __init__(self, messages, fail=None):
        self.messages, self.fail = messages, fail

    async def __aenter__(self):
        if self.fail:
            raise self.fail
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self.messages:
            yield message
        await asyncio.sleep(3600)


def test_reconnects_after_failure_without_logging_the_key(monkeypatch, caplog):
    sockets = iter([FakeSocket([], fail=OSError("wss://wss.api-tennis.com/live?APIkey=SECRET")),
                    FakeSocket([json.dumps({"event_key": 9, "event_status": "Set 1"})])])
    stream = LiveScoreStream("SECRET", "UTC", connect=lambda url: next(sockets))
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await real_sleep(0)
    monkeypatch.setattr("app.api.live_stream.asyncio.sleep", fast_sleep)

    async def scenario():
        task = asyncio.create_task(stream.run())
        for _ in range(50):
            await real_sleep(0)
            if stream.matches:
                break
        connected = stream.connected
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return connected
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(scenario()) is True
    assert "9" in stream.matches
    assert "OSError" in caplog.text and "SECRET" not in caplog.text


def connected_stream(*fixtures):
    stream = LiveScoreStream("k", "UTC")
    stream.connected = True
    for fixture in fixtures:
        stream.handle(json.dumps(fixture))
    return stream


def test_scores_come_from_the_socket_instead_of_livescore(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI()
    stream = connected_stream(score_fixture())
    monitor = Monitor(CONFIG, api, repo, None, stream)
    asyncio.run(monitor.live_scan())
    assert api.score_calls == 0
    assert [w["favorite_side"] for w in repo.third_set_watched()] == ["Home"]
    repo.close()


def test_disconnected_socket_falls_back_to_livescore(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI()
    stream = connected_stream(score_fixture())
    stream.connected = False
    asyncio.run(Monitor(CONFIG, api, repo, None, stream).live_scan())
    assert api.score_calls == 1 and len(repo.third_set_watched()) == 1
    repo.close()


def test_third_set_opening_wakes_the_live_loop(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI()
    api.payload = live_payload(status="Set 2")
    stream = connected_stream()
    monitor = Monitor(CONFIG, api, repo, None, stream)

    async def scenario():
        task = asyncio.create_task(monitor.live_loop(3600))
        await asyncio.sleep(0.05)
        first = api.live_calls
        stream.handle(json.dumps(score_fixture(points="15 - 0")))  # Not an opening: no wake-up.
        await asyncio.sleep(0.05)
        unchanged = api.live_calls
        stream.handle(json.dumps(score_fixture()))                  # 0-0 in set 3: scan now.
        await asyncio.sleep(0.05)
        task.cancel()
        return first, unchanged, api.live_calls
    assert asyncio.run(scenario()) == (1, 1, 2)
    repo.close()


def test_heartbeat_records_socket_state(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI()
    api.payload = live_payload(status="Set 2")
    monitor = Monitor(CONFIG, api, repo, None, connected_stream())
    asyncio.run(monitor.live_scan())
    state = {r["key"]: r["value"] for r in repo.conn.execute("SELECT key,value FROM bot_state")}
    assert state["stream_connected"] == "1"
    assert Decimal(state["live_events"]) == 2
    repo.close()
