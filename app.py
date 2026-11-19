# -*- coding: utf-8 -*-
"""
长文朗读器 —— 基于微软 Edge 在线语音（edge-tts）
特点：无字数上限、中文优先、句子级同步高亮、可导出 MP3、重复内容走本地缓存
运行：python app.py   （会自动打开浏览器）
"""

import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import edge_tts

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
os.makedirs(CACHE, exist_ok=True)

TICKS_PER_SEC = 10_000_000          # edge 的时间戳单位：100 纳秒
AUDIO_BYTES_PER_SEC = 48_000 / 8    # 48kbps CBR → 每秒 6000 字节
CHUNK_CHARS = 700                   # 单次请求字符数上限（越小越稳，进度也越细）
MAX_CHARS = 300_000

VOICES = {
    "zh-CN-YunxiNeural": "云希 · 男声 · 自然亲和（默认）",
    "zh-CN-XiaoxiaoNeural": "晓晓 · 女声 · 温柔清晰",
    "zh-CN-YunyangNeural": "云扬 · 男声 · 新闻播报",
    "zh-CN-XiaoyiNeural": "晓伊 · 女声 · 年轻活泼",
    "zh-CN-YunjianNeural": "云健 · 男声 · 浑厚有力",
    "zh-CN-YunxiaNeural": "云夏 · 男声 · 少年感",
}

JOBS = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------- 文本切分
SENT_RE = re.compile(r"[^。！？!?；;…]*[。！？!?；;…]+|[^。！？!?；;…]+")


def split_chunks(text: str, max_chars: int = CHUNK_CHARS):
    """按行→句切分，累积到不超过 max_chars 一片。避免在半句话处断开。"""
    chunks, buf = [], ""

    def flush():
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for sent in SENT_RE.findall(line):
            if not sent:
                continue
            # 单句本身就超长（无标点长文），硬切
            while len(sent) > max_chars:
                if buf:
                    flush()
                chunks.append(sent[:max_chars])
                sent = sent[max_chars:]
            if len(buf) + len(sent) > max_chars:
                flush()
            buf += sent
        buf += "\n"
    flush()
    return chunks


# ---------------------------------------------------------------- 合成
async def synth_chunk(text: str, voice: str, rate: str):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    audio = bytearray()
    bounds = []
    async for msg in comm.stream():
        t = msg["type"]
        if t == "audio":
            audio.extend(msg["data"])
        elif t in ("SentenceBoundary", "WordBoundary"):
            bounds.append({"text": msg["text"], "offset": int(msg["offset"])})
    return bytes(audio), bounds


async def run_synthesis(job, text, voice, rate):
    chunks = split_chunks(text)
    job["total"] = len(chunks)
    audio = bytearray()
    all_bounds = []
    cum_bytes = 0
    for i, ch in enumerate(chunks):
        err = None
        for attempt in range(3):
            try:
                data, bounds = await synth_chunk(ch, voice, rate)
                if not data:
                    raise RuntimeError("服务返回空音频")
                err = None
                break
            except Exception as e:                      # noqa: BLE001
                err = e
                await asyncio.sleep(1.5 * (attempt + 1))
        if err is not None:
            raise RuntimeError(f"第 {i + 1}/{len(chunks)} 段合成失败：{err}")

        base_ticks = cum_bytes * 8 * TICKS_PER_SEC // 48_000
        for b in bounds:
            all_bounds.append({"text": b["text"], "ticks": b["offset"] + base_ticks})
        audio.extend(data)
        cum_bytes += len(data)
        job["done"] = i + 1
        job["chars_done"] = min(len(text), job["chars_done"] + len(ch))

    return bytes(audio), all_bounds


