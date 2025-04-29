import websocket
import paho.mqtt.client as mqtt_client
import os,time,threading,logging,json,argparse,hashlib,hmac,base64

api_key = None
api_secret = None
ws_url = "wss://ws.poloniex.com/ws/v3/private"
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
        mqtt.publish("poloniex/account", json.dumps(data[0]))
    elif channel == "positions":
        mqtt.publish("poloniex/positions", json.dumps(data))
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
        with open(os.path.expanduser("~/.poloniex-api-key"), "r") as f:
            api_key_json = json.load(f)
            api_key = api_key_json.get("api_key")
            api_secret = api_key_json.get("api_secret")
            if api_key is None or api_secret is None:
                logging.error("api_key or api_secret not found in ~/.poloniex-api-key")
                api_key_file_error = True
    except Exception as e:
        logging.error(f"Failed to read api_secret: {e}")
        api_key_file_error = True
    
    if api_key_file_error:
        logging.error("Please create json file ~/.poloniex-api-key with api_key and api_secret")
        exit(1)

    # Create WebSocket client
    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    
    # Start WebSocket in a separate thread
    ws.run_forever()
