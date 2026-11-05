#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无 Gradle 构建 MHGU 手机管家 APK。

依赖（本机已有，复用 D2R 工程那套）：
  JDK 21      F:\\Program\\REA\\engines\\java\\jdk-21.0.12.1+1
  build-tools I:\\D2-mobile\\android-build\\toolchain\\android-14
  platform-34 I:\\D2-mobile\\android-build\\toolchain\\plat\\android-34\\android.jar
"""
import glob
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
BUILD = os.path.join(ROOT, "build")
APKDIR = os.path.join(BUILD, "apk")
DIST = os.path.join(ROOT, "dist")

JDK = r"F:\Program\REA\engines\java\jdk-21.0.12.1+1\bin"
BT = r"I:\D2-mobile\android-build\toolchain\android-14"
ANDROID_JAR = r"I:\D2-mobile\android-build\toolchain\plat\android-34\android.jar"

JAVA = os.path.join(JDK, "java.exe")
JAVAC = os.path.join(JDK, "javac.exe")
KEYTOOL = os.path.join(JDK, "keytool.exe")
AAPT2 = os.path.join(BT, "aapt2.exe")
ZIPALIGN = os.path.join(BT, "zipalign.exe")
D8_JAR = os.path.join(BT, "lib", "d8.jar")
APKSIGNER = os.path.join(BT, "lib", "apksigner.jar")

KS = os.path.join(BUILD, "keystore", "mhgu.jks")
KS_PASS = os.path.join(BUILD, "keystore", "pass.txt")
ALIAS = "mhgu"
VERSION = "0.1.0"


def sh(cmd, cwd=None, allow_fail=False):
    print("  $", " ".join(str(c) for c in cmd)[:160])
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, errors="replace")
    if r.returncode != 0 and not allow_fail:
        print(r.stdout[-3000:])
        raise SystemExit("命令失败，退出码 %d" % r.returncode)
    return r.stdout


def clean():
    for d in (APKDIR, DIST):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    os.makedirs(APKDIR, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)


def prepare_assets():
    dst = os.path.join(APKDIR, "assets", "www")
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(os.path.join(APP, "www"), dst)
    print("  assets/www:", len(os.listdir(dst)), "个文件")
    shutil.copyfile(os.path.join(ROOT, "tools", "build_android.py"),
                    os.path.join(APKDIR, "assets", "build_android.py"))
    tcz = os.path.join(ROOT, "payload", "tc.zip")
    if os.path.isfile(tcz):
        shutil.copyfile(tcz, os.path.join(APKDIR, "assets", "tc.zip"))
        print("  assets/tc.zip: %.1f MB" % (os.path.getsize(tcz) / 1048576))
    else:
        print("  assets/tc.zip: 缺（内置工具链不完整，编译不可用）")


def ensure_keystore():
    if os.path.isfile(KS):
        return open(KS_PASS).read().strip()
    os.makedirs(os.path.dirname(KS), exist_ok=True)
    pw = "mhgu" + "".join(c for c in "20261105")
    sh([KEYTOOL, "-genkeypair", "-keystore", KS, "-alias", ALIAS, "-keyalg", "RSA",
        "-keysize", "2048", "-validity", "10950", "-storepass", pw, "-keypass", pw,
        "-dname", "CN=biigdrong, OU=mhgu-tool, O=biigdrong, L=CN, C=CN"])
    open(KS_PASS, "w").write(pw)
    print("  生成签名密钥:", KS)
    return pw


def main():
    for p in (JAVA, JAVAC, AAPT2, ZIPALIGN):
        if not os.path.isfile(p):
            raise SystemExit("缺少工具: " + p)
    if not os.path.isfile(ANDROID_JAR):
        raise SystemExit("缺少 android.jar: " + ANDROID_JAR)

    print("[1/7] 清理")
    clean()
    prepare_assets()
    pw = ensure_keystore()

    print("[2/7] aapt2 compile")
    res_zip = os.path.join(APKDIR, "res.zip")
    sh([AAPT2, "compile", "--dir", os.path.join(APP, "android", "res"), "-o", res_zip])

    print("[3/7] aapt2 link")
    base_apk = os.path.join(APKDIR, "base.apk")
    gen_dir = os.path.join(APKDIR, "gen")
    os.makedirs(gen_dir, exist_ok=True)
    sh([AAPT2, "link", "-o", base_apk,
        "-I", ANDROID_JAR,
        "--manifest", os.path.join(APP, "android", "AndroidManifest.xml"),
        res_zip,
        "-A", os.path.join(APKDIR, "assets"),
        "--java", gen_dir,
        "--min-sdk-version", "21", "--target-sdk-version", "34",
        "--version-code", "1", "--version-name", VERSION])

    print("[4/7] javac")
    classes = os.path.join(APKDIR, "classes")
    os.makedirs(classes, exist_ok=True)
    srcs = glob.glob(os.path.join(APP, "android", "src", "**", "*.java"), recursive=True)
    srcs += glob.glob(os.path.join(gen_dir, "**", "*.java"), recursive=True)
    sh([JAVAC, "-J-Duser.language=en", "-J-Duser.country=US", "-encoding", "UTF-8",
        "--release", "8", "-nowarn", "-cp", ANDROID_JAR, "-d", classes] + srcs)
    want = os.path.join(classes, "cn", "biigdrong", "mhgu", "MainActivity.class")
    if not os.path.isfile(want):
        raise SystemExit("javac 没有产出 MainActivity.class —— 编译失败但未报错，检查源码")

    print("[5/7] d8")
    dexdir = os.path.join(APKDIR, "dex")
    os.makedirs(dexdir, exist_ok=True)
    clses = glob.glob(os.path.join(classes, "**", "*.class"), recursive=True)
    sh([JAVA, "-cp", D8_JAR, "com.android.tools.r8.D8", "--min-api", "21", "--release",
        "--lib", ANDROID_JAR, "--output", dexdir] + clses)

    print("[6/7] 组装 zip")
    unsigned = os.path.join(APKDIR, "unsigned.apk")
    dex = os.path.join(dexdir, "classes.dex")
    if not os.path.isfile(dex):
        raise SystemExit("d8 没有产出 classes.dex")
    with zipfile.ZipFile(base_apk) as zin, zipfile.ZipFile(unsigned, "w") as zout:
        for item in zin.infolist():
            if item.filename == "classes.dex":
                continue
            ct = zipfile.ZIP_STORED if item.filename == "resources.arsc" else zipfile.ZIP_DEFLATED
            zout.writestr(item, zin.read(item.filename), compress_type=ct)
        zout.write(dex, "classes.dex", compress_type=zipfile.ZIP_DEFLATED)
        # 原生库：以 lib/arm64-v8a/libX.so 形式打包，安装后落到 nativeLibraryDir（可执行）
        jni = os.path.join(APP, "android", "jniLibs")
        if os.path.isdir(jni):
            abi_dirs = sorted(os.listdir(jni))
            for abi in abi_dirs:
                d = os.path.join(jni, abi)
                if not os.path.isdir(d):
                    continue
                for fn in sorted(os.listdir(d)):
                    p = os.path.join(d, fn)
                    if os.path.isfile(p):
                        zout.write(p, "lib/%s/%s" % (abi, fn), compress_type=zipfile.ZIP_STORED)
                        print("     + lib/%s/%s  %.2f MB" % (abi, fn, os.path.getsize(p) / 1048576))

    print("[7/7] 对齐 + 签名")
    aligned = os.path.join(APKDIR, "aligned.apk")
    sh([ZIPALIGN, "-f", "-p", "4", unsigned, aligned])
    out_apk = os.path.join(DIST, "MHGU-Manager-v%s.apk" % VERSION)
    sh([JAVA, "-jar", APKSIGNER, "sign", "--ks", KS, "--ks-key-alias", ALIAS,
        "--ks-pass", "pass:" + pw, "--key-pass", "pass:" + pw,
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true", "--out", out_apk, aligned])
    verify = sh([JAVA, "-jar", APKSIGNER, "verify", "--verbose", "--print-certs", out_apk],
                allow_fail=True)
    print(verify.strip()[:600])

    badging = sh([AAPT2, "dump", "badging", out_apk], allow_fail=True)
    for line in badging.splitlines():
        if line.startswith(("package:", "application-label", "launchable-activity", "sdkVersion", "targetSdkVersion")):
            print("  " + line.strip())

    print("\n完成：%s  %.2f MB" % (out_apk, os.path.getsize(out_apk) / 1048576))


if __name__ == "__main__":
    main()
