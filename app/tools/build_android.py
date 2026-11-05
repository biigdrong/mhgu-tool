#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 Android（Termux）上生成 libmhgugame.so —— gu-native 的手机端编译脚本。

上游 tools/build_game.py 只认 Windows / Linux(x86_64) 宿主，因为在 Android 上
platform.system() 返回 Linux，它会去找 prebuilt/linux-x86_64 的 clang，而那里面
全是 x86 ELF，一执行就 "cannot execute"。

本脚本做同一件事，只把工具链换成 Termux 自带的 clang / lld（aarch64 宿主，
target 默认就是 aarch64-linux-android）。其余全部照抄上游：
  · ID_FILES / ID_FLAGS 一致  → gamelib_id() 算出来的 id 与 app 期望的一致
  · CFLAGS 一致               → 生成的对象与上游同参
  · 链接参数一致（含 stub libmain.so、16 KB 页对齐、soname）

用法：
    python build_android.py <含 exefs 与 romfs 的目录>
可选：
    --gu PATH      gu-native 源码目录（默认：本脚本所在目录的上一级，或 ~/gu-native）
    --jobs N       并行编译数（默认 CPU 核数）
    --api N        target API level（默认 29）
"""
import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
BUILD_ID = bytes.fromhex("FB08F1D20FD1204F")  # 1.4.0 的 main

# —— 与上游 build_game.py 完全一致，改了就与 app 期望的 id 不符
ID_FILES = ['tools/gen.py', 'tools/lifter.py', 'tools/neon.py', 'tools/nso.py', 'tools/gamelib_entry.c',
            'runtime/hle/hle_symbols.txt', 'runtime/cpu.h', 'runtime/vfp.h', 'runtime/neon_rt.h',
            'runtime/gamelib.h']
ID_FLAGS = 'android-arm64-v8a -O2 -nossp -fno-strict-aliasing -ffp-contract=off -fno-math-errno'
CFLAGS = ['-DANDROID', '-fdata-sections', '-ffunction-sections', '-funwind-tables', '-no-canonical-prefixes',
          '-DNDEBUG', '-fPIC', '-O2', '-fno-strict-aliasing', '-ffp-contract=off', '-fno-math-errno',
          '-fvisibility=hidden', '-march=armv8.2-a', '-w', '-fno-stack-protector', '-U_FORTIFY_SOURCE',
          '-g0', '-fno-plt']


def say(s):
    print(s, flush=True)


def fail(s):
    say("\nERROR: " + s)
    sys.exit(1)


def find_root(explicit):
    cands = [explicit, os.path.dirname(TOOLS),
             os.path.expanduser("~/gu-native"),
             "/sdcard/MHGU/gu-native"]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "tools", "gen.py")):
            return os.path.abspath(c)
    fail("找不到 gu-native 源码。用 --gu 指定，或先 git clone 到 ~/gu-native")


def load_upstream(root):
    """动态加载上游 tools/build_game.py。

    为什么要这么做：gamelib_id() 的输入（文件清单 + ID_FLAGS）一旦上游改动，
    硬编码在本脚本里的副本就会算出不同的 id，app 会以版本不符拒绝加载。
    直接复用上游的常量与函数，上游怎么更新都不用改这里。
    """
    import importlib.util
    p = os.path.join(root, "tools", "build_game.py")
    if not os.path.isfile(p):
        say("提示：上游没有 tools/build_game.py，用内置默认参数")
        return None
    try:
        spec = importlib.util.spec_from_file_location("mhgu_upstream", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:  # noqa: BLE001
        say("警告：加载上游 build_game.py 失败（%s），改用内置默认参数" % e)
        return None


def gamelib_id(root):
    h = hashlib.sha256()
    for name in ID_FILES:
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            fail("源码不完整，缺 " + name)
        with open(p, "rb") as f:
            h.update(f.read().replace(b"\r", b""))
    h.update((ID_FLAGS + "\n").encode())
    return h.hexdigest()[:16]


def check_game(game):
    main = os.path.join(game, "exefs", "main")
    if not os.path.isfile(main):
        fail("这个目录里没有 exefs/main。请指向同时含 exefs 与 romfs 的文件夹。")
    if not os.path.isfile(os.path.join(game, "exefs", "sdk")):
        fail("缺 exefs/sdk。")
    with open(main, "rb") as f:
        head = f.read(0x48)
    if head[:4] != b"NSO0" or head[0x40:0x48] != BUILD_ID:
        fail("exefs 不是 1.4.0 版本（build ID 应为 %s）。" % BUILD_ID.hex().upper())
    if not os.path.isdir(os.path.join(game, "romfs", "nativeNX")):
        say("提示：缺 romfs/nativeNX —— 不影响编译，但游戏运行时需要它。")


def find_tool(name):
    for p in (os.path.join(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"), "bin", name),
              shutil.which(name)):
        if p and os.path.isfile(p):
            return p
    return None


def prune_cache(cache_dir, keep=1000 * 1024 * 1024, limit=1200 * 1024 * 1024):
    """缓存超过上限时按最旧优先删，避免无限增长"""
    try:
        entries = []
        total = 0
        for n in os.listdir(cache_dir):
            if not n.endswith(".o"):
                continue
            p = os.path.join(cache_dir, n)
            st = os.stat(p)
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= limit:
            return
        entries.sort()
        for _m, sz, p in entries:
            if total <= keep:
                break
            try:
                os.remove(p)
                total -= sz
            except OSError:
                pass
    except OSError:
        pass


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    return r.returncode, r.stdout or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("--gu")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--api", type=int, default=29)
    ap.add_argument("--force", action="store_true", help="已有同版本 so 也重建")
    ap.add_argument("--clang", help="内置工具链：clang 可执行文件路径")
    ap.add_argument("--lld", help="内置工具链：ld.lld 路径")
    ap.add_argument("--sysroot", help="内置工具链：sysroot 目录")
    ap.add_argument("--resource-dir", help="内置工具链：clang 资源目录")
    a = ap.parse_args()

    game = os.path.abspath(a.game)
    root = find_root(a.gu)
    say("gu-native: " + root)

    machine = platform.machine()
    say("宿主: %s / %s" % (platform.system(), machine))
    if machine not in ("aarch64", "arm64"):
        say("警告：宿主不是 aarch64，本脚本按 Android ARM64 目标编译，仍可继续。")

    clang = a.clang or find_tool("clang")
    if not clang or not os.path.isfile(clang):
        fail("找不到 clang。请用 --clang 指定，或在 Termux 里 pkg install clang")
    say("clang: " + clang)
    code, out = run([clang, "--version"])
    say(out.splitlines()[0] if out else "")

    try:
        import lz4.block  # noqa: F401
    except ImportError:
        if a.clang:
            fail("内置环境缺 lz4 模块")
        say("安装 lz4 模块…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "lz4"])

    check_game(game)

    up = load_upstream(root)
    cflags = CFLAGS
    if up is not None and hasattr(up, "gamelib_id"):
        try:
            gid = up.gamelib_id()
            cflags = list(getattr(up, "CFLAGS", CFLAGS))
            say("参数来源：上游 build_game.py（自适应，上游更新无需改本脚本）")
        except Exception as e:  # noqa: BLE001
            say("上游 gamelib_id() 失败（%s），退回内置算法" % e)
            gid = gamelib_id(root)
    else:
        gid = gamelib_id(root)
        say("参数来源：本脚本内置默认值")
    say("game code id: " + gid)

    dst_so = os.path.join(game, "libmhgugame.so")
    dst_id = os.path.join(game, "libmhgugame.id")
    if not a.force and os.path.isfile(dst_so) and os.path.isfile(dst_id):
        try:
            old = open(dst_id).read().strip()
        except OSError:
            old = ""
        if old == gid and os.path.getsize(dst_so) > 10 * 1024 * 1024:
            say("已有同版本的 so（id %s，%d MB），跳过编译。" %
                (gid, os.path.getsize(dst_so) >> 20))
            say("要强制重建请加 --force")
            return
        if old:
            say("已有 so 的 id 是 %s，与本版源码算出的 %s 不同，需要重建。" % (old, gid))

    t0 = time.time()
    work = tempfile.mkdtemp(prefix="mhgu_", dir=os.path.expanduser("~"))
    say("工作目录: " + work)
    import atexit
    atexit.register(shutil.rmtree, work, True)  # 失败或中断也清掉，避免几百 MB 累加

    # ---------- 1/3 翻译
    say("\n阶段 1/3：把 ARM 代码翻成 C（纯 Python；sdk 与 main 并行，省约 1 分钟）")
    gen = os.path.join(work, "gen")
    hle = os.path.join(root, "runtime", "hle", "hle_symbols.txt")
    steps = [
        [sys.executable, "gen.py", "sdk", os.path.join(game, "exefs", "sdk"), "0x02400000",
         os.path.join(gen, "sdk"), "--hle", hle],
        [sys.executable, "gen.py", "main", os.path.join(game, "exefs", "main"), "0x00200000",
         os.path.join(gen, "main")],
    ]

    def do_gen(cmd):
        code, out = run(cmd, cwd=os.path.join(root, "tools"))
        if code:
            raise RuntimeError("翻译 %s 失败：\n%s" % (cmd[2], out[-2000:]))
        say("  %s 完成" % cmd[2])
        return True

    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(do_gen, steps))
    except RuntimeError as e:
        fail(str(e))

    sources = []
    for sub in ("main", "sdk"):
        d = os.path.join(gen, sub)
        if not os.path.isdir(d):
            fail("gen.py 没有产出 " + sub + " 目录")
        sources += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".c")]
    entry = os.path.join(root, "tools", "gamelib_entry.c")
    if os.path.isfile(entry):
        sources.append(entry)
    say("共 %d 个 C 文件待编译" % len(sources))

    # ---------- 2/3 编译
    say("\n阶段 2/3：clang 编译（%d 并行，手机上这一步要几十分钟）" % a.jobs)
    objdir = os.path.join(work, "obj")
    os.makedirs(objdir)
    target = "--target=aarch64-linux-android%d" % a.api
    toolflags = []
    if a.sysroot:
        toolflags.append("--sysroot=" + a.sysroot)
    if a.resource_dir:
        toolflags.append("-resource-dir=" + a.resource_dir)
    if a.lld:
        toolflags.append("--ld-path=" + a.lld)
    done = [0]
    hits = [0]
    import threading
    lock = threading.Lock()

    # 对象缓存：文件内容 + 编译参数决定键（gid 只并进真正引用它的文件），
    # 上游只改少数文件时其余 .o 直接复用，不必全量重编。
    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "mhgu_obj")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        cache_dir = ""
    inc_runtime = "-I" + os.path.join(root, "runtime")
    base_flags = " ".join([target] + toolflags + list(cflags) + [inc_runtime])
    _c, _v = run([clang, "--version"])
    clang_ver = (_v.splitlines()[0] if _v else "") + "|" + str(a.api)

    def obj_key(data):
        h = hashlib.sha256()
        h.update(data)
        h.update(base_flags.encode("utf-8", "replace"))
        if b"GAMELIB_ID" in data:
            h.update(('-DGAMELIB_ID="%s"' % gid).encode())
        h.update(clang_ver.encode())
        return h.hexdigest()

    def note(n):
        if n % 20 == 0 or n == len(sources):
            say("  %d / %d（复用 %d）" % (n, len(sources), hits[0]))

    def build(item):
        i, src = item
        obj = os.path.join(objdir, "%d.o" % i)
        with open(src, "rb") as f:
            data = f.read()
        cached = ""
        if cache_dir:
            cached = os.path.join(cache_dir, obj_key(data) + ".o")
            if os.path.isfile(cached):
                try:
                    shutil.copyfile(cached, obj)
                    with lock:
                        done[0] += 1
                        hits[0] += 1
                        n = done[0]
                    note(n)
                    return obj
                except OSError:
                    pass
        cmd = [clang, target] + toolflags + list(cflags) + [inc_runtime,
                       '-DGAMELIB_ID="%s"' % gid, "-c", src, "-o", obj]
        code, out = run(cmd)
        if code:
            raise RuntimeError("%s://n%s" % (src, out[-1500:]))
        if cached:
            try:
                tmp = "%s.%d.tmp" % (cached, threading.get_ident())
                shutil.copyfile(obj, tmp)
                os.replace(tmp, cached)
            except OSError:
                pass
        with lock:
            done[0] += 1
            n = done[0]
        note(n)
        return obj

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(a.jobs) as pool:
        try:
            objs = list(pool.map(build, enumerate(sources)))
        except RuntimeError as e:
            fail("编译失败：%s" % e)

    # ---------- 3/3 链接
    say("\n阶段 3/3：链接")
    stub_c = os.path.join(work, "stub.c")
    open(stub_c, "w").close()
    stub = os.path.join(work, "libmain.so")
    code, out = run([clang, target] + toolflags + ["-shared", "-Wl,-soname,libmain.so", stub_c, "-o", stub])
    if code:
        fail("stub 链接失败：\n" + out[-1500:])

    rsp = os.path.join(work, "objs.rsp")
    with open(rsp, "w") as f:
        f.write("\n".join('"%s"' % o for o in objs))
    lib = os.path.join(work, "libmhgugame.so")
    code, out = run([clang, target] + toolflags + ["-shared", "-fPIC", "-Wl,-soname,libmhgugame.so",
                     "-Wl,--build-id=sha1", "-Wl,--no-rosegment", "-Wl,--gc-sections",
                     "-Wl,-z,max-page-size=16384", "-Wl,--allow-shlib-undefined",
                     "-o", lib, "@" + rsp, "-L" + work, "-lmain", "-latomic", "-lm"])
    if code:
        fail("链接失败：\n" + out[-2000:])

    strip = find_tool("llvm-strip")
    if strip:
        run([strip, "--strip-unneeded", lib])

    dst_so = os.path.join(game, "libmhgugame.so")
    dst_id = os.path.join(game, "libmhgugame.id")
    shutil.copyfile(lib, dst_so)
    with open(dst_id, "w") as f:
        f.write(gid)

    size = os.path.getsize(dst_so) >> 20
    say("\n完成，用时 %.0f 分钟" % ((time.time() - t0) / 60))
    say("  %s  (%d MB)" % (dst_so, size))
    say("  %s  (%s)" % (dst_id, gid))
    say("\n产物已写入游戏文件夹，打开触屏版即可。")
    if cache_dir:
        say("复用对象：%d / %d" % (hits[0], len(sources)))
        prune_cache(cache_dir)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
