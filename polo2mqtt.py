#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time,threading,logging,json,argparse,io
import websocket,requests,cairo,cairosvg # media-gfx/cairosvg
import paho.mqtt.client as mqtt_client

from gi import require_version
require_version("Pango", "1.0")
require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

channel = ["candles_minute_10"]
symbols = ["XMR_USDT", "BTC_USDT"]
xmrusdt_price_history = []

mqtt = None

CELL_WIDTH, CELL_HEIGHT = 187, 154

monero_svg = """
<svg id="Layer_1" data-name="Layer 1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3756.09 3756.49"><title>monero</title><path d="M4128,2249.81C4128,3287,3287.26,4127.86,2250,4127.86S372,3287,372,2249.81,1212.76,371.75,2250,371.75,4128,1212.54,4128,2249.81Z" transform="translate(-371.96 -371.75)" style="fill:#fff"/><path id="_149931032" data-name=" 149931032" d="M2250,371.75c-1036.89,0-1879.12,842.06-1877.8,1878,0.26,207.26,33.31,406.63,95.34,593.12h561.88V1263L2250,2483.57,3470.52,1263v1579.9h562c62.12-186.48,95-385.85,95.37-593.12C4129.66,1212.76,3287,372,2250,372Z" transform="translate(-371.96 -371.75)" style="fill:#f26822"/><path id="_149931160" data-name=" 149931160" d="M1969.3,2764.17l-532.67-532.7v994.14H1029.38l-384.29.07c329.63,540.8,925.35,902.56,1604.91,902.56S3525.31,3766.4,3855,3225.6H3063.25V2231.47l-532.7,532.7-280.61,280.61-280.62-280.61h0Z" transform="translate(-371.96 -371.75)" style="fill:#4d4d4d"/></svg>
"""
monero_surface = None

def fetch_xmrusdt_price_history():
    """
    PoloniexのAPIから過去24時間の10分足データを取得し、[startTime, close]のリストを返す。
    
    Returns:
        list: [[startTime, close], [startTime, close], ...] の形式。
              startTimeはUnixタイムスタンプ（ミリ秒）、closeはfloat。
              失敗した場合は空リストを返す。
    """
    url = "https://poloniex.com/proxy/sapi/spot/quotation/candlesticks?symbol=XMR_USDT&interval=MINUTE_10&limit=144"
    
    try:
        # APIリクエストを送信
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # ステータスコードが200でない場合例外を発生
        
        # JSONデータをパース
        data = response.json()
        
        # レスポンスの形式をチェック
        if data.get("code") != 200 or "data" not in data:
            print(f"API error: {data.get('message', 'Unknown error')}")
            return []
        
        # [startTime, close] のリストを抽出
        candles = [
            [candle[0], float(candle[3])]  # startTime: インデックス0, close: インデックス3
            for candle in data["data"]
        ]
        
        return candles
    
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return []
    except (ValueError, KeyError, IndexError) as e:
        print(f"Data parsing error: {e}")
        return []

def on_poloniex_public_message(data):
    global xmrusdt_price_history
    # convert the message to JSON
    # copy xmrusdt_price_history to a new list
    xmrusdt_price_history_new = [item[:] for item in xmrusdt_price_history]
    try:
        # check if the message is a candle
        if "data" not in data or not isinstance(data["data"], list): return
        #else
        for trade in data["data"]:
            if "symbol" not in trade or "startTime" not in trade or "close" not in trade: continue
            #else
            symbol = trade["symbol"]
            if symbol != "XMR_USDT": continue
            start_time = trade["startTime"]
            current_price = float(trade["close"])
            last_price_in_history = xmrusdt_price_history_new[-1] if xmrusdt_price_history_new else None
            if last_price_in_history is not None:
                if last_price_in_history[0] == start_time:
                    # 既存のデータを更新
                    xmrusdt_price_history_new[-1][1] = current_price
                if last_price_in_history[0] < start_time:
                    xmrusdt_price_history_new.append([start_time, current_price])
            logging.debug(current_price)
            # 履歴が長くなりすぎないよう制限
            while len(xmrusdt_price_history_new) > 144:
                xmrusdt_price_history_new.pop(0)
        # 更新された履歴を保存
        xmrusdt_price_history = xmrusdt_price_history_new

    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        return
    except Exception as e:
        logging.error(f"Error processing message: {e}")
        return

