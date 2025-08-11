#!/usr/bin/env python3
# # -*- coding: utf-8 -*-
import os,time,threading,logging,json,argparse,hashlib,hmac,base64,io

import websocket,cairo
import paho.mqtt.client as mqtt_client

from gi import require_version
require_version("Pango", "1.0")
require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

api_key = None
api_secret = None
ws_url = "wss://ws.poloniex.com/ws/v3/private"
mqtt = None

# poloniex account balance
eq = None
upl = None

CELL_WIDTH, CELL_HEIGHT = 187, 114

def ping_thread(ws):
    try:
        while True:
            ws.send(json.dumps({"event": "ping"}))
            logging.debug("Ping sent")
            time.sleep(10)
    except Exception as e:
        logging.error(f"Ping thread error(disconnected?): {e}")

def on_open(ws):
    nonce = int(time.time() * 1000)
    message = f"GET\n/ws\nsignTimestamp={nonce}"
    sign = base64.b64encode(hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).digest()).decode()
    subscribe_message = {
        "event": "subscribe",
        "channel": ["auth"],
        "params": {
            "key": api_key,
            "signTimestamp": nonce,
            "signature": sign
        }
    }
    ws.send(json.dumps(subscribe_message))
    logging.debug("認証メッセージ送信:", subscribe_message)

def draw(eq, upl):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_WIDTH, CELL_HEIGHT)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.fill()

    # PangoCairoコンテキストの作成
    pango_ctx = PangoCairo.create_context(ctx)

    if eq is not None:
        pango_layout = Pango.Layout.new(pango_ctx)
        # フォントの設定（Noto SansとNoto Emojiを指定）
        font_desc = Pango.FontDescription.new()
        font_desc.set_family("Noto Sans")
        font_desc.set_size(12 * Pango.SCALE)  # フォントサイズ24ポイント
        pango_layout.set_font_description(font_desc)
        pango_layout.set_text("₿先物口座残高", -1)

        ctx.set_source_rgb(0, 0, 0)
        ctx.move_to(3, 1)
        PangoCairo.show_layout(ctx, pango_layout)
        ctx.set_font_size(22)
        eq_str = f"{eq:.2f}ドル"
        eq_extents = ctx.text_extents(eq_str)
        eq_width = eq_extents[2]
        ctx.move_to((CELL_WIDTH - eq_width) - 4, 42)
        ctx.show_text(eq_str)
    if upl is not None:
        ctx.set_font_size(16)
        ctx.move_to(3, 68)
        ctx.show_text("うち含み損益")
        ctx.set_font_size(28)
        sign = " "
        color = (0, 0, 0)
        if upl > 0:
            sign = "+"
            color = (0, 0.7, 0)
        elif upl < 0:
            sign = "-"
            color = (0.7, 0, 0)
        ctx.set_source_rgb(*color)
        upl_str = f"{sign}{upl:.2f}ドル"
        upl_extents = ctx.text_extents(upl_str)
        upl_width = upl_extents[2]
        ctx.move_to((CELL_WIDTH - upl_width) - 4, 100)
        ctx.show_text(upl_str)

    # draw the gray frame
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.stroke()
    # return as PNG binary
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()

def on_account(data):
    global eq, upl
    eq_str = data.get("eq")
    eq = float(eq_str) if eq_str is not None else None
    upl_str = data.get("upl")
    upl = float(upl_str) if upl_str is not None else None
    png = draw(eq, upl)
    mqtt.publish("poloniex/balance", png)

def on_positions(data):
    pass

def on_message(ws, message):
    logging.debug(f"Received message: {message}")
    json_message = json.loads(message)
    event = json_message.get("event")
    channel = json_message.get("channel")
    data = json_message.get("data")

    if channel is None: return

    if event == "subscribe":
        logging.info(f"Subscribed to channel: {channel}")
        return
    #else
    if channel == "auth":
        if data["success"]:
            logging.info("Authentication successful")
            SUBSCRIBE_MESSAGE = {
                "event": "subscribe",
                "channel": ["account","positions"],
                "symbols": ["BTC_USDT_PERP"]
            }
            ws.send(json.dumps(SUBSCRIBE_MESSAGE))
            threading.Thread(target=ping_thread, args=(ws,), daemon=True).start()
        else:
            logging.error("Failed to authenticate")
        return
    elif channel == "account":
        #https://api-docs.poloniex.com/v3/futures/websocket/private/account
        #mqtt.publish("poloniex/account", json.dumps(data[0]))
        on_account(data[0])
    elif channel == "positions":
        #mqtt.publish("poloniex/positions", json.dumps(data))
        on_positions(data)
    else:
        logging.warning(f"Received message from unknown channel: {channel}")

def on_error(ws, error):
    logging.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info(f"WebSocket closed with code: {close_status_code}, message: {close_msg}")

if __name__ == "__main__":
    # Argument parser
    parser = argparse.ArgumentParser(description="Poloniex Private WebSocket API to MQTT bridge")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--loglevel", type=str, default="info", help="Set the logging level (debug, info, warning, error)")
    args = parser.parse_args()
    # Set logging level
    logging.basicConfig(level=args.loglevel.upper(), format='%(asctime)s - %(levelname)s - %(message)s')
    # MQTTクライアント設定
    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.connect(args.mqtt)
    mqtt.loop_start() # run in a separate thread

    # read api_key and secret from ~/.poloniex_api_secret (json)
    api_key_file_error = False
    try:
        with open(os.path.expanduser("~/.config/poloniex-api-key"), "r") as f:
            api_key_json = json.load(f)
            api_key = api_key_json.get("api_key")
            api_secret = api_key_json.get("api_secret")
            if api_key is None or api_secret is None:
                logging.error("api_key or api_secret not found in ~/.config/poloniex-api-key")
                api_key_file_error = True
    except Exception as e:
        logging.error(f"Failed to read api_secret: {e}")
        api_key_file_error = True
    
    if api_key_file_error:
        logging.error("Please create json file ~/.config/poloniex-api-key with api_key and api_secret")
        exit(1)

    # Create WebSocket client
    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    
    # Start WebSocket in a separate thread
    ws.run_forever()
