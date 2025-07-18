#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess,logging,time,io,json

import cairo,cairosvg # media-gfx/cairosvg
from gi import require_version
require_version("Pango", "1.0")
require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

import paho.mqtt.client as mqtt_client # dev-python/paho-mqtt
import requests

charts = {}
topics = {}
xmrusdt_price_history = []
xmr_balance = None
xmr_unlocked_balance = None
p2pool_data = None

monero_svg = """
<svg id="Layer_1" data-name="Layer 1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3756.09 3756.49"><title>monero</title><path d="M4128,2249.81C4128,3287,3287.26,4127.86,2250,4127.86S372,3287,372,2249.81,1212.76,371.75,2250,371.75,4128,1212.54,4128,2249.81Z" transform="translate(-371.96 -371.75)" style="fill:#fff"/><path id="_149931032" data-name=" 149931032" d="M2250,371.75c-1036.89,0-1879.12,842.06-1877.8,1878,0.26,207.26,33.31,406.63,95.34,593.12h561.88V1263L2250,2483.57,3470.52,1263v1579.9h562c62.12-186.48,95-385.85,95.37-593.12C4129.66,1212.76,3287,372,2250,372Z" transform="translate(-371.96 -371.75)" style="fill:#f26822"/><path id="_149931160" data-name=" 149931160" d="M1969.3,2764.17l-532.67-532.7v994.14H1029.38l-384.29.07c329.63,540.8,925.35,902.56,1604.91,902.56S3525.31,3766.4,3855,3225.6H3063.25V2231.47l-532.7,532.7-280.61,280.61-280.62-280.61h0Z" transform="translate(-371.96 -371.75)" style="fill:#4d4d4d"/></svg>
"""
monero_surface = None

# poloniex account balance
eq = None
upl = None

UPDATE_FPS = 5
MOVIE_FPS = 15
BGCOLOR_R, BGCOLOR_G, BGCOLOR_B = (0.0, 1.0, 0.0)
frame_count = 0

WIDTH, HEIGHT = 1216, 684
CELL_WIDTH, CELL_HEIGHT = 187, 154
GAP_X, GAP_Y = 20, 20

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

def run_ffmpeg(rtmp_url):
    # Run ffmpeg to stream the video
    return subprocess.Popen([
        'ffmpeg',
        '-y',
        '-f', 'rawvideo',
        '-pix_fmt', 'bgra',
        '-s', f"{WIDTH}x{HEIGHT}",
        '-r', str(UPDATE_FPS),
        '-i', '-',
        '-vf', 'fps=' + str(MOVIE_FPS) + ',format=yuv420p',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-tune', 'zerolatency',
        '-f', 'flv',
        rtmp_url,
    ], 
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
    )

def on_poloniex_public_message(message):
    global xmrusdt_price_history
    # convert the message to JSON
    # copy xmrusdt_price_history to a new list
    xmrusdt_price_history_new = [item[:] for item in xmrusdt_price_history]
    try:
        data = json.loads(message.payload)
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

def on_poloniex_account_message(message):
    global eq, upl
    data = json.loads(message.payload)
    eq_str = data.get("eq")
    eq = float(eq_str) if eq_str is not None else None
    upl_str = data.get("upl")
    upl = float(upl_str) if upl_str is not None else None

def on_poloniex_positions_message(message):
    pass

def on_p2pool_message(message):
    global p2pool_data
    data = json.loads(message.payload)
    shares = []
    for n in data["shares"]:
        shares.append(int(n, 16))
    uncles = []
    for n in data["uncles"]:
        uncles.append(int(n, 16))
    payouts = []
    for n in data["payouts"]:
        payouts.append(int(n, 16))
    p2pool_data = {
        "shares": shares,
        "uncles": uncles,
        "payouts": payouts
    }

