"""mochi-files server, including public Scratch project download."""
import io
import json
import os
import re
import zipfile
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory

app = Flask(__name__, static_folder=None)
ROOT = os.path.dirname(__file__)
session = requests.Session()
session.headers.update({"User-Agent": "mochi-files/1.0 (Scratch public project downloader)"})
MAX_ASSET = 30 * 1024 * 1024


def read_public(url, limit):
    with session.get(url, stream=True, timeout=(8, 35), allow_redirects=False) as response:
        response.raise_for_status()
        output = io.BytesIO()
        for block in response.iter_content(65536):
            output.write(block)
            if output.tell() > limit:
                raise ValueError("ファイルサイズ制限を超えました")
        return output.getvalue()


def get_asset(name):
    if not re.fullmatch(r"[0-9a-fA-F]{32}\.[a-zA-Z0-9]{1,8}", name):
        raise ValueError("不正なアセット名")
    return name, read_public(
        "https://assets.scratch.mit.edu/internalapi/asset/" + quote(name) + "/get/",
        MAX_ASSET,
    )


def public_meta(pid):
    response = session.get("https://api.scratch.mit.edu/projects/" + str(pid), timeout=15)
    response.raise_for_status()
    return response.json()



# Ephemeral discovery and WebRTC signaling. No file bytes are stored here.
PEERS = {}
PEER_LOCK = threading.Lock()
PEER_TTL = 35


def prune_peers():
    now = time.time()
    for key in list(PEERS):
        if now - PEERS[key]["seen"] > PEER_TTL:
            del PEERS[key]


@app.post("/api/share/register")
def share_register():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "デバイス")).strip()[:40] or "デバイス"
    with PEER_LOCK:
        prune_peers()
        if len(PEERS) >= 100:
            return jsonify(error="接続数の上限です"), 429
        token = secrets.token_urlsafe(32)
        PEERS[token] = {"name": name, "seen": time.time(), "queue": deque(maxlen=150)}
    return jsonify(token=token)


@app.post("/api/share/heartbeat")
def share_heartbeat():
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    with PEER_LOCK:
        prune_peers()
        if token not in PEERS:
            return jsonify(error="接続が終了しました"), 401
        PEERS[token]["seen"] = time.time()
        return jsonify(peers=[
            {"id": key, "name": value["name"]}
            for key, value in PEERS.items() if key != token
        ])


@app.post("/api/share/events")
def share_events():
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    with PEER_LOCK:
        prune_peers()
        if token not in PEERS:
            return jsonify(error="接続が終了しました"), 401
        peer = PEERS[token]
        peer["seen"] = time.time()
        events = list(peer["queue"])
        peer["queue"].clear()
    return jsonify(events=events)


@app.post("/api/share/signal")
def share_signal():
    data = request.get_json(silent=True) or {}
    sender, recipient = data.get("token"), data.get("to")
    event = data.get("event")
    if not isinstance(event, dict) or len(json.dumps(event)) > 16000:
        return jsonify(error="不正な信号です"), 400
    if event.get("type") not in ("request", "accept", "reject", "offer", "answer", "ice", "cancel"):
        return jsonify(error="不正なイベントです"), 400
    with PEER_LOCK:
        prune_peers()
        if sender not in PEERS or recipient not in PEERS or sender == recipient:
            return jsonify(error="送信先が見つかりません"), 404
        PEERS[recipient]["queue"].append({"from": sender, "name": PEERS[sender]["name"], "event": event})
    return jsonify(ok=True)


@app.post("/api/share/leave")
def share_leave():
    token = (request.get_json(silent=True) or {}).get("token")
    with PEER_LOCK:
        PEERS.pop(token, None)
    return jsonify(ok=True)


@app.get("/")
def home():
    return send_from_directory(ROOT, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    if filename not in ("style.css", "app.js"):
        return jsonify(error="Not found"), 404
    return send_from_directory(ROOT, filename)


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/api/scratch/<int:pid>/info")
def info(pid):
    try:
        item = public_meta(pid)
        return jsonify(
            title=item.get("title", ""),
            image=item.get("image", ""),
            author={"username": item.get("author", {}).get("username", "")},
        )
    except (requests.RequestException, ValueError):
        return jsonify(error="作品情報を取得できませんでした"), 502


@app.get("/api/scratch/<int:pid>/download")
def download(pid):
    if pid < 1:
        return jsonify(error="作品IDが正しくありません"), 400
    try:
        metadata = public_meta(pid)
        project_bytes = read_public(
            "https://projects.scratch.mit.edu/" + str(pid), 30 * 1024 * 1024
        )
        project = json.loads(project_bytes)
        if not isinstance(project, dict) or "targets" not in project:
            raise ValueError("作品データを解析できません")
        names = set()
        for target in project["targets"]:
            for kind in ("costumes", "sounds"):
                for asset in target.get(kind, []):
                    name = asset.get("md5ext") or (
                        str(asset.get("assetId", "")) + "." + str(asset.get("dataFormat", ""))
                    )
                    names.add(name)
        if len(names) > 1200:
            return jsonify(error="素材数が多すぎます"), 413
        result = io.BytesIO()
        total = len(project_bytes)
        with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project.json", project_bytes)
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(get_asset, name) for name in names]
                for future in as_completed(futures):
                    name, contents = future.result()
                    total += len(contents)
                    if total > 250 * 1024 * 1024:
                        raise ValueError("作品が大きすぎます")
                    archive.writestr(name, contents)
        result.seek(0)
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", metadata.get("title") or str(pid))[:70]
        return send_file(result, as_attachment=True, download_name=filename+".sb3", mimetype="application/zip")
    except (requests.RequestException, ValueError, json.JSONDecodeError, zipfile.BadZipFile):
        return jsonify(error="作品を取得できません。公開状態やScratch側の制限を確認してね"), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
