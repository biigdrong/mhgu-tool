#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把从 Termux 拉来的工具链裁成可塞进 APK 的形态。

- 可执行文件（clang / lld / python）→ jniLibs/arm64-v8a/lib*.so（安装后在 nativeLibraryDir，可 execve）
- 其余纯数据与共享库 → payload/tc.zip（APK 的 assets，首次启动解到 filesDir/tc）
"""
import os
import shutil
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TC = os.path.join(BASE, "payload", "tc")
JNI = os.path.join(BASE, "app", "android", "jniLibs", "arm64-v8a")
OUT_ZIP = os.path.join(BASE, "payload", "tc.zip")

# clang 头文件里对我们无用的目标平台
DROP_HEADERS = [
    "arm_sve.h", "arm_sme.h", "arm_mve.h",
    "avx2intrin.h", "avx512fintrin.h", "avx512vlintrin.h", "avx512bwintrin.h",
    "avx512dqintrin.h", "avx512cdintrin.h", "avx512vbmiintrin.h",
    "opencl-c.h", "opencl-c-base.h", "altivec.h", "vecintrin.h",
    "hexagon_protos.h", "hvx_hexagon_protos.h", "msa.h", "nvptxintrin.h",
    "wasm_simd128.h", "riscv_vector.h", "bmiintrin.h", "bmi2intrin.h",
]

# python 标准库里 gen.py / build_android.py 用不到的大件
DROP_PY_DIRS = [
    "ensurepip", "idlelib", "test", "unittest", "pydoc_data", "asyncio",
    "xml", "turtledemo", "lib2to3", "sqlite3", "venv", "distutils",
    "site-packages/pip", "site-packages/pip-26.3.dist-info",
    "turtle.py", "turtledemo", "curses", "dbm", "shelve.py", "pydoc.py",
]


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, u)
        n /= 1024.0
    return "%.1f TB" % n


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    t = 0
    for dp, _, fn in os.walk(path):
        for f in fn:
            try:
                t += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return t


def rm(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def main():
    before = size_of(TC)

    # 1) clang 资源目录：只留 C 需要的头文件与 aarch64 builtins
    inc = os.path.join(TC, "lib", "clang", "21", "include")
    for name in os.listdir(inc):
        p = os.path.join(inc, name)
        if os.path.isdir(p):
            rm(p)          # llvm/ cuda_wrappers/ openmp/ ppc_wrappers/ …
    for name in DROP_HEADERS:
        rm(os.path.join(inc, name))

    liblinux = os.path.join(TC, "lib", "clang", "21", "lib", "linux")
    if os.path.isdir(liblinux):
        for name in os.listdir(liblinux):
            if name != "libclang_rt.builtins-aarch64-android.a":
                rm(os.path.join(liblinux, name))

    # 2) python 标准库瘦身
    py = os.path.join(TC, "lib", "python3.14")
    rm(os.path.join(py, "__pycache__"))
    for name in DROP_PY_DIRS:
        rm(os.path.join(py, name))
    # lib-dynload 里保留，但干掉明显无关的测试模块
    dyn = os.path.join(py, "lib-dynload")
    if os.path.isdir(dyn):
        for name in os.listdir(dyn):
            if "_test" in name:
                rm(os.path.join(dyn, name))
    # 递归清 __pycache__
    for dp, dn, _ in os.walk(py):
        if "__pycache__" in dn:
            rm(os.path.join(dp, "__pycache__"))

    after = size_of(TC)
    print("裁剪：%s → %s（省 %s）" % (human(before), human(after), human(before - after)))

    # 3) 可执行文件进 jniLibs
    os.makedirs(JNI, exist_ok=True)
    for src, dst in (("bin/clang-21", "libclang.so"),
                     ("bin/lld", "ld.lld"),
                     ("bin/python3.14", "libpython.so"),
                     ("bin/llvm-strip", "llvm-strip")):
        s = os.path.join(TC, src)
        d = os.path.join(JNI, dst)
        shutil.copyfile(s, d)
        print("jniLibs: %-14s %s" % (dst, human(os.path.getsize(d))))

    # 4) 其余打包成 assets/tc.zip
    #    解出来是：tc/lib/*.so、tc/lib/python3.14/…、tc/clang/21/…
    #    PYTHONHOME 指向 tc 根，CPython 自己会去 tc/lib/python3.14 找标准库
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
    entries = []
    libd = os.path.join(TC, "lib")
    for name in sorted(os.listdir(libd)):
        p = os.path.join(libd, name)
        if name == "clang":
            continue
        if os.path.isfile(p) and (name.endswith(".so") or ".so." in name):
            entries.append((p, "lib/" + name))
    for dp, _, fn in os.walk(os.path.join(libd, "clang", "21")):
        for f in fn:
            p = os.path.join(dp, f)
            entries.append((p, "clang/21/" + os.path.relpath(p, os.path.join(libd, "clang", "21")).replace(os.sep, "/")))
    for dp, _, fn in os.walk(py):
        for f in fn:
            p = os.path.join(dp, f)
            entries.append((p, "lib/python3.14/" + os.path.relpath(p, py).replace(os.sep, "/")))
    sr = os.path.join(TC, "sysroot")
    for dp, _, fn in os.walk(sr):
        for f in fn:
            p = os.path.join(dp, f)
            entries.append((p, "sysroot/" + os.path.relpath(p, sr).replace(os.sep, "/")))

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, arc in entries:
            z.write(src, arc)
    zs = os.path.getsize(OUT_ZIP)
    print("assets/tc.zip: %d 个文件，%s（原始 %s）" % (len(entries), human(zs), human(after)))
    print("APK 预计约 %s" % human(zs + sum(
        os.path.getsize(os.path.join(JNI, f)) for f in os.listdir(JNI))))


if __name__ == "__main__":
    main()
