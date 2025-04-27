#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import cv2
import numpy as np
import time,logging
import paho.mqtt.client as mqtt_client

previous_screenshot_dow30 = None
previous_screenshot_bitcoin = None

FPS = 2.0

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

    origin_x, origin_y = 180, 185
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

    origin_x, origin_y = 185, 265
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

def main(mqtt_host, save_images=False, fps=FPS):
    global previous_screenshot_dow30, previous_screenshot_bitcoin

    # create a new Chrome browser instance
    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920,1440")
    chrome_options.add_argument("--headless")
    service = Service(executable_path='/usr/bin/chromedriver')
    driver_dow30 = webdriver.Chrome(service=service, options=chrome_options)
    driver_dow30.get("https://sekai-kabuka.com/dow30.html")
    driver_bitcoin = webdriver.Chrome(service=service, options=chrome_options)
    driver_bitcoin.get("https://sekai-kabuka.com/bitcoin.html")
    logging.info("Browser opened")

    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.on_connect = on_connect
    mqtt.on_message = on_message
    mqtt.connect(mqtt_host)
    mqtt.loop_start()

    try:
        while True:
            start_time = time.time()
            screenshot = cv2.imdecode(np.frombuffer(driver_dow30.get_screenshot_as_png(), np.uint8), cv2.IMREAD_UNCHANGED)
            diff = cv2.absdiff(previous_screenshot_dow30, screenshot) if previous_screenshot_dow30 is not None else None
            if diff is not None and np.sum(diff) == 0: continue
            #else
            # Process the screenshot
            cells_dow30 = process_screenshot_dow30(screenshot, diff)
            previous_screenshot_dow30 = screenshot

            screenshot = cv2.imdecode(np.frombuffer(driver_bitcoin.get_screenshot_as_png(), np.uint8), cv2.IMREAD_UNCHANGED)
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
        driver_dow30.quit()
        driver_bitcoin.quit()
        logging.info("Browser closed")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kabuka Pakuri")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker hostname")
    parser.add_argument("--save-images", action="store_true", help="Save images to disk")
    parser.add_argument("--fps", type=float, default=FPS, help="Frames per second")
    parser.add_argument("--loglevel", type=str, default="info", help="Logging level (default: info)")
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel.upper())
    main(args.mqtt, args.save_images, args.fps)