def build_spans(text: str, bounds):
    """把服务返回的句子时间戳，映射回原文的字符区间（保证首尾相接、不重叠）。"""
    spans = []
    pos = 0
    for b in bounds:
        w = (b.get("text") or "").strip()
        idx = -1
        if w:
            idx = text.find(w, pos)
            if idx < 0 and len(w) > 10:
                idx = text.find(w[:10], pos)
            if idx < 0 and len(w) > 5:
                idx = text.find(w[:5], pos)
        if idx < 0:
            idx = pos
        if idx >= len(text):
            continue
        if spans and idx <= spans[-1]["s"]:
            continue
        spans.append({"s": idx, "t": b["ticks"] // 10_000})   # 毫秒
        pos = idx + 1

    for i in range(len(spans) - 1):
        spans[i]["e"] = spans[i + 1]["s"]
    if spans:
        spans[-1]["e"] = len(text)
    return spans


def cache_paths(key):
    return os.path.join(CACHE, key + ".mp3"), os.path.join(CACHE, key + ".json")


def make_key(text, voice, rate):
    h = hashlib.sha1()
    h.update(f"{voice}|{rate}|{text}".encode("utf-8"))
    return h.hexdigest()[:20]


def worker(key, text, voice, rate):
    job = JOBS[key]
    try:
        audio, bounds = asyncio.run(run_synthesis(job, text, voice, rate))
        spans = build_spans(text, bounds)
        duration = len(audio) * 8 * 1000 // 48_000
        mp3, meta = cache_paths(key)
        with open(mp3, "wb") as f:
            f.write(audio)
        with open(meta, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "text": text,
                    "voice": voice,
                    "rate": rate,
                    "duration": duration,
                    "chars": len(text),
                    "spans": spans,
                },
                f,
                ensure_ascii=False,
            )
        job["state"] = "ready"
        job["duration"] = duration
        job["spans"] = len(spans)
    except Exception as e:                              # noqa: BLE001
        job["state"] = "error"
        job["err"] = str(e)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "ReadAloud/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, download=None):
        if not os.path.exists(path):
            self.send_error(404, "not found")
            return
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header(
                "Content-Disposition", f"attachment; filename=\"{download}\""
            )
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remain = length
            while remain > 0:
                buf = f.read(min(65536, remain))
                if not buf:
                    break
                try:
                    self.wfile.write(buf)
                except (BrokenPipeError, ConnectionAbortedError, OSError):
                    return
                remain -= len(buf)

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/api/ping":
            self._json({"app": "readaloud", "pid": os.getpid()})
        elif p in ("/", "/index.html"):
            self._file(os.path.join(BASE, "index.html"), "text/html; charset=utf-8")
        elif p == "/api/voices":
            self._json(VOICES)
        elif p == "/api/job":
            key = (parse_qs(u.query).get("key") or [""])[0]
            job = JOBS.get(key)
            if not job:
                if os.path.exists(cache_paths(key)[0]):
                    self._json({"state": "ready"})
                else:
                    self._json({"state": "missing"}, 404)
            else:
                self._json(
                    {
                        "state": job["state"],
                        "done": job["done"],
                        "total": job["total"],
                        "chars_done": job["chars_done"],
                        "chars": job["chars"],
                        "spans": job.get("spans"),
                        "duration": job.get("duration"),
                        "err": job.get("err"),
                    }
                )
        elif p.startswith("/audio/"):
            key = p[7:].split(".")[0]
            dl = "朗读.mp3" if "dl" in u.query else None
            self._file(cache_paths(key)[0], "audio/mpeg", dl)
        elif p.startswith("/timings/"):
            key = p[9:].split(".")[0]
            self._file(cache_paths(key)[1], "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/synth":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:                               # noqa: BLE001
            self._json({"error": "请求格式错误"}, 400)
            return

        text = (data.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        voice = data.get("voice") or "zh-CN-YunxiNeural"
        rate = data.get("rate") or "+0%"
        if voice not in VOICES:
            voice = "zh-CN-YunxiNeural"
        if not text:
            self._json({"error": "文本为空"}, 400)
            return
        if len(text) > MAX_CHARS:
            self._json({"error": f"文本过长（{len(text)} 字），上限 {MAX_CHARS} 字"}, 400)
            return

        key = make_key(text, voice, rate)
        mp3, _ = cache_paths(key)
        if os.path.exists(mp3):
            self._json({"key": key, "cached": True, "chars": len(text)})
            return

        with JOBS_LOCK:
            job = JOBS.get(key)
            if job is None or job["state"] == "error":
                job = {
                    "state": "running",
                    "done": 0,
                    "total": max(1, len(split_chunks(text))),
                    "chars_done": 0,
                    "chars": len(text),
                    "err": None,
                }
                JOBS[key] = job
                threading.Thread(
                    target=worker, args=(key, text, voice, rate), daemon=True
                ).start()
        self._json({"key": key, "cached": False, "chars": len(text)})


def pick_port(start=8731):
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def port_open(port, timeout=0.25):
    """毫秒级判断端口有没有人监听（不走 HTTP、不碰系统代理）"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def already_running(port):
    """端口有人听着，再确认是不是朗读器本身（绕过代理，直连）"""
    if not port_open(port):
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return b"readaloud" in r.read()
    except Exception:                                   # noqa: BLE001
        return False


def open_browser(url):
    """三层兜底：shell 关联 → webbrowser → cmd start。任何一层成功即返回"""
    try:
        if hasattr(os, "startfile"):
            os.startfile(url)                            # noqa: S606
            return True
    except Exception:                                   # noqa: BLE001
        pass
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:                                   # noqa: BLE001
        pass
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", url],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:                                   # noqa: BLE001
        return False


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:                                   # noqa: BLE001
        pass

    # 已经有一个在跑：直接把页面打开，不再起第二个
    for p in range(8731, 8741):
        if already_running(p):
            url = f"http://127.0.0.1:{p}/"
            ok = open_browser(url)
            print("=" * 52)
            print("  朗读器已经在运行了，不需要再开一个")
            print(f"  地址：{url}")
            if not ok:
                print("  浏览器没能自动打开，请手动复制上面这行地址")
            print("=" * 52)
            return

    port = pick_port()
    url = f"http://127.0.0.1:{port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print("=" * 52)
    print("  长文朗读器已启动")
    print(f"  地址：{url}")
    print("  这个窗口不要关，关掉服务就停了")
    print("=" * 52)
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(
            1.0, lambda: print("  浏览器已打开" if open_browser(url)
                               else f"  浏览器没自动打开，请手动访问 {url}")
        ).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
