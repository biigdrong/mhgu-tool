#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MHGU 装机助手 —— HTTP 服务与 API 路由。

用法：python app.py [--port 8777] [--no-browser]
界面：http://127.0.0.1:<port>/
"""
from __future__ import annotations

import json
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core
from core import APP_VERSION, CFG_PATH, TASKS, TASKS_LOCK, BRANCHES, start_task

UI_DIR = Path(__file__).resolve().parent / "ui"
CFG = core.load_config()
CFG_LOCK = threading.Lock()


def json_body(h):
    n = int(h.headers.get("Content-Length") or 0)
    if not n:
        return {}
    try:
        return json.loads(h.rfile.read(n).decode("utf-8"))
    except Exception:
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = f"MHGUTool/{APP_VERSION}"

    def log_message(self, fmt, *args):
        pass

    # ---------- helpers
    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, ctype="text/html; charset=utf-8"):
        try:
            data = path.read_bytes()
        except Exception:
            return self.send_json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- routes
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self.send_file(UI_DIR / "index.html")
            if p == "/api/ping":
                return self.send_json({"ok": True, "version": APP_VERSION})
            if p == "/api/config":
                with CFG_LOCK:
                    cfg = dict(CFG)
                return self.send_json({
                    "config": cfg,
                    "branches": {k: core.plan({**cfg, "branch": k}) for k in BRANCHES},
                    "version": APP_VERSION,
                })
            if p == "/api/detect":
                return self.send_json(detect())
            if p == "/api/task":
                tid = (q.get("id") or [""])[0]
                since = int((q.get("since") or ["0"])[0])
                with TASKS_LOCK:
                    t = TASKS.get(tid)
                if not t:
                    return self.send_json({"error": "no such task"}, 404)
                return self.send_json(t.snapshot(since))
            if p == "/api/tasks":
                with TASKS_LOCK:
                    items = [{"id": t.id, "kind": t.kind, "title": t.title,
                              "status": t.status, "progress": t.progress} for t in TASKS.values()]
                return self.send_json({"tasks": items[-20:]})
            if p == "/api/release":
                br = (q.get("branch") or ["touch"])[0]
                repo = BRANCHES[br]["repo"]
                try:
                    rel = core.latest_release(repo)
                    asset = core.pick_asset(rel, BRANCHES[br]["asset"])
                    return self.send_json({"ok": True, "repo": repo, "tag": rel.get("tag_name"),
                                           "name": rel.get("name"), "asset": asset,
                                           "published": rel.get("published_at")})
                except Exception as e:  # noqa: BLE001
                    return self.send_json({"ok": False, "error": str(e)}, 502)
            if p == "/api/adb/devices":
                try:
                    exe = core.find_adb(CFG)
                    return self.send_json({"ok": True, "adb": exe, "devices": core.adb_devices(exe)})
                except Exception as e:  # noqa: BLE001
                    return self.send_json({"ok": False, "error": str(e)})
            return self.send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": str(e), "trace": traceback.format_exc(limit=4)}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        body = json_body(self)
        try:
            if p == "/api/config":
                with CFG_LOCK:
                    for k, v in body.items():
                        if k in CFG or k in ("branch",):
                            CFG[k] = v
                    core.save_config(CFG)
                return self.send_json({"ok": True, "config": CFG})
            if p == "/api/browse":
                return self.send_json(browse(body.get("kind", "dir"), body.get("initial", "")))
            if p == "/api/start":
                kind = body.get("kind") or "deploy"
                with CFG_LOCK:
                    for k, v in (body.get("config") or {}).items():
                        if k in CFG:
                            CFG[k] = v
                    core.save_config(CFG)
                    cfg = dict(CFG)
                t = start_task(kind, title_of(kind, cfg), job_of(kind, cfg))
                return self.send_json({"ok": True, "task": t.snapshot()})
            if p == "/api/cancel":
                tid = body.get("id")
                with TASKS_LOCK:
                    t = TASKS.get(tid)
                if t:
                    t.cancel_flag.set()
                return self.send_json({"ok": bool(t)})
            if p == "/api/adb/connect":
                with CFG_LOCK:
                    cfg = dict(CFG)
                    if body.get("addr"):
                        CFG["adb_addr"] = body["addr"]
                addr = body.get("addr") or cfg.get("adb_addr")
                ip = body.get("ip") or cfg.get("adb_wifi_ip")

                def job(t):
                    exe = core.find_adb(cfg)
                    if not addr:
                        port = core.find_port(ip, t)
                        if not port:
                            raise RuntimeError("没扫到端口，请直接填 IP:端口")
                        a = f"{ip}:{port}"
                    else:
                        a = addr
                    serial = core.adb_connect_wifi(exe, a, t)
                    t.add("设备：" + serial)
                    return {"serial": serial, "addr": a}

                t = start_task("adb", "连接手机", job)
                return self.send_json({"ok": True, "task": t.snapshot()})
            return self.send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": str(e), "trace": traceback.format_exc(limit=4)}, 500)


def title_of(kind: str, cfg: dict) -> str:
    br = BRANCHES[cfg["branch"]]["name"]
    return {"deploy": f"一键部署 → {br}",
            "unpack": "仅解包游戏文件",
            "build": "仅生成游戏代码（触屏版）"}.get(kind, kind)


def job_of(kind: str, cfg: dict):
    if kind == "unpack":
        def job(t):
            from pathlib import Path as P
            br = BRANCHES[cfg["branch"]]
            out = P(cfg.get("out_dir") or (core.DATA / "out")) / br["key"]
            xci = P(cfg["xci_path"]) if cfg.get("xci_path") else None
            if not xci or not xci.is_file():
                raise RuntimeError("请先指定 XCI/NSP")
            keys = P(cfg["keys_path"]) if cfg.get("keys_path") else None
            if not keys or not keys.is_file():
                raise RuntimeError("请先指定 prod.keys")
            return core.unpack_xci(xci, keys, out, t)
        return job
    if kind == "build":
        def job(t):
            from pathlib import Path as P
            game = P(cfg["game_dir"]) if cfg.get("game_dir") else None
            if not game or not (game / "exefs" / "main").is_file():
                raise RuntimeError("请指定含 exefs/romfs 的目录")
            ok, bid = core.verify_exefs(game / "exefs")
            if not ok:
                raise RuntimeError(bid)
            gu = core.ensure_gu_native(t)
            ndk = core.ensure_ndk(cfg, t)
            return core.build_game(gu, game, ndk, int(cfg.get("jobs") or 4), t)
        return job
    return lambda t: core.deploy_task(cfg, t)


def detect() -> dict:
    """扫一遍常见位置，省得用户手输。"""
    from pathlib import Path as P
    out = {"xci": [], "game_dir": [], "keys": [], "adb": ""}
    roots = [P("I:/NS"), P("E:/"), P("D:/"), P("F:/"), P("G:/")]
    for r in roots:
        if not r.is_dir():
            continue
        try:
            for e in r.iterdir():
                n = e.name.lower()
                if e.is_file() and n.endswith((".xci", ".nsp")):
                    out["xci"].append(str(e))
                elif e.is_dir() and ("mhgu" in n or "怪物猎人" in e.name):
                    for sub in e.rglob("*"):
                        if sub.is_file() and sub.name.lower().endswith((".xci", ".nsp")):
                            out["xci"].append(str(sub))
                        if sub.is_dir() and sub.name.lower() in ("exefs", "romfs"):
                            if sub.parent.name.lower() not in out["game_dir"]:
                                out["game_dir"].append(str(sub.parent))
                        if len(out["xci"]) > 30:
                            break
        except Exception:
            continue
    for k in ("I:/NS/23.0.1keys/prod.keys", "I:/NS/23.0.1keys/prod-f57fb5068ffa.keys"):
        if P(k).is_file():
            out["keys"].append(k)
    for a in (r"F:\tools\android\platform-tools\adb.exe",):
        if P(a).is_file():
            out["adb"] = a
    out["xci"] = sorted(set(out["xci"]))[:20]
    out["game_dir"] = sorted(set(out["game_dir"]))[:10]
    return out


def browse(kind: str, initial: str) -> dict:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "file":
            p = filedialog.askopenfilename(
                title="选择 XCI / NSP / prod.keys", initialdir=initial or "/",
                filetypes=[("Switch 游戏包", "*.xci *.nsp"), ("key 文件", "*.keys"), ("全部", "*.*")])
        else:
            p = filedialog.askdirectory(title="选择文件夹", initialdir=initial or "/")
        root.destroy()
        return {"ok": bool(p), "path": p or ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"系统对话框不可用（{e}），请直接粘贴路径"}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    core.DATA.mkdir(parents=True, exist_ok=True)
    core.LOGS.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print()
    print("  MHGU 装机助手 —— 官方版 / 触屏版")
    print(f"  界面地址：{url}")
    print("  关闭本窗口即退出")
    print()
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