def on_connect(client, userdata, flags, rc, properties):
    logging.info(f"Connected to MQTT broker with result code {rc}")
    rst = client.subscribe("sekai-kabuka/+", qos=1)
    if rst[0] == mqtt_client.MQTT_ERR_SUCCESS:
        topics[rst[1]] = "sekai-kabuka/+"
    rst = client.subscribe("poloniex/public", qos=1)
    if rst[0] == mqtt_client.MQTT_ERR_SUCCESS:
        topics[rst[1]] = "poloniex/public"
    rst = client.subscribe("poloniex/account", qos=1)
    if rst[0] == mqtt_client.MQTT_ERR_SUCCESS:
        topics[rst[1]] = "poloniex/account"
    rst = client.subscribe("poloniex/positions", qos=1)
    if rst[0] == mqtt_client.MQTT_ERR_SUCCESS:
        topics[rst[1]] = "poloniex/positions"
    rst = client.subscribe("p2pool/+", qos=1)
    if rst[0] == mqtt_client.MQTT_ERR_SUCCESS:
        topics[rst[1]] = "p2pool/+"

def on_subscribe(client, userdata, mid, granted_qos, properties):
    topic_subscribed = topics[mid]
    logging.info(f"Subscribed to topic with mid: {topic_subscribed}, granted_qos: {granted_qos}")
    if topic_subscribed == "sekai-kabuka/+":
        client.publish("sekai-kabuka", "REQUEST ALL")
    elif topic_subscribed == "p2pool/+":
        client.publish("p2pool", "REQUEST ALL")

def on_message(client, userdata, message):
    topic = message.topic
    #logging.info(f"Received message: {topic}")
    if topic.startswith("sekai-kabuka/"):
        name = topic.split("/")[-1]
        charts[name] = message.payload
    elif topic == "poloniex/public":
        on_poloniex_public_message(message)
    elif topic == "poloniex/account":
        on_poloniex_account_message(message)
    elif topic == "poloniex/positions":
        on_poloniex_positions_message(message)
    elif topic.startswith("p2pool/"):
        on_p2pool_message(message)

def draw_png(ctx, png, x, y):
    if png is None: return
    # Draw a cell at the given coordinates
    surface = cairo.ImageSurface.create_from_png(io.BytesIO(png))
    ctx.set_source_surface(surface, x, y)
    ctx.paint()

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

def draw_xmrusdt_chart(ctx, x, y):
    global xmrusdt_price_history
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

def draw_xmrusdt(ctx, x, y):
    global monero_surface, xmrusdt_price_history
    # fill the background with white
    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(x, y, CELL_WIDTH, CELL_HEIGHT)
    ctx.fill()

    if monero_surface is None:
        # create a Cairo surface from the SVG data
        monero_surface = rasterize_svg(monero_svg, 40)
    # draw the monero logo
    ctx.set_source_surface(monero_surface, x + 3, y + 3)
    ctx.paint()

    ctx.set_source_rgb(0, 0, 0)
    ctx.set_font_size(13)
    ctx.move_to(x + 46, y + 16)
    ctx.show_text("XMR/USDT")

    if xmrusdt_price_history is not None and len(xmrusdt_price_history) > 0:
        draw_xmrusdt_chart(ctx, x + 1, y + 50)

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
            ctx.move_to(x + 50, y + 43)
            ctx.show_text("%c%.02f%%" % (
                '+' if change > 0 else '-' if change < 0 else ' ',
                abs(change)
            ))

            # draw highest price
            if highest_price is not None:
                ctx.set_source_rgb(0, 0, 0)
                ctx.set_font_size(9)
                ctx.move_to(x + 150, y + 50)
                ctx.show_text(f"{highest_price:.02f}")
            
            # draw lowest price
            if lowest_price is not None:
                ctx.set_source_rgb(0, 0, 0)
                ctx.set_font_size(9)
                ctx.move_to(x + 150, y + 130)
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
        ctx.move_to(x + (CELL_WIDTH - total_width) / 2, y + CELL_HEIGHT - 5)
        ctx.show_text(current_price_str)
        if current_price > start_price:
            ctx.set_source_rgb(0, 0.7, 0)
        elif current_price < start_price:
            ctx.set_source_rgb(0.7, 0, 0)
        else:
            ctx.set_source_rgb(0, 0, 0)
        ctx.move_to(x + (CELL_WIDTH - total_width) / 2 + current_price_width + gap, y + CELL_HEIGHT - 5)
        ctx.show_text(price_chanege_str)

    # draw the gray frame
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.rectangle(x, y, CELL_WIDTH, CELL_HEIGHT)
    ctx.stroke()

