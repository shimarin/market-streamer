#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,base64,subprocess,websocket,time,logging
import urllib.request
import cv2
import numpy as np
import paho.mqtt.client as mqtt_client

# コマンドIDを管理するためのカウンタ
command_id = 0

previous_screenshot_dow30 = None
previous_screenshot_bitcoin = None

FPS = 2.0

# ヘルパー関数：Chromeを指定ポートで起動
def start_chrome(port=9222, width=1920, height=1440):
    process = subprocess.Popen([
        "google-chrome-stable",
        f"--window-size={width},{height}",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=*",
        "--headless=new",
        "--no-sandbox"
    ])
    time.sleep(2)  # Chromeの起動を待つ
    return process

# ヘルパー関数：WebSocketデバッグURLを取得
def get_ws_url(port):
    with urllib.request.urlopen(f'http://localhost:{port}/json/version') as response:
        data = json.loads(response.read())
        return data['webSocketDebuggerUrl']

# ヘルパー関数：CDPコマンドを送信
def send_command(ws, method, params=None, session_id=None):
    global command_id
    if params is None:
        params = {}
    command = {
        "id": command_id,
        "method": method,
        "params": params
    }
    if session_id:
        command["sessionId"] = session_id
    ws.send(json.dumps(command))
    while True:
        response = ws.recv()
        response = json.loads(response)
        if "id" in response and response["id"] == command_id:
            break
        elif "method" in response and response["method"] == "Inspector.detached":
            logging.info("Inspector detached")
            break

    command_id += 1
    return response

# ヘルパー関数：ターゲットにアタッチしてセッションIDを取得
def attach_to_target(ws, target_id):
    result = send_command(ws, "Target.attachToTarget", {
        "targetId": target_id,
        "flatten": True
    })
    return result["result"]["sessionId"]

# ヘルパー関数：初期ターゲットを取得
def get_initial_target(ws):
    with urllib.request.urlopen('http://localhost:9222/json') as response:
        targets = json.loads(response.read())
        for target in targets:
            if target["type"] == "page":
                return target["id"]
    raise Exception("No page target found")

def load_pages(ws, url1, url2):
    # 初期ターゲットを取得
    initial_target_id = get_initial_target(ws)
    logging.info(f"Initial target ID: {initial_target_id}")
    # 初期ターゲットにアタッチ
    session_id = attach_to_target(ws, initial_target_id)
    logging.info(f"Session ID: {session_id}")
    # 初期ターゲットでPageを有効化
    send_command(ws, "Page.enable", session_id=session_id)
    # 初期ターゲットでURLをナビゲート
    send_command(ws, "Page.navigate", {"url": url1}, session_id=session_id)

    # ページの読み込みを待つ
    logging.info("Waiting for the page to load...")
    while True:
        result = send_command(ws, "Page.getNavigationHistory", session_id=session_id)
        if result.get("result", {}).get("currentIndex", 0) > 0:
            break
        time.sleep(0.5)
    logging.info("Page loaded successfully.")

    # 新しいタブを作成
    new_tab = send_command(ws, "Target.createTarget", {"url": "about:blank"})
    new_target_id = new_tab["result"]["targetId"]

    logging.info(f"New target ID: {new_target_id}")
    # 新しいタブにアタッチ
    new_session_id = attach_to_target(ws, new_target_id)
    logging.info(f"New session ID: {new_session_id}")

    # 新しいタブでPageを有効化
    send_command(ws, "Page.enable", session_id=new_session_id)
    # 新しいタブでURLをナビゲート
    send_command(ws, "Page.navigate", {"url": url2}, session_id=new_session_id)

    # 新しいタブのページ読み込みを待つ
    logging.info("Waiting for the new tab to load...")
    while True:
        result = send_command(ws, "Page.getNavigationHistory", session_id=new_session_id)
        if result.get("result", {}).get("currentIndex", 0) > 0:
            break
        time.sleep(0.5)
    logging.info("New tab loaded successfully.")
    return session_id, new_session_id

def take_screenshot(ws, session_id, clip=None):
    params = {
        "format": "png",
        "fromSurface": True
    }
    if clip:
        params["clip"] = clip
    screenshot_result = send_command(ws, "Page.captureScreenshot", params, session_id=session_id)
    return base64.b64decode(screenshot_result["result"]["data"])

def process_screenshot(screenshot, coords, diff = None):
    cells = {}
    if diff is not None:
        diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    for name, (x, y, width, height) in coords.items():
        if diff is not None:
            roi = diff[y:y+height, x:x+width]
            if np.sum(roi) == 0:
                continue
        # crop the image
        cropped_image = screenshot[y:y+height, x:x+width]
        success, encoded_image = cv2.imencode('.png', cropped_image)
        cells[name] = encoded_image.tobytes()
    return cells

