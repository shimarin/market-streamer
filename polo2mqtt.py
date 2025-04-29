#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import websocket
import paho.mqtt.client as mqtt_client
import time,threading,logging,json,argparse

channel = ["candles_minute_10"]
symbols = ["XMR_USDT", "BTC_USDT"]
mqtt = None

def ping_thread(ws):
    try:
        while True:
            ws.send(json.dumps({"event": "ping"}))
            logging.debug("Ping sent")
            time.sleep(10)
    except Exception as e:
        logging.error(f"Ping thread error(disconnected?): {e}")

def on_open(ws):
    logging.info("WebSocket connection opened")
    SUBSCRIPTION_MESSAGE = {
        "channel": channel,
        "symbols": symbols,
        "event": "subscribe"
    }       
    ws.send(json.dumps(SUBSCRIPTION_MESSAGE))
    # Start ping thread
    threading.Thread(target=ping_thread, args=(ws,), daemon=True).start()

def on_message(ws, message):
    logging.debug(f"Received message: {message}")
    # MQTTにメッセージを送信
    if "data" in message:
        mqtt.publish("poloniex/public", message)

def on_error(ws, error):
    logging.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info("WebSocket closed with code: {close_status_code}, message: {close_msg}")

if __name__ == "__main__":
    # Argument parser
    parser = argparse.ArgumentParser(description="Poloniex WebSocket to MQTT bridge")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--loglevel", type=str, default="info", help="Set the logging level (debug, info, warning, error)")
    args = parser.parse_args()
    # Set logging level
    logging.basicConfig(level=args.loglevel.upper(), format='%(asctime)s - %(levelname)s - %(message)s')
    # MQTTクライアント設定
    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.connect(args.mqtt)
    mqtt.loop_start() # run in a separate thread

    # WebSocket server settings
    ws_url = "wss://ws-web.poloniex.com/ws/public"

    # Create WebSocket client
    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    
    # Start WebSocket in a separate thread
    ws.run_forever()
