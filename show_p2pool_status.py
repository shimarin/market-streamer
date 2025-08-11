#!/usr/bin/env python3
import urllib.request
import json

URL = "http://xmr/local/stratum"

def fetch_status(url):
    with urllib.request.urlopen(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.load(resp)

def parse_workers(worker_list):
    # 例: "[2409:11:...:ddeb]:43220,850,176095,5869,rig07"
    names = []
    for entry in worker_list:
        # カンマ区切りの最終要素を名前とみなす
        parts = entry.split(",")
        names.append(parts[-1])
    return names

def main():
    data = fetch_status(URL)
    # hashrate_15m を取得
    hr_15m = data.get("hashrate_15m")
    # workers の名前一覧を取得
    workers = parse_workers(data.get("workers", []))

    # 出力
    print(f"hashrate_15m: {hr_15m}")
    print("workers:")
    for name in workers:
        print(f"  - {name}")

if __name__ == "__main__":
    main()