def draw_balance(ctx, x, y):
    global eq, upl
    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(x, y, CELL_WIDTH, 114)
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
        ctx.move_to(x + 3, y + 1)
        PangoCairo.show_layout(ctx, pango_layout)
        ctx.set_font_size(22)
        eq_str = f"{eq:.2f}ドル"
        eq_extents = ctx.text_extents(eq_str)
        eq_width = eq_extents[2]
        ctx.move_to(x + (CELL_WIDTH - eq_width) - 4, y + 42)
        ctx.show_text(eq_str)
    if upl is not None:
        ctx.set_font_size(16)
        ctx.move_to(x + 3, y + 68)
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
        ctx.move_to(x + (CELL_WIDTH - upl_width) - 4, y + 100)
        ctx.show_text(upl_str)

    # draw the gray frame
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.rectangle(x, y, CELL_WIDTH, 114)
    ctx.stroke()

def draw_p2pool_chart(ctx, x, y, height, data, color=(0, 0, 1)):
    min_data = 0
    max_data = max(data)
    # 青色を設定

    ctx.set_source_rgb(*color)
    ctx.set_line_width(1)

    def fit_to_chart(data):
        """データをチャートの高さにスケールする関数"""
        if max_data == 0: return height
        return height - ((data - min_data) / (max_data - min_data) * height)

    # 価格をチャート高さに正規化して折れ線を描画
    for i in range(len(data)):
        # 現在のデータポイント
        current_data = data[i]
        plot_y = fit_to_chart(current_data) + y  # チャートのY座標を計算
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

def draw_p2pool(ctx, x, y):
    global p2pool_data, xmr_balance
    width, height = 122, 64

    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(x, y, width, height)
    ctx.fill()

    if p2pool_data is not None:
        chart_height = (height) / 5

        cy = y + 3
        draw_p2pool_chart(ctx, x + 1, cy, chart_height, p2pool_data["shares"], (0, 0, 1))
        cy += chart_height + 3
        draw_p2pool_chart(ctx, x + 1, cy, chart_height, p2pool_data["uncles"], (0, 0.5, 0.5))
        cy += chart_height + 3
        draw_p2pool_chart(ctx, x + 1, cy, chart_height, p2pool_data["payouts"], (1, 0, 0))

    # draw the XMR balance
    if xmr_balance is not None and xmr_unlocked_balance is not None:
        ctx.set_source_rgb(0, 0, 0)
        ctx.set_font_size(12)
        xmr_balance_str = f"{xmr_unlocked_balance:.4f}{'+' if xmr_balance > xmr_unlocked_balance else ''} XMR"
        xmr_balance_extents = ctx.text_extents(xmr_balance_str)
        xmr_balance_width = xmr_balance_extents[2]
        ctx.move_to(x + (width - xmr_balance_width) - 4, y + height - 3)
        ctx.show_text(xmr_balance_str)

    # draw the gray frame
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.rectangle(x, y, width, height)
    ctx.stroke()

