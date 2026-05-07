"""
Regression checks for Binance Futures routed WebSocket stream mapping.
"""
import asyncio
import sys

import market_realtime


def test_public_streams_use_public_endpoint():
    streams = market_realtime._public_streams_for(["BTC"])
    url = market_realtime._combined_stream_url(
        market_realtime.FSTREAM_PUBLIC_STREAM_BASE,
        streams,
    )

    assert url == (
        "wss://fstream.binance.com/public/stream?"
        "streams=btcusdt@bookTicker/btcusdt@depth5@100ms"
    )


def test_market_streams_use_market_endpoint():
    streams = market_realtime._market_streams_for(["BTC"])
    url = market_realtime._combined_stream_url(
        market_realtime.FSTREAM_MARKET_STREAM_BASE,
        streams,
    )

    assert url == (
        "wss://fstream.binance.com/market/stream?"
        "streams=btcusdt@markPrice@1s/btcusdt@aggTrade"
    )


def test_legacy_combined_endpoint_removed():
    legacy = "wss://fstream.binance.com/stream?streams="
    assert market_realtime.FSTREAM_PUBLIC_STREAM_BASE != legacy
    assert market_realtime.FSTREAM_MARKET_STREAM_BASE != legacy
    assert "/public/stream?streams=" in market_realtime.FSTREAM_PUBLIC_STREAM_BASE
    assert "/market/stream?streams=" in market_realtime.FSTREAM_MARKET_STREAM_BASE


def test_stream_reconnect_reuses_disconnected_connection_params():
    calls = []
    original_connect = market_realtime.websockets.connect
    original_sleep = market_realtime.asyncio.sleep
    original_running = market_realtime._running

    class FakeWebsocket:
        async def recv(self):
            raise OSError("simulated disconnect")

    class FakeConnection:
        def __init__(self, url, kwargs):
            self.url = url
            self.kwargs = kwargs

        async def __aenter__(self):
            calls.append((self.url, self.kwargs))
            if len(calls) >= 2:
                market_realtime._running = False
            return FakeWebsocket()

        async def __aexit__(self, *_):
            return False

    def fake_connect(url, **kwargs):
        return FakeConnection(url, kwargs)

    async def fake_sleep(_):
        return None

    async def run_test():
        market_realtime._running = True
        market_realtime.websockets.connect = fake_connect
        market_realtime.asyncio.sleep = fake_sleep
        try:
            state = market_realtime.RealtimeState(["BTC"])
            await market_realtime._consume_stream("public", "wss://example.test/ws", state)
        finally:
            market_realtime.websockets.connect = original_connect
            market_realtime.asyncio.sleep = original_sleep
            market_realtime._running = original_running

    asyncio.run(run_test())

    assert calls == [
        (
            "wss://example.test/ws",
            {
                "ping_interval": market_realtime.WS_PING_INTERVAL_SECONDS,
                "ping_timeout": market_realtime.WS_PING_TIMEOUT_SECONDS,
            },
        ),
        (
            "wss://example.test/ws",
            {
                "ping_interval": market_realtime.WS_PING_INTERVAL_SECONDS,
                "ping_timeout": market_realtime.WS_PING_TIMEOUT_SECONDS,
            },
        ),
    ]


if __name__ == "__main__":
    tests = [
        test_public_streams_use_public_endpoint,
        test_market_streams_use_market_endpoint,
        test_legacy_combined_endpoint_removed,
        test_stream_reconnect_reuses_disconnected_connection_params,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"FAIL {test.__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
