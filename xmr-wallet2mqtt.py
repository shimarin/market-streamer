#!/usr/bin/python3
import logging,time,io
import requests,cairo
import paho.mqtt.client as mqtt_client # dev-python/paho-mqtt

from gi import require_version
require_version("Pango", "1.0")
require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

xmr_balance = None
xmr_unlocked_balance = None

CELL_WIDTH, CELL_HEIGHT = 122, 64

def fetch_xmr_balance():
    """ウォレットを同期し、残高を確認"""
    def send_xmr_wallet_rpc_request(method, params=None):
        """RPCリクエストを送信し、結果を返す"""
        headers = {"Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": method,
            "params": params or {}
        }
        response = requests.post("http://localhost:18082/json_rpc", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            raise Exception(f"RPC error: {result['error']['message']}")
        return result.get("result")

    try:
        # ウォレットを同期
        logging.debug("Starting wallet refresh")
        refresh_result = send_xmr_wallet_rpc_request("refresh")
        logging.debug(f"Refresh completed: {refresh_result}")

        # 残高を取得
        balance_result = send_xmr_wallet_rpc_request("get_balance", {
            "account_index": 0,
            "address_indices": [0]
        })
        balance = balance_result["balance"] / 1e12  # ピコモネロをXMRに変換
        unlocked_balance = balance_result["unlocked_balance"] / 1e12  # ピコモネロをXMRに変換

        logging.debug(f"XMR Balance: {balance} total, {unlocked_balance} unlocked")
        return (balance, unlocked_balance)
    except Exception as e:
        logging.error(f"Error in sync_and_check_balance: {e}")
        return (None, None)

def draw(xmr_balance, xmr_unlocked_balance):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_WIDTH, CELL_HEIGHT)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.fill()

    ctx.set_source_rgb(0, 0, 0)
    ctx.set_font_size(12)
    xmr_balance_str = f"{xmr_unlocked_balance:.4f}{'+' if xmr_balance > xmr_unlocked_balance else ''} XMR"
    xmr_balance_extents = ctx.text_extents(xmr_balance_str)
    xmr_balance_width = xmr_balance_extents[2]
    ctx.move_to((CELL_WIDTH - xmr_balance_width) - 4, CELL_HEIGHT - 3)
    ctx.show_text(xmr_balance_str)
    # return as PNG binary
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()

def main(mqtt_host):
    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.connect(mqtt_host)
    mqtt.loop_start()  # run in a separate thread

    xmr_balance, xmr_unlocked_balance = None, None

    try:
        while True:
            xmr_balance_new, xmr_unlocked_balance_new = fetch_xmr_balance()
            if xmr_balance_new != xmr_balance or xmr_unlocked_balance_new != xmr_unlocked_balance:
                xmr_balance = xmr_balance_new
                xmr_unlocked_balance = xmr_unlocked_balance_new
                png = draw(xmr_balance, xmr_unlocked_balance)
                mqtt.publish("xmr/balance", png)
            time.sleep(10)
    finally:
        mqtt.loop_stop()
        mqtt.disconnect()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Market streamer")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    main(args.mqtt)