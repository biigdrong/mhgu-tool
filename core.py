#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MHGU 装机助手 —— 核心逻辑。

两条支路共用「解包 → 校验 → 合并 1.4.0 更新」；只有触屏分支需要在本机编译游戏代码。
中文（chS 简体 / chT 繁体）是官方 1.4.0 更新自带的多语言资源，不是外挂汉化补丁。
  official : morerokk/MHGURecomp     APK 自带 libmhgugame.so（113.7 MB），无需编译
  touch    : khorne-hub/gu-native    APK 仅 6.9 MB，必须用 NDK 从自有 exefs 生成 so

只处理本机文件，不上传任何东西。唯一的外部下载是 GitHub 的 APK / 源码与 Google 的 NDK。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_VERSION = "0.1.0"

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
TOOLS = BASE / "tools"
DL = DATA / "downloads"
WORK = DATA / "work"
CACHE = DATA / "tools"
LOGS = DATA / "logs"
CFG_PATH = DATA / "config.json"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

BRANCHES = {
    "official": {
        "key": "official",
        "name": "官方版 MHGU Recomp",
        "repo": "morerokk/MHGURecomp",
        "asset": r"^Android-MHGURecomp\..*\.apk$",
        "package": "io.github.morerokk.mhgurecomp",
        "needs_build": False,
        "needs_folder_pick": False,
        "device_data": "/sdcard/Android/data/io.github.morerokk.mhgurecomp/files/data",
        "device_note": "游戏文件直接推进 app 私有目录，装完即用",
        "apk_hint": "约 136 MB，内含编译好的游戏代码",
    },
    "touch": {
        "key": "touch",
        "name": "触屏版 gu-native",
        "repo": "khorne-hub/gu-native",
        "asset": r"^MHGU-.*\.apk$",
        "package": "com.mhgu.recomp",
        "needs_build": True,
        "needs_folder_pick": True,
        "device_data": "/sdcard/MHGU",
        "device_note": "游戏文件夹必须放在公共目录（不能进 Android/data），进 app 再选一次",
        "apk_hint": "约 7 MB，不含游戏代码，需本机生成 so",
    },
}

DEFAULTS = {
    "branch": "touch",
    "game_dir": "",           # 含 exefs / romfs 的文件夹
    "xci_path": "",           # 也可以直接给 xci / nsp
    "keys_path": "",
    "adb_path": "",
    "adb_wifi_ip": "192.168.5.12",
    "adb_addr": "",
    "ndk_path": "",
    "jobs": os.cpu_count() or 4,
    "out_dir": str(DATA / "out"),
    "last_apk": {},
    "last_build": {},
}

CFG_LOCK = threading.Lock()


# ---------------------------------------------------------------- 配置
def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CFG_PATH.read_text("utf-8")))
    except Exception:
        pass
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: dict) -> None:
    with CFG_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------- 任务框架
TASKS: dict[str, "Task"] = {}
TASKS_LOCK = threading.Lock()
_TASK_SEQ = [0]


