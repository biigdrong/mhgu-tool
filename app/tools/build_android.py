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
        fail("缺 romfs/nativeNX。游戏资源没解全。")


def find_tool(name):
    for p in (os.path.join(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"), "bin", name),
              shutil.which(name)):
        if p and os.path.isfile(p):
            return p
    return None


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
    a = ap.parse_args()

    game = os.path.abspath(a.game)
    root = find_root(a.gu)
    say("gu-native: " + root)

    machine = platform.machine()
    say("宿主: %s / %s" % (platform.system(), machine))
    if machine not in ("aarch64", "arm64"):
        say("警告：宿主不是 aarch64，本脚本按 Android ARM64 目标编译，仍可继续。")

    clang = find_tool("clang")
    if not clang:
        fail("找不到 clang。在 Termux 里先执行：pkg install -y clang python lz4")
    say("clang: " + clang)
    code, out = run([clang, "--version"])
    say(out.splitlines()[0] if out else "")

    try:
        import lz4.block  # noqa: F401
    except ImportError:
        say("安装 lz4 模块…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "lz4"])

    check_game(game)
    gid = gamelib_id(root)
    say("game code id: " + gid)

    t0 = time.time()
    work = tempfile.mkdtemp(prefix="mhgu_", dir=os.path.expanduser("~"))
    say("工作目录: " + work)

    # ---------- 1/3 翻译
    say("\n阶段 1/3：把 ARM 代码翻成 C（这一步最慢，纯 Python）")
    gen = os.path.join(work, "gen")
    hle = os.path.join(root, "runtime", "hle", "hle_symbols.txt")
    steps = [
        [sys.executable, "gen.py", "sdk", os.path.join(game, "exefs", "sdk"), "0x02400000",
         os.path.join(gen, "sdk"), "--hle", hle],
        [sys.executable, "gen.py", "main", os.path.join(game, "exefs", "main"), "0x00200000",
         os.path.join(gen, "main")],
    ]
    for cmd in steps:
        code, out = run(cmd, cwd=os.path.join(root, "tools"))
        if code:
            fail("翻译失败：\n" + out[-2000:])
        say("  " + os.path.basename(cmd[2] if cmd[1] == "gen.py" else cmd[0]) + " 完成")

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
    done = [0]

    def build(item):
        i, src = item
        obj = os.path.join(objdir, "%d.o" % i)
        cmd = [clang, target] + CFLAGS + ["-I" + os.path.join(root, "runtime"),
                                          '-DGAMELIB_ID="%s"' % gid, "-c", src, "-o", obj]
        code, out = run(cmd)
        if code:
            raise RuntimeError("%s:\n%s" % (src, out[-1500:]))
        done[0] += 1
        if done[0] % 20 == 0 or done[0] == len(sources):
            say("  %d / %d" % (done[0], len(sources)))
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
    code, out = run([clang, target, "-shared", "-Wl,-soname,libmain.so", stub_c, "-o", stub])
    if code:
        fail("stub 链接失败：\n" + out[-1500:])

    rsp = os.path.join(work, "objs.rsp")
    with open(rsp, "w") as f:
        f.write("\n".join('"%s"' % o for o in objs))
    lib = os.path.join(work, "libmhgugame.so")
    code, out = run([clang, target, "-shared", "-fPIC", "-Wl,-soname,libmhgugame.so",
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
    say("\n现在打开 MHGU（触屏版），它应该在 /sdcard/MHGU 里找到这两个文件。")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
