import os
import time

from pocketoptionapi import PocketOption


def get_otc_candles(symbol, period=300):
    ssid = os.getenv("PO_SSID")

    if not ssid:
        raise RuntimeError("PO_SSID не настроен в GitHub Secrets")

    api = PocketOption(ssid)

    ok, error = api.connect()

    if not ok:
        raise RuntimeError(
            f"Pocket Option connection error: {error}"
        )

    try:
        deadline = time.time() + 20

        while time.time() < deadline:
            if api.check_connect() and api.is_time_synced():
                break

            time.sleep(0.2)
        else:
            raise RuntimeError(
                "Pocket Option: не удалось синхронизировать время"
            )

        api.subscribe(symbol, period=period)

        time.sleep(1)

        candles = api.get_historical_candles(
            symbol,
            period=period,
            offset=9000,
            count_request=1,
        )

        if not candles:
            raise RuntimeError(
                f"Pocket Option не вернул свечи для {symbol}"
            )

        if len(candles) < 80:
            raise RuntimeError(
                f"Для {symbol} получено только "
                f"{len(candles)} свечей, нужно минимум 80"
            )

        return candles

    finally:
        api.disconnect_websocket()
