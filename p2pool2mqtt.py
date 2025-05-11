#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging,time,json
import paho.mqtt.client as mqtt_client
import requests
import bs4

def get_data(alias):
    # fetch https://p2pool.observer/miner/{alias} and parse the html
    soup = bs4.BeautifulSoup(requests.get(f"https://p2pool.observer/miner/{alias}").text, "html.parser")
    # select all div > code[class="mono"]
    elements = soup.select("div > code[class='mono']")
    # get the text of the first element
    if len(elements) < 4:
        logging.warning(f"No sufficient data found(expected 5): {elements}")
        return
    shares = elements[2].get_text()[2:-2].replace("|", "").replace(".","0")
    uncles = elements[3].get_text()[2:-2].replace("|", "").replace(".","0")
    payouts = elements[4].get_text()[2:-2].replace("|", "").replace(".","0") if len(elements) > 4 else "0" * 120
    if len(shares) != 120 or len(uncles) != 120 or len(payouts) != 120:
        logging.warning(f"Data length mismatch(expected 120): {shares}, {uncles}, {payouts}")
        return
    logging.debug(f"shares: {shares}, uncles: {uncles}, payouts: {payouts}")
    return {
        "shares": shares,
        "uncles": uncles,
        "payouts": payouts
    }

def get_data_and_publish(client, aliases):
    for alias in aliases:
        data = get_data(alias)
        if data:
            # Publish the data to MQTT
            client.publish(f"p2pool/{alias}", json.dumps(data), qos=1)
            logging.debug(f"Published data to p2pool/{alias}: {data}")
        else:
            logging.warning(f"No data to publish for {alias}")

def on_connect(client, userdata, flags, rc, properties=None):
    logging.info(f"Connected to MQTT broker with result code {rc}")
    get_data_and_publish(client, userdata["aliases"])
    # Subscribe to the topic with userdata = aliases
    client.subscribe(f"p2pool", qos=1)
    logging.info(f"Subscribed to p2pool")

# Callback function for when a message is received
def on_message(client, userdata, msg):
    if msg.topic != "p2pool":
        logging.warning(f"Unexpected topic: {msg.topic}")
        return
    #else
    get_data_and_publish(client, userdata["aliases"])

def main(mqtt_host, alias):
    # MQTTクライアント設定
    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    # MQTTのコールバック関数

    # Set the userdata to the aliases
    mqtt.user_data_set({"aliases": [alias]})
    # Set the callback function
    mqtt.on_connect = on_connect
    mqtt.on_message = on_message
    # Connect to the MQTT broker
    mqtt.connect(mqtt_host)
    # Start the MQTT loop
    mqtt.loop_start()
    try:
        while True:
            get_data_and_publish(mqtt, [alias])
            time.sleep(300)
    except KeyboardInterrupt:
        logging.info("Exiting...")
        mqtt.loop_stop()
        mqtt.disconnect()
        logging.info("MQTT disconnected")
        logging.info("MQTT loop stopped")

if __name__ == "__main__":
    # Argument parser
    import argparse
    parser = argparse.ArgumentParser(description="P2pool.observer to MQTT bridge")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("alias", type=str, help="P2pool alias")
    args = parser.parse_args()
    # Set logging level
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    #print(get_data())
    main(args.mqtt, args.alias)