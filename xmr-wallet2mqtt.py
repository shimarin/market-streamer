#!/usr/bin/python3
import logging,time,io,argparse
import requests,cairo
import paho.mqtt.client as mqtt_client # dev-python/paho-mqtt

from gi import require_version
require_version("Pango", "1.0")
require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

xmr_balance = None
xmr_unlocked_balance = None

#CELL_WIDTH, CELL_HEIGHT = 122, 64
CELL_WIDTH, CELL_HEIGHT = 187, 114

DEFAULT_WALLET_RPC_URL = "http://localhost:18082/json_rpc"  # Monero wallet RPC URL
DEFAULT_P2POOL_STATUS_URL = "http://xmr/local/stratum"  # p2pool status URL

def fetch_xmr_balance(url):
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
        response = requests.post(url, headers=headers, json=payload)
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

def fetch_p2pool_status(url):
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        logging.error(f"Request timed out after 5 seconds")
        return None
    except requests.RequestException as e:
        logging.error(f"Error fetching p2pool status: {e}")
        return None

def parse_workers(worker_list):
    # 例: "[2409:11:...:ddeb]:43220,850,176095,5869,rig07"
    names = []
    for entry in worker_list:
        # カンマ区切りの最終要素を名前とみなす
        parts = entry.split(",")
        names.append(parts[-1])
    return names

def fit_text_to_rect(text, font_family, rect_w_px, rect_h_px,
                     min_pt=6.0, max_pt=200.0, wrap=True, dpi=96.0):
    """
    指定矩形に収まる最大ポイントサイズを二分探索で求め、layout とサイズを返す。
    """
    # 仮の image surface（実描画前の計測用）
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, rect_w_px), max(1, rect_h_px))
    cr = cairo.Context(surf)
    PangoCairo.context_set_resolution(PangoCairo.create_context(cr), dpi)  # 任意（通常は 96dpi）

    layout = PangoCairo.create_layout(cr)
    layout.set_text(text, -1)

    # 幅を Pango 単位で設定
    layout.set_width(rect_w_px * Pango.SCALE)
    if wrap:
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)  # 単語優先で足りなければ文字折返し
        layout.set_ellipsize(Pango.EllipsizeMode.NONE)
    else:
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_ellipsize(Pango.EllipsizeMode.END)  # 収まらない場合は末尾を…

    # 二分探索でポイントサイズ決定
    lo, hi = min_pt, max_pt
    best = min_pt
    for _ in range(24):  # 精度充分
        mid = (lo + hi) / 2.0
        desc = Pango.FontDescription()
        desc.set_family(font_family)
        desc.set_absolute_size(mid * Pango.SCALE)  # ポイントではなく "device unit" 指定。PangoはSCALE固定
        layout.set_font_description(desc)

        _, logical = layout.get_pixel_extents()
        w_px, h_px = logical.width, logical.height

        if w_px <= rect_w_px and h_px <= rect_h_px:
            best = mid
            lo = mid  # もっと大きく
        else:
            hi = mid  # 小さく

        if hi - lo < 0.1:  # 0.1pt 以内で収束
            break

    # 最終サイズを反映
    desc = Pango.FontDescription()
    desc.set_family(font_family)
    desc.set_absolute_size(best * Pango.SCALE)
    layout.set_font_description(desc)
    return layout, best

def draw(xmr_balance, xmr_unlocked_balance, p2pool_status):

    xmr_balance_str = "N/A"
    if xmr_balance is not None and xmr_unlocked_balance is not None:
        xmr_balance_str = f"{xmr_unlocked_balance:.4f}{'+' if xmr_balance > xmr_unlocked_balance else ''}XMR"

    hr_15m = "N/A"
    workers_str = "N/A"
    if p2pool_status is not None:
        if "hashrate_15m" in p2pool_status:
            hr_15m =  f"{p2pool_status.get("hashrate_15m")}H/s"
        if "workers" in p2pool_status:
            # workers の名前一覧を取得
            workers = parse_workers(p2pool_status.get("workers", []))
            workers_str = f"{len(workers)}({', '.join(workers)})"

    text = f"ハッシュレート: {hr_15m}\nワーカー: {workers_str}\nウォレット残高: {xmr_balance_str}"
    layout, pt = fit_text_to_rect(text, "Sans Serif", CELL_WIDTH - 6, CELL_HEIGHT)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL_WIDTH, CELL_HEIGHT)
    ctx = cairo.Context(surface)

    # 背景
    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.fill()

    ctx.set_source_rgb(0, 0, 0)  # 黒

    # レイアウトの高さを中央に配置
    _, logical = layout.get_pixel_extents()
    x = 0  # 左寄せ。中央寄せにしたい場合は (rect_w_px - logical.width)/2
    y = (CELL_HEIGHT - logical.height) / 2

    ctx.move_to(x + 3, y)
    PangoCairo.show_layout(ctx, layout)

    ctx.rectangle(0, 0, CELL_WIDTH, CELL_HEIGHT)
    ctx.stroke()

    # return as PNG binary
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()

def main(mqtt_host, wallet_rpc_url, p2pool_status_url):
    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.connect(mqtt_host)
    mqtt.loop_start()  # run in a separate thread

    xmr_balance, xmr_unlocked_balance = None, None

    try:
        while True:
            xmr_balance, xmr_unlocked_balance = fetch_xmr_balance(wallet_rpc_url)
            p2pool_status = fetch_p2pool_status(p2pool_status_url)
            png = draw(xmr_balance, xmr_unlocked_balance, p2pool_status)
            mqtt.publish("xmr/balance", png)
            time.sleep(10)
    finally:
        mqtt.loop_stop()
        mqtt.disconnect()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Market streamer")
    parser.add_argument("--mqtt", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--wallet-rpc-url", type=str, default=DEFAULT_WALLET_RPC_URL, help="Monero wallet RPC URL")
    parser.add_argument("--p2pool-status-url", type=str, default=DEFAULT_P2POOL_STATUS_URL, help="p2pool status URL")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    wallet_rpc_url = args.wallet_rpc_url
    p2pool_status_url = args.p2pool_status_url
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    main(args.mqtt, wallet_rpc_url, p2pool_status_url)