def draw_frame(surface):
    global frame_count, charts, eq, upl
    ctx = cairo.Context(surface)

    x, y = GAP_X, GAP_Y
    draw_png(ctx, charts.get("sunday_dow"), x, y)
    x += CELL_WIDTH
    draw_png(ctx, charts.get("nasdaq100_sunday"), x, y)
    x -= CELL_WIDTH
    y += CELL_HEIGHT
    draw_png(ctx, charts.get("sp500_cfd"), x, y)
    x += CELL_WIDTH
    draw_png(ctx, charts.get("russel2000_cfd"), x, y)
    x -= CELL_WIDTH
    y += CELL_HEIGHT + GAP_Y
    draw_png(ctx, charts.get("vix"), x, y)
    x += CELL_WIDTH
    draw_png(ctx, charts.get("yield"), x, y)

    x -= CELL_WIDTH
    y += CELL_HEIGHT + GAP_Y
    draw_png(ctx, charts.get("date"), x, y)
    x += CELL_WIDTH
    draw_balance(ctx, x, y)

    x += CELL_WIDTH + GAP_X
    y = GAP_Y
    draw_png(ctx, charts.get("n225_cfd"), x, y)
    x += CELL_WIDTH
    draw_png(ctx, charts.get("sunday_dollar"), x, y)
    y += CELL_HEIGHT + GAP_Y
    x -= CELL_WIDTH
    draw_png(ctx, charts.get("btcusd"), x, y)
    x += CELL_WIDTH
    draw_xmrusdt(ctx, x, y)
    x -= CELL_WIDTH
    y += CELL_HEIGHT + GAP_Y
    draw_p2pool(ctx, x, y)

    y = GAP_Y
    x += CELL_WIDTH + CELL_WIDTH + GAP_X
    draw_png(ctx, charts.get("gold_sunday"), x, y)
    y += CELL_HEIGHT
    draw_png(ctx, charts.get("lng"), x, y)
    y += CELL_HEIGHT
    draw_png(ctx, charts.get("palladium"), x, y)
    y = GAP_Y
    x += CELL_WIDTH
    draw_png(ctx, charts.get("wti"), x, y)
    y += CELL_HEIGHT
    draw_png(ctx, charts.get("copper"), x, y)

def main(mqtt_host, rtmp_url):
    global frame_count, xmrusdt_price_history, xmr_balance, xmr_unlocked_balance
    xmrusdt_price_history = fetch_xmrusdt_price_history()

    # MQTTクライアント設定
    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.on_connect = on_connect
    mqtt.on_subscribe = on_subscribe
    mqtt.on_message = on_message
    mqtt.connect(mqtt_host)
    mqtt.loop_start()  # run in a separate thread

    ffmpeg = run_ffmpeg(rtmp_url)
    logging.info("FFmpeg started")

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(BGCOLOR_R, BGCOLOR_G, BGCOLOR_B)
    ctx.rectangle(0, 0, WIDTH, HEIGHT)
    ctx.fill()
    del ctx

    last_xmr_balance_check = 0

    try:
        while True:
            start_time = time.time()

            # Check if we need to fetch new XMR price history
            if start_time - last_xmr_balance_check > 60 * 10:
                xmr_balance, xmr_unlocked_balance = fetch_xmr_balance()
                last_xmr_balance_check = start_time

            draw_frame(surface)

            ffmpeg.stdin.write(surface.get_data())
            ffmpeg.stdin.flush()
            frame_count += 1

            current_time = time.time()
            elapsed_time = current_time - start_time
            if elapsed_time < 1.0 / UPDATE_FPS:
                time.sleep(1.0 / UPDATE_FPS - elapsed_time)
            else:
                logging.warning("Frame processing took too long, skipping frame")
    except KeyboardInterrupt:
        logging.info("Exiting...")
    finally:
        ffmpeg.stdin.close()
        mqtt.loop_stop()
        mqtt.disconnect()
        logging.info("MQTT disconnected")

if __name__ == "__main__":
    # Argument parser
    DEFAULT_RTMP_SERVER = "rtmp://localhost/live/sekai-kabuka"
    import argparse
    parser = argparse.ArgumentParser(description="Market streamer")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--rtmp", type=str, default=DEFAULT_RTMP_SERVER, help="RTMP server address")
    parser.add_argument("--loglevel", type=str, default="info", help="Set the logging level (debug, info, warning, error)")
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel.upper(), format='%(asctime)s - %(levelname)s - %(message)s')

    main(args.mqtt, args.rtmp)