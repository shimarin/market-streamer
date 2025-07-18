#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,base64,subprocess,time,logging,tempfile
import urllib.request
import websocket # dev-python/websocket-client
import cv2 # media-libs/opencv
import numpy as np
import paho.mqtt.client as mqtt_client

# コマンドIDを管理するためのカウンタ
command_id = 0

previous_screenshot_dow30 = None
previous_screenshot_bitcoin = None

CHROME_WIDTH = 1920
CHROME_HEIGHT = 1440
CHROME_PORT = 9222

FPS = 2.0

# ヘルパー関数：Chromeを指定ポートで起動
def start_chrome(port, user_data_dir, width=CHROME_WIDTH, height=CHROME_HEIGHT, headless=True):
    cmdline = [
        "google-chrome-stable",
        "--no-first-run",
        "--no-default-browser-check",
        "--incognito",
        f"--user-data-dir={user_data_dir}",
        f"--window-size={width},{height}",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=*",
        #"--no-sandbox",
        "--process-per-site",
        "--disable-background-timer-throttling",
        #"--no-zygote",
        #"--disable-setuid-sandbox",
        "--disable-features=TranslateUI,Translate",
        "--hide-scrollbars",
        "--enable-unsafe-swiftshader"
    ]
    if headless:
        cmdline.append("--headless")
    logging.debug(f"Starting Chrome with command: {' '.join(cmdline)}")
    process = subprocess.Popen(
        cmdline
    )
    time.sleep(2)  # Chromeの起動を待つ
    return process

# ヘルパー関数：WebSocketデバッグURLを取得
def get_ws_url(port):
    for i in range(10):
        try:
            url = f'http://localhost:{port}/json/version'
            logging.debug(f"Fetching WebSocket URL from {url}")
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read())
                return data['webSocketDebuggerUrl']
        except Exception as e:
            logging.error(f"Error getting WebSocket URL: {e}")
            time.sleep(1)
    raise Exception("Failed to get WebSocket URL")

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
def get_initial_target(ws, port=CHROME_PORT):
    with urllib.request.urlopen(f'http://localhost:{port}/json') as response:
        targets = json.loads(response.read())
        for target in targets:
            if target["type"] == "page":
                return target["id"]
    raise Exception("No page target found")

def block_ad(ws, session_id):
    send_command(ws, "Network.setBlockedURLs", {
        "urls": [
            "*.doubleclick.net", "*.adservice.google.com","*.googleadservices.com",
            "*.google","*.google.com",
            "xn--jx2a33n.com"
        ]
    }, session_id=session_id)

def load_pages(ws, port, url1, url2):
    # 初期ターゲットを取得
    initial_target_id = get_initial_target(ws, port)
    logging.info(f"Initial target ID: {initial_target_id}")
    # 初期ターゲットにアタッチ
    session_id = attach_to_target(ws, initial_target_id)
    logging.info(f"Session ID: {session_id}")
    # 初期ターゲットでPage, Networkを有効化
    send_command(ws, "Page.enable", session_id=session_id)
    send_command(ws, "Network.enable", session_id=session_id)

    # 広告ブロック
    #block_ad(ws, session_id)

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

    # 新しいウィンドウを作成
    new_window = send_command(ws, "Target.createTarget", {
        "url": "about:blank",
        "newWindow": True
    })
    new_target_id = new_window["result"]["targetId"]

    logging.info(f"New target ID: {new_target_id}")
    # 新しいウィンドウにアタッチ
    new_session_id = attach_to_target(ws, new_target_id)
    logging.info(f"New session ID: {new_session_id}")

    # 新しいウィンドウでPage, Networkを有効化
    send_command(ws, "Page.enable", session_id=new_session_id)
    send_command(ws, "Network.enable", session_id=new_session_id)

    # 広告ブロック
    #block_ad(ws, new_session_id)

    # 新しいウィンドウでURLをナビゲート
    send_command(ws, "Page.navigate", {"url": url2}, session_id=new_session_id)

    # 新しいウィンドウのページ読み込みを待つ
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

def main(mqtt_host, chrome_port, chrome_user_dir, save_images=False, fps=FPS, debug=False):
    global previous_screenshot_dow30, previous_screenshot_bitcoin

    # create a new Chrome browser instance
    chrome = start_chrome(chrome_port, chrome_user_dir, width=CHROME_WIDTH, height=CHROME_HEIGHT, headless=not debug)
    try:
        ws_url = get_ws_url(chrome_port)
    except Exception as e:
        logging.error(f"Error getting WebSocket URL: {e}")
        chrome.terminate()
        raise

    ws = websocket.WebSocket(skip_utf8_validation=True)
    ws.connect(ws_url)

    logging.info("Connected to WebSocket")

    # ページを読み込む
    dow30, bitcoin = load_pages(ws, chrome_port, "https://sekai-kabuka.com/dow30.html", "https://sekai-kabuka.com/bitcoin.html")
    logging.info("Browser opened")

    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.on_connect = on_connect
    mqtt.on_message = on_message
    mqtt.connect(mqtt_host)
    mqtt.loop_start()

    last_reload_time = time.time()

    try:
        while True:
            start_time = time.time()
            screenshot_png = take_screenshot(ws, dow30, clip={"x": 188, "y": 185, "width": 1530, "height": 960, "scale":1})
            screenshot = cv2.imdecode(np.frombuffer(screenshot_png, np.uint8), cv2.IMREAD_UNCHANGED)
            diff = cv2.absdiff(previous_screenshot_dow30, screenshot) if previous_screenshot_dow30 is not None else None
            cells_dow30 = process_screenshot_dow30(screenshot, diff) if diff is not None else {}
            previous_screenshot_dow30 = screenshot

            screenshot_png = take_screenshot(ws, bitcoin, clip={"x": 193, "y": 265, "width": 800, "height": 600, "scale":1})
            screenshot = cv2.imdecode(np.frombuffer(screenshot_png, np.uint8), cv2.IMREAD_UNCHANGED)
            diff = cv2.absdiff(previous_screenshot_bitcoin, screenshot) if previous_screenshot_bitcoin is not None else None
            cells_bitcoin = process_screenshot_bitcoin(screenshot, diff) if diff is not None else {}
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
            
            # reload the page if 1 hour have passed
            if time.time() - last_reload_time > 3600:
                logging.info("Reloading pages...")
                send_command(ws, "Page.reload", session_id=dow30)
                send_command(ws, "Page.reload", session_id=bitcoin)
                last_reload_time = time.time()

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
    parser.add_argument("--chrome-port", type=int, default=CHROME_PORT, help="Chrome remote debugging port")
    parser.add_argument("--save-images", action="store_true", help="Save images to disk")
    parser.add_argument("--fps", type=float, default=FPS, help="Frames per second")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    with tempfile.TemporaryDirectory(prefix="chrome_debug_") as user_data_dir:
        main(args.mqtt, args.chrome_port, user_data_dir, args.save_images, args.fps, args.debug)