def rasterize_svg(svg_data, size):
    """
    convert SVG logo to caito surface
    """
    # SVGをPNGに変換
    png_data = io.BytesIO()
    cairosvg.svg2png(
        bytestring=svg_data.encode('utf-8'),
        output_width=size,
        output_height=size,
        write_to=png_data
    )
    png_data.seek(0)

    # PNGデータをCairoのImageSurfaceとして読み込み
    return cairo.ImageSurface.create_from_png(png_data)

def draw_xmrusdt_chart(ctx, xmrusdt_price_history, x, y):
    # 価格（close）の最小値と最大値を計算
    prices = [candle[1] for candle in xmrusdt_price_history]
    start_price = prices[0]  # 最初の価格
    min_price = min(prices)
    max_price = max(prices)

    def normalize_price(price):
        """価格を0～1に正規化する関数"""
        return (price - min_price) / (max_price - min_price)

    chart_height = CELL_HEIGHT / 2
    chart_width = 144
    def fit_to_chart(price):
        """価格をチャートの高さにスケールする関数"""
        return chart_height - (normalize_price(price) * chart_height)

    if max_price <= min_price: return  # 価格の変動がない場合は何もしない

    # fill "higher than start" area with green
    ctx.set_source_rgba(0, 1, 0.5, 0.2)  # 薄い緑
    ctx.set_line_width(1)
    ctx.rectangle(x, y, chart_width, fit_to_chart(start_price))  # rectangle from (x, y) to (x + chart_width, fit_to_chart(start_price))
    ctx.fill()  # fill the rectangle
    # fill "lower than start" area with red
    ctx.set_source_rgba(1, 0, 0, 0.2)  # 薄い赤
    ctx.rectangle(x, fit_to_chart(start_price) + y, chart_width, chart_height - fit_to_chart(start_price))  # rectangle from (x, fit_to_chart(start_price) + y) to (x + chart_width, y + chart_height)
    ctx.fill()  # fill the rectangle

    # 青色を設定
    ctx.set_source_rgb(0, 0, 1)  # RGB: (0, 0, 1) = 青
    ctx.set_line_width(1)

    # 価格をチャート高さに正規化して折れ線を描画
    for i in range(len(xmrusdt_price_history)):
        # 現在のデータポイント
        price = xmrusdt_price_history[i][1]
        plot_y = fit_to_chart(price) + y  # チャートのY座標を計算
        # 横軸：1データポイント=1ピクセル
        plot_x = x + i

        if i == 0:
            # 最初の点：線を開始
            ctx.move_to(plot_x, plot_y)
        else:
            # 以降の点：線を引く
            ctx.line_to(plot_x, plot_y)

    # 線を描画
    ctx.stroke()

    # draw left arrow at the position of current price
    # black stroke, white fill
    ctx.set_source_rgb(1, 1, 1)  # RGB: (1, 1, 1) = 白
    ctx.set_line_width(1)
    arrow_x = x + len(xmrusdt_price_history) + 1
    arrow_y = y + fit_to_chart(xmrusdt_price_history[-1][1])
    ctx.move_to(arrow_x, arrow_y)
    ctx.line_to(arrow_x + 16, arrow_y - 5) 
    ctx.line_to(arrow_x + 16, arrow_y + 5)
    ctx.close_path()
    ctx.fill()
    ctx.set_source_rgb(0, 0, 0)  # RGB: (0, 0, 0) = 黒
    ctx.set_line_width(1)
    ctx.move_to(arrow_x, arrow_y)
    ctx.line_to(arrow_x + 16, arrow_y - 5)
    ctx.line_to(arrow_x + 16, arrow_y + 5)
    ctx.close_path()
    ctx.stroke()