class Task:
    def __init__(self, kind: str, title: str):
        _TASK_SEQ[0] += 1
        self.id = f"t{int(time.time())}_{_TASK_SEQ[0]}"
        self.kind = kind
        self.title = title
        self.status = "running"      # running | done | failed | cancelled
        self.step = ""
        self.progress = 0.0          # 0..1，未知进度时保持 -1
        self.message = ""
        self.error = ""
        self.log: list[str] = []
        self.result: dict = {}
        self.started = time.time()
        self.cancel_flag = threading.Event()

    def add(self, line: str) -> None:
        line = str(line).rstrip()
        if not line:
            return
        stamp = time.strftime("%H:%M:%S")
        with TASKS_LOCK:
            self.log.append(f"[{stamp}] {line}")
            if len(self.log) > 4000:
                del self.log[:1000]
        try:
            with open(LOGS / f"{self.id}.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def set(self, step: str = None, progress: float = None, message: str = None) -> None:
        if step is not None:
            self.step = step
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def snapshot(self, since: int = 0) -> dict:
        with TASKS_LOCK:
            lines = self.log[since:]
            total = len(self.log)
        return {
            "id": self.id, "kind": self.kind, "title": self.title,
            "status": self.status, "step": self.step, "progress": self.progress,
            "message": self.message, "error": self.error, "result": self.result,
            "lines": lines, "cursor": total,
            "elapsed": round(time.time() - self.started, 1),
        }


def start_task(kind: str, title: str, fn) -> Task:
    t = Task(kind, title)
    with TASKS_LOCK:
        TASKS[t.id] = t
        for old in list(TASKS):
            if TASKS[old].status != "running" and len(TASKS) > 40:
                TASKS.pop(old, None)

    def runner():
        try:
            t.result = fn(t) or {}
            if t.status == "running":
                t.status = "done"
                t.progress = 1.0
                t.set(step="完成")
        except Cancelled:
            t.status = "cancelled"
            t.add("已取消")
        except Exception as e:  # noqa: BLE001
            t.status = "failed"
            t.error = str(e)
            t.add(f"失败：{e}")
            import traceback
            t.add(traceback.format_exc(limit=6))

    threading.Thread(target=runner, daemon=True, name=f"task-{kind}").start()
    return t


class Cancelled(Exception):
    pass


def check_cancel(t: Task) -> None:
    if t.cancel_flag.is_set():
        raise Cancelled()


# ---------------------------------------------------------------- 子进程
def run(cmd, cwd=None, timeout=None) -> tuple[int, str]:
    r = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    return r.returncode, r.stdout or ""


def run_stream(cmd, t: Task, cwd=None, quiet=False, on_line=None) -> int:
    """逐行回传子进程输出，便于界面看进度。"""
    t.add("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        errors="replace", bufsize=1, creationflags=CREATE_NO_WINDOW,
    )
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip()
        if line and not quiet:
            t.add(line)
        if on_line:
            on_line(line)
    p.wait()
    return p.returncode


# ---------------------------------------------------------------- hactool
def hactool_exe() -> Path:
    p = TOOLS / ("hactool.exe" if os.name == "nt" else "hactool")
    if not p.is_file():
        raise RuntimeError(f"缺少 {p.name}：请把 hactool 放进 tools 目录")
    return p


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def hactool_info(keys: Path, path: Path) -> str:
    code, out = run([hactool_exe(), "-k", keys, "-t", "nca", path])
    return out


def xci_list_secure(xci: Path, keys: Path) -> list[str]:
    """返回 secure 分区里的 NCA 文件名。"""
    code, out = run([hactool_exe(), "-k", keys, "-t", "xci", xci], timeout=600)
    if code != 0 and "Secure Partition" not in out:
        raise RuntimeError("hactool 读不了这个 XCI：\n" + out[-1500:])
    names = re.findall(r"secure:/([\w.\-]+\.nca)", out)
    return names


def nca_type(keys: Path, nca: Path) -> tuple[str, str]:
    out = hactool_info(keys, nca)
    ctype = ""
    m = re.search(r"Content Type:\s*(\w+)", out)
    if m:
        ctype = m.group(1)
    return ctype, out


def pick_program_ncas(sec: Path, keys: Path, t: Task) -> tuple[Path, Path | None]:
    """在已解密的 NCA 里挑出 base 与 update 的 Program NCA（按体积判断）。"""
    ncas = sorted(sec.glob("*.nca"))
    if not ncas:
        raise RuntimeError("secure 分区里没有 NCA")
    progs = []
    for n in ncas:
        ctype, _ = nca_type(keys, n)
        if ctype.lower() == "program":
            progs.append((n.stat().st_size, n))
    if not progs:
        raise RuntimeError("没找到 Program NCA，这个包可能不是游戏本体")
    progs.sort(reverse=True)
    base = progs[0][1]
    update = progs[1][1] if len(progs) > 1 else None
    t.add(f"base NCA   {base.name}  {human(progs[0][0])}")
    if update:
        t.add(f"update NCA {update.name}  {human(progs[1][0])}")
    else:
        t.add("只有一份 Program NCA（未整合更新），后面会因版本不符而拒绝")
    return base, update


# ---------------------------------------------------------------- 校验 / 语言
BUILD_ID_140 = bytes.fromhex("FB08F1D20FD1204F")


def verify_exefs(exefs: Path) -> tuple[bool, str]:
    main = exefs / "main"
    if not main.is_file():
        return False, "缺 exefs/main"
    if not (exefs / "sdk").is_file():
        return False, "缺 exefs/sdk"
    with open(main, "rb") as f:
        head = f.read(0x48)
    if head[:4] != b"NSO0":
        return False, "exefs/main 不是 NSO0，文件可能没解对"
    bid = head[0x40:0x48]
    if bid != BUILD_ID_140:
        return False, f"版本不符：build ID {bid.hex().upper()}（需要 {BUILD_ID_140.hex().upper()}，即 1.4.0）"
    return True, bid.hex().upper()


def count_chs(romfs: Path) -> int:
    d = romfs / "nativeNX" / "chS"
    if not d.is_dir():
        return 0
    n = 0
    for _, _, files in os.walk(d):
        n += len(files)
    return n


def count_files(root: Path) -> int:
    n = 0
    for _, _, files in os.walk(root):
        n += len(files)
    return n


def dir_size(root: Path) -> int:
    total = 0
    for dp, _, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 解包
def unpack_xci(xci: Path, keys: Path, out: Path, t: Task) -> dict:
    """XCI → out/exefs + out/romfs（romfs 已合并 update 增量，中文 chS/chT 即来自该更新）。"""
    out.mkdir(parents=True, exist_ok=True)
    exefs = out / "exefs"
    romfs = out / "romfs"

    t.set(step="读取 XCI 分区", progress=0.02)
    names = xci_list_secure(xci, keys)
    t.add(f"secure 分区 {len(names)} 个 NCA")

    sec = WORK / "sec"
    if sec.exists():
        shutil.rmtree(sec, ignore_errors=True)
    sec.mkdir(parents=True, exist_ok=True)

    t.set(step="解密 NCA（约 14 GB 落盘，最耗时的一步之一）", progress=0.05)
    code = run_stream([hactool_exe(), "-k", keys, "-t", "xci", "-x", f"--securedir={sec}", xci],
                      t, quiet=True)
    if code != 0:
        raise RuntimeError("NCA 解密失败，看日志末尾")
    t.add(f"已解出 {len(list(sec.glob('*.nca')))} 个 NCA")

    base, update = pick_program_ncas(sec, keys, t)
    if not update:
        raise RuntimeError("这个包没有 1.4.0 更新，无法用于两条支路（官方与触屏版都要求 1.4.0）")

    t.set(step="提取 exefs（来自 1.4.0 更新）", progress=0.35)
    if exefs.exists():
        shutil.rmtree(exefs, ignore_errors=True)
    exefs.mkdir(parents=True, exist_ok=True)
    code = run_stream([hactool_exe(), "-k", keys, "-t", "nca", "-x", f"--exefsdir={exefs}", update], t, quiet=True)
    ok, info = verify_exefs(exefs)
    if not ok:
        raise RuntimeError("exefs 校验不通过：" + info)
    t.add(f"exefs OK，build ID {info}")

    t.set(step="提取基础 romfs（12 GB，最耗时的一步）", progress=0.4)
    if romfs.exists():
        shutil.rmtree(romfs, ignore_errors=True)
    romfs.mkdir(parents=True, exist_ok=True)

    def romfs_progress(line: str):
        m = re.search(r"Saving .*?(\d+)\s*$", line)
        if "Saving" in line:
            t.set(progress=0.4 + 0.35 * min(1.0, t.progress))

    code = run_stream([hactool_exe(), "-k", keys, "-t", "nca", "-x", f"--romfsdir={romfs}", base],
                      t, quiet=True, on_line=romfs_progress)
    if code != 0:
        raise RuntimeError("romfs 提取失败，看日志末尾")
    base_files = count_files(romfs)
    t.add(f"基础 romfs {base_files} 个文件")

    t.set(step="合并 1.4.0 更新增量（官方中文语言包在这里）", progress=0.78)
    upd = WORK / "romfs_update"
    if upd.exists():
        shutil.rmtree(upd, ignore_errors=True)
    upd.mkdir(parents=True, exist_ok=True)
    code = run_stream([hactool_exe(), "-k", keys, "-t", "nca", "-x",
                       f"--basenca={base}", "--onlyupdated", f"--romfsdir={upd}", update],
                      t, quiet=True)
    if code != 0:
        t.add("警告：增量合并返回非 0，继续尝试覆盖")
    merged = 0
    for dp, _, files in os.walk(upd):
        rel = Path(dp).relative_to(upd)
        dst_dir = romfs / rel
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(Path(dp) / f, dst_dir / f)
            merged += 1
    t.add(f"覆盖 {merged} 个增量文件")

    shutil.rmtree(sec, ignore_errors=True)
    shutil.rmtree(upd, ignore_errors=True)

    chs = count_chs(romfs)
    total = count_files(romfs)
    size = dir_size(romfs)
    t.add(f"完成：{total} 个文件 / {human(size)}，其中简体中文 {chs} 个（官方 1.4.0 内置）")
    t.set(step="解包完成", progress=0.82)
    return {"exefs": str(exefs), "romfs": str(romfs), "files": total,
            "size": size, "chs": chs, "build_id": info}


# ---------------------------------------------------------------- GitHub / APK
GH_MIRRORS = ["", "https://ghfast.top/", "https://gh-proxy.com/"]


def http_json(url: str, timeout=30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": f"mhgu-tool/{APP_VERSION}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def latest_release(repo: str) -> dict:
    return http_json(f"https://api.github.com/repos/{repo}/releases/latest")


def pick_asset(rel: dict, pattern: str) -> dict | None:
    rx = re.compile(pattern)
    for a in rel.get("assets", []):
        if rx.match(a.get("name", "")):
            return a
    return None


def download(url: str, dest: Path, t: Task, expect_size: int = 0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for mirror in GH_MIRRORS:
        u = url if not mirror else mirror + url
        try:
            t.add(f"下载 {dest.name} ← {mirror or 'github'}")
            req = urllib.request.Request(u, headers={"User-Agent": f"mhgu-tool/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or expect_size or 0)
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        t.set(progress=0.9 * got / total, message=f"{human(got)} / {human(total)}")
            if expect_size and dest.stat().st_size != expect_size:
                raise RuntimeError(f"大小不符：{dest.stat().st_size} != {expect_size}")
            return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
            t.add(f"  失败：{e}")
            dest.unlink(missing_ok=True)
    raise RuntimeError(f"下载失败：{last_err}")


# ---------------------------------------------------------------- 编译（触屏分支）
GU_ZIP = "https://github.com/khorne-hub/gu-native/archive/refs/heads/main.zip"
NDK_RELEASE = "r27"
NDK_REV = "27.0.12077973"
NDK_URL = f"https://dl.google.com/android/repository/android-ndk-{NDK_RELEASE}-windows.zip"


def ensure_gu_native(t: Task) -> Path:
    d = CACHE / "gu-native"
    if (d / "tools" / "build_game.py").is_file():
        t.add(f"已有 gu-native 源码：{d}")
        return d
    t.set(step="获取触屏分支源码", progress=0.02)
    z = CACHE / "gu-native.zip"
    download(GU_ZIP, z, t)
    import zipfile
    tmp = CACHE / "_gu"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(tmp)
    inner = next(tmp.iterdir())
    shutil.move(str(inner), str(d))
    shutil.rmtree(tmp, ignore_errors=True)
    z.unlink(missing_ok=True)
    if not (d / "tools" / "build_game.py").is_file():
        raise RuntimeError("gu-native 源码结构异常：没有 tools/build_game.py")
    t.add(f"源码就位：{d}")
    return d


def ndk_host_tag() -> str:
    return "windows-x86_64" if os.name == "nt" else "linux-x86_64"


def ensure_ndk(cfg: dict, t: Task) -> Path:
    cand = [cfg.get("ndk_path"), os.environ.get("ANDROID_NDK_HOME"), os.environ.get("ANDROID_NDK_ROOT"),
            str(CACHE / f"android-ndk-{NDK_RELEASE}")]
    for c in cand:
        if c and (Path(c) / "toolchains" / "llvm" / "prebuilt" / ndk_host_tag()).is_dir():
            t.add(f"使用 NDK：{c}")
            return Path(c)
    if os.name != "nt":
        raise RuntimeError("本工具目前只自动下载 Windows 版 NDK；Linux 请自行指定 --ndk")
    t.set(step="下载 Android NDK（约 1.8 GB，只需一次）", progress=0.02)
    z = CACHE / f"android-ndk-{NDK_RELEASE}.zip"
    if not z.is_file() or z.stat().st_size < 500 * 1024 * 1024:
        download(NDK_URL, z, t)
    t.set(step="解压 NDK", progress=0.12)
    import zipfile
    with zipfile.ZipFile(z) as zf:
        zf.extractall(CACHE)
    z.unlink(missing_ok=True)
    ndk = CACHE / f"android-ndk-{NDK_RELEASE}"
    if not ndk.is_dir():
        raise RuntimeError("NDK 解压后目录不对")
    t.add(f"NDK 就位：{ndk}")
    return ndk


def build_game(gu: Path, game: Path, ndk: Path, jobs: int, t: Task) -> dict:
    t.set(step="编译游戏代码（6 分钟起步）", progress=0.2)
    cmd = [sys.executable, str(gu / "tools" / "build_game.py"), str(game), "--ndk", str(ndk), "--jobs", str(jobs)]
    total_mark = [0]
    done_mark = [0]

    def on_line(line: str):
        m = re.search(r"Step (\d) of 3", line)
        if m:
            step = int(m.group(1))
            t.set(progress=0.2 + 0.2 * (step - 1))
        m = re.search(r"compiling (\d+) files", line)
        if m:
            total_mark[0] = int(m.group(1))
        m = re.match(r"\s*(\d+) of (\d+)\s*$", line)
        if m and total_mark[0]:
            done_mark[0] = int(m.group(1))
            t.set(progress=0.4 + 0.55 * done_mark[0] / total_mark[0],
                  message=f"编译 {done_mark[0]}/{total_mark[0]}")
        if line.startswith("Dowloading") or "Downloading the Android NDK" in line:
            t.set(step="下载 NDK（1.8 GB）")

    code = run_stream(cmd, t, on_line=on_line)
    so = game / "libmhgugame.so"
    idf = game / "libmhgugame.id"
    if code != 0 or not so.is_file():
        raise RuntimeError("编译失败，看日志末尾的 clang 输出")
    t.set(step="编译完成", progress=0.98)
    return {"so": str(so), "id": str(idf) if idf.is_file() else "",
            "size": so.stat().st_size, "gid": idf.read_text("utf-8").strip() if idf.is_file() else ""}


# ---------------------------------------------------------------- adb
def find_adb(cfg: dict) -> str:
    cands = [cfg.get("adb_path"),
             r"F:\tools\android\platform-tools\adb.exe",
             str(Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe")]
    for c in cands:
        if c and Path(c).is_file():
            return c
    w = shutil.which("adb")
    if w:
        return w
    raise RuntimeError("找不到 adb：请在设置里指定 adb.exe 路径")


def adb(adb_exe: str, *args, timeout=60) -> tuple[int, str]:
    return run([adb_exe, *args], timeout=timeout)


def adb_devices(adb_exe: str) -> list[str]:
    code, out = adb(adb_exe, "devices")
    devs = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line:
            devs.append(line.split("\t")[0])
    return devs


def adb_connect_wifi(adb_exe: str, addr: str, t: Task) -> str:
    t.add(f"连接 {addr}")
    code, out = adb(adb_exe, "connect", addr)
    t.add(out.strip())
    time.sleep(1.5)
    devs = adb_devices(adb_exe)
    if not devs:
        raise RuntimeError("连接后仍无设备。手机需开启「无线调试」，端口每次会变；配对失效要重新配对")
    return devs[0]


def find_port(ip: str, t: Task, lo=30000, hi=50000) -> int | None:
    """无线调试端口每次轮换，这里扫一遍（并发 300，超时 0.5 s）。"""
    import queue
    from concurrent.futures import ThreadPoolExecutor

    def probe(p):
        s = socket.socket()
        s.settimeout(0.5)
        try:
            return p if s.connect_ex((ip, p)) == 0 else None
        finally:
            s.close()

    t.add(f"扫描 {ip} 的 30000-50000 端口（约 1 分钟）")
    with ThreadPoolExecutor(max_workers=300) as ex:
        for i, r in enumerate(ex.map(probe, range(lo, hi + 1))):
            if r:
                t.add(f"发现开放端口 {r}")
                return r
            if i % 4000 == 0:
                t.set(progress=i / (hi - lo))
    return None


def push_tree(adb_exe: str, serial: str, src: Path, dst: str, t: Task, label="") -> None:
    """先打 tar 再推，比逐个文件快一个档；手机上解压后删包。"""
    tar_path = WORK / f"push_{int(time.time())}.tar"
    t.set(step=f"打包 {label or src.name}", progress=0.0)
    with tarfile.open(tar_path, "w") as tf:
        tf.add(str(src), arcname=src.name)
    size = tar_path.stat().st_size
    t.add(f"压缩包 {human(size)}")

    t.set(step="推送到手机（12 GB 级，10 分钟左右）", progress=0.02)
    remote_tar = f"{dst}/{tar_path.name}"
    proc = subprocess.Popen([adb_exe, "-s", serial, "push", str(tar_path), remote_tar],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", creationflags=CREATE_NO_WINDOW)
    assert proc.stdout is not None
    for line in proc.stdout:
        m = re.search(r"(\d+)%", line)
        if m:
            t.set(progress=0.02 + 0.68 * int(m.group(1)) / 100)
        elif line.strip() and not line.strip().endswith("%"):
            t.add(line.strip()[-200:])
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("adb push 失败")

    t.set(step="手机上解压（小文件多，要等）", progress=0.72)
    cmd = f"cd {dst} && tar -xf {tar_path.name} && rm -f {tar_path.name} && echo UNPACK_OK"
    code, out = run([adb_exe, "-s", serial, "shell", cmd], timeout=7200)
    if "UNPACK_OK" not in out:
        raise RuntimeError("解压失败：" + out[-800:])
    t.add("解压完成")
    tar_path.unlink(missing_ok=True)
    t.set(progress=0.95)


def device_stat(adb_exe: str, serial: str, path: str) -> str:
    code, out = run([adb_exe, "-s", serial, "shell", f"du -sh {path} 2>/dev/null; find {path} -type f 2>/dev/null | wc -l"])
    return out.strip()


# ---------------------------------------------------------------- 主流程
def plan(cfg: dict) -> dict:
    br = BRANCHES[cfg.get("branch") or "touch"]
    return {
        "branch": br["key"], "branch_name": br["name"], "repo": br["repo"],
        "needs_build": br["needs_build"], "apk_hint": br["apk_hint"],
        "device_data": br["device_data"], "device_note": br["device_note"],
        "package": br["package"],
    }


def deploy_task(cfg: dict, t: Task) -> dict:
    """解包 → 校验 →（触屏）编译 → 推送。"""
    br = BRANCHES[cfg["branch"]]
    out = Path(cfg.get("out_dir") or (DATA / "out")) / br["key"]

    # 1) 游戏文件来源
    game = Path(cfg["game_dir"]) if cfg.get("game_dir") else None
    xci = Path(cfg["xci_path"]) if cfg.get("xci_path") else None
    if xci and xci.is_file():
        keys = Path(cfg["keys_path"]) if cfg.get("keys_path") else None
        if not keys or not keys.is_file():
            raise RuntimeError("解包 XCI 需要 prod.keys，请在设置里指定")
        t.set(step="解包 XCI", progress=0.01)
        info = unpack_xci(xci, keys, out, t)
        game = out
    elif game and (game / "exefs" / "main").is_file():
        ok, bid = verify_exefs(game / "exefs")
        if not ok:
            raise RuntimeError(bid)
        chs = count_chs(game / "romfs")
        info = {"exefs": str(game / "exefs"), "romfs": str(game / "romfs"),
                "files": count_files(game / "romfs"), "size": dir_size(game / "romfs"),
                "chs": chs, "build_id": bid}
        t.add(f"直接用现成目录：{game}（{info['files']} 文件，简中 {chs}）")
        t.set(step="校验通过", progress=0.82)
    else:
        raise RuntimeError("请指定含 exefs/romfs 的目录，或一个 XCI/NSP")
    t.result["game"] = info

    if info.get("chs", 0) == 0:
        t.add("提示：romfs 里没有 nativeNX/chS，这个包不含简体中文语言资源，游戏内只能选日/英等语言")

    # 2) 触屏分支：编译
    if br["needs_build"]:
        gu = ensure_gu_native(t)
        ndk = ensure_ndk(cfg, t)
        b = build_game(gu, Path(game), ndk, int(cfg.get("jobs") or 4), t)
        t.result["build"] = b
        t.add(f"libmhgugame.so {human(b['size'])}，id {b['gid']}")
    else:
        t.add("官方版 APK 自带游戏代码，跳过编译")

    # 3) APK
    t.set(step="检查 APK", progress=0.9)
    rel = latest_release(br["repo"])
    asset = pick_asset(rel, br["asset"])
    if not asset:
        raise RuntimeError(f"{br['repo']} 最新 release 里没有匹配 {br['asset']} 的包")
    apk = DL / asset["name"]
    if not apk.is_file() or apk.stat().st_size != asset["size"]:
        download(asset["browser_download_url"], apk, t, asset["size"])
    t.result["apk"] = {"name": asset["name"], "size": asset["size"],
                       "tag": rel.get("tag_name"), "path": str(apk)}
    t.add(f"APK {asset['name']}（{human(asset['size'])}，{rel.get('tag_name')}）")

    # 4) 推手机
    if cfg.get("adb_addr") or cfg.get("adb_wifi_ip"):
        adb_exe = find_adb(cfg)
        addr = cfg.get("adb_addr") or ""
        if not addr:
            port = find_port(cfg["adb_wifi_ip"], t)
            if not port:
                raise RuntimeError("没扫到无线调试端口，请在设置里直接填 IP:端口")
            addr = f"{cfg['adb_wifi_ip']}:{port}"
        serial = adb_connect_wifi(adb_exe, addr, t)
        t.set(step="安装 APK", progress=0.94)
        code, out = run([adb_exe, "-s", serial, "install", "-r", "-g", str(apk)], timeout=900)
        t.add(out.strip()[-600:])
        if code != 0:
            raise RuntimeError("APK 安装失败")
        t.set(step="推送游戏文件", progress=0.95)
        dst = br["device_data"]
        run([adb_exe, "-s", serial, "shell", f"mkdir -p {dst}"])
        # 触屏版要把 exefs 与 romfs 一起放在同一层
        push_tree(adb_exe, serial, Path(game) / "exefs", dst, t, "exefs")
        push_tree(adb_exe, serial, Path(game) / "romfs", dst, t, "romfs")
        t.result["device"] = {"serial": serial, "path": dst, "stat": device_stat(adb_exe, serial, dst)}
        t.add(f"手机端：{dst} → {t.result['device']['stat']}")
    else:
        t.add("没有配置手机地址，已跳过推送（文件都在本机）")

    t.set(step="全部完成", progress=1.0)
    return t.result