def process_screenshot_dow30(screenshot, diff = None):
    coords = {}

    origin_x, origin_y = 0, 0
    width, height = 187, 154
    x_gap = 8
    x_gap2 = 9
    y_gap_small = 1
    y_gap_large = 27

    x, y = origin_x, origin_y
    x += width
    coords["sunday_dow"] = (x, y, width, height)
    y += height + y_gap_small
    coords["nasdaq100_sunday"] = (x, y, width, height)
    y += height + y_gap_small
    coords["sp500_cfd"] = (x, y, width, height)

    y += height + y_gap_large
    coords["n225_cfd"] = (x, y, width, height)

    x, y, width, height = coords["sunday_dow"]
    x += width + x_gap + width
    coords["russel2000_cfd"] = (x, y, width, height)
    y += height + y_gap_small
    coords["vix"] = (x, y, width, height)
    y += height + y_gap_small
    coords["yield"] = (x, y, width, height)

    x += width + x_gap2
    y += height + y_gap_large
    coords["sunday_dollar"] = (x, y, width, height)

    x += width + width + x_gap2
    coords["gold_sunday"] = (x, y, width, height)

    y += height + y_gap_small
    coords["wti"] = (x, y, width, height)

    x += width
    coords["lng"] = (x, y, width, height)

    y += height + y_gap_small
    coords["copper"] = (x, y, width, height)

    x, y, width, height = coords["russel2000_cfd"]
    x += width + x_gap + width + width + x_gap2 + 1 + width + 1
    coords["date"] = (x, y, width, 114) # exclude minutes bar

    return process_screenshot(screenshot, coords, diff)

def process_screenshot_bitcoin(screenshot, diff = None):
    coords = {}

    origin_x, origin_y = 0, 0
    width, height = 187, 154
    x_gap = 8
    x_gap2 = 9
    y_gap_small = 1
    y_gap_large = 27

    x, y = origin_x, origin_y
    x += width + width + width
    y += height + y_gap_small

    coords["btcusd"] = (x, y, width, height)

    return process_screenshot(screenshot, coords, diff)

def on_connect(client, userdata, flags, rc, properties):
    logging.info(f"Connected to MQTT broker with result code {rc}")
    client.subscribe("sekai-kabuka", qos=1)

def on_message(client, userdata, message):
    global previous_screenshot_dow30, previous_screenshot_bitcoin
    topic = message.topic
    logging.info(f"Received message: {topic}")
    if topic == "sekai-kabuka":
        previous_screenshot_dow30 = None
        previous_screenshot_bitcoin = None

def main(mqtt_host, chrome_port, save_images=False, fps=FPS):
    global previous_screenshot_dow30, previous_screenshot_bitcoin

    # create a new Chrome browser instance
    chrome = start_chrome(chrome_port)
    ws_url = get_ws_url(chrome_port)
    ws = websocket.WebSocket()
    ws.connect(ws_url)

    logging.info("Connected to WebSocket")
    # ページを読み込む
    dow30, bitcoin = load_pages(ws, "https://sekai-kabuka.com/dow30.html", "https://sekai-kabuka.com/bitcoin.html")
    logging.info("Browser opened")

    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.on_connect = on_connect
    mqtt.on_message = on_message
    mqtt.connect(mqtt_host)
    mqtt.loop_start()

    try:
        while True:
            start_time = time.time()
            screenshot_png = take_screenshot(ws, dow30, clip={"x": 180, "y": 185, "width": 1530, "height": 960, "scale":1})
            screenshot = cv2.imdecode(np.frombuffer(screenshot_png, np.uint8), cv2.IMREAD_UNCHANGED)
            diff = cv2.absdiff(previous_screenshot_dow30, screenshot) if previous_screenshot_dow30 is not None else None
            if diff is not None and np.sum(diff) == 0: continue
            #else
            # Process the screenshot
            cells_dow30 = process_screenshot_dow30(screenshot, diff)
            previous_screenshot_dow30 = screenshot

            screenshot_png = take_screenshot(ws, bitcoin, clip={"x": 185, "y": 265, "width": 800, "height": 600, "scale":1})
            screenshot = cv2.imdecode(np.frombuffer(screenshot_png, np.uint8), cv2.IMREAD_UNCHANGED)
            diff = cv2.absdiff(previous_screenshot_bitcoin, screenshot) if previous_screenshot_bitcoin is not None else None
            if diff is not None and np.sum(diff) == 0: continue
            #else
            # Process the screenshot
            cells_bitcoin = process_screenshot_bitcoin(screenshot, diff)
            previous_screenshot_bitcoin = screenshot

            # merge the two dictionaries
            cells = {**cells_dow30, **cells_bitcoin}

            for name, cell in cells.items():
                logging.debug(f"Publishing {name}")
                # save the image to a file if debug
                if save_images:
                    with open("%s.png" % name, "wb") as f:
                        f.write(cell)
                # publish the image to MQTT.
                mqtt.publish("sekai-kabuka/%s" % name, payload=cell)
            end_time = time.time()
            elapsed_time = end_time - start_time
            if elapsed_time < 1.0 / fps:
                time.sleep(1.0 / fps - elapsed_time)
    except KeyboardInterrupt:
        logging.info("Exiting...")
    finally:
        mqtt.loop_stop()
        mqtt.disconnect()
        logging.info("MQTT disconnected")
        chrome.terminate()
        logging.info("Browser closed")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kabuka Pakuri")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker hostname")
    parser.add_argument("--chrome-port", type=int, default=9222, help="Chrome remote debugging port")
    parser.add_argument("--save-images", action="store_true", help="Save images to disk")
    parser.add_argument("--fps", type=float, default=FPS, help="Frames per second")
    parser.add_argument("--loglevel", type=str, default="info", help="Logging level (default: info)")
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel.upper())
    main(args.mqtt, args.chrome_port, args.save_images, args.fps)