def draw_xmrusdt(xmrusdt_price_history):
    global monero_surface
    # create a Cairo surface
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_WIDTH, CELL_HEIGHT)
    ctx = cairo.Context(surface)

    # fill the background with white
    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.fill()

    if monero_surface is None:
        # create a Cairo surface from the SVG data
        monero_surface = rasterize_svg(monero_svg, 40)
    # draw the monero logo
    ctx.set_source_surface(monero_surface, 3, 3)
    ctx.paint()

    ctx.set_source_rgb(0, 0, 0)
    ctx.set_font_size(13)
    ctx.move_to(46, 16)
    ctx.show_text("XMR/USDT")

    if xmrusdt_price_history is not None and len(xmrusdt_price_history) > 0:
        draw_xmrusdt_chart(ctx, xmrusdt_price_history, 1, 50)

        start_price = xmrusdt_price_history[0][1]
        current_price = xmrusdt_price_history[-1][1]
        highest_price = max([candle[1] for candle in xmrusdt_price_history])
        lowest_price = min([candle[1] for candle in xmrusdt_price_history])

        if highest_price > lowest_price:
            # draw change in percentage
            change = (current_price - start_price) / start_price * 100
            if change > 0:
                ctx.set_source_rgb(0, 0.7, 0)
            elif change < 0:
                ctx.set_source_rgb(0.7, 0, 0)
            else:
                ctx.set_source_rgb(0, 0, 0)
            ctx.set_font_size(22)
            ctx.move_to(50, 43)
            ctx.show_text("%c%.02f%%" % (
                '+' if change > 0 else '-' if change < 0 else ' ',
                abs(change)
            ))

            # draw highest price
            if highest_price is not None:
                ctx.set_source_rgb(0, 0, 0)
                ctx.set_font_size(9)
                ctx.move_to(150, 50)
                ctx.show_text(f"{highest_price:.02f}")
            
            # draw lowest price
            if lowest_price is not None:
                ctx.set_source_rgb(0, 0, 0)
                ctx.set_font_size(9)
                ctx.move_to(150, 130)
                ctx.show_text(f"{lowest_price:.02f}")

        ctx.set_font_size(17)
        current_price_str = "%.02f" % current_price
        current_price_extents = ctx.text_extents(current_price_str)
        current_price_width = current_price_extents[2]
        height = current_price_extents[3]
        price_chanege_str = "%c%.02f" % (
            '+' if current_price > start_price else '-' if current_price < start_price else ' ',
            abs(current_price - start_price)
        )
        price_change_width = ctx.text_extents(price_chanege_str)[2]
        gap = 5
        total_width = current_price_width + price_change_width + gap
        ctx.set_source_rgb(0, 0, 0)
        ctx.move_to((CELL_WIDTH - total_width) / 2, CELL_HEIGHT - 5)
        ctx.show_text(current_price_str)
        if current_price > start_price:
            ctx.set_source_rgb(0, 0.7, 0)
        elif current_price < start_price:
            ctx.set_source_rgb(0.7, 0, 0)
        else:
            ctx.set_source_rgb(0, 0, 0)
        ctx.move_to((CELL_WIDTH - total_width) / 2 + current_price_width + gap, CELL_HEIGHT - 5)
        ctx.show_text(price_chanege_str)

    # draw the gray frame
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.stroke()

    # return as PNG binary
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()

def ping_thread(ws):
    try:
        while True:
            ws.send(json.dumps({"event": "ping"}))
            logging.debug("Ping sent")
            time.sleep(10)
    except Exception as e:
        logging.error(f"Ping thread error(disconnected?): {e}")

def on_open(ws):
    global xmrusdt_price_history
    xmrusdt_price_history = fetch_xmrusdt_price_history()

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
        #mqtt.publish("poloniex/public", message)
        on_poloniex_public_message(json.loads(message))
        png = draw_xmrusdt(xmrusdt_price_history)
        mqtt.publish("poloniex/xmrusdt", png)

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
