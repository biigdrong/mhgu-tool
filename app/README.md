# mhgu-app —— MHGU 手机管家（Android）

一个自包含的 WebView 应用：**下载两个分支的 APK**、**看中文更新说明**、**用手机自带的
内置工具链现场编译触屏版所需的 `libmhgugame.so`**。不需要 Termux、不需要电脑。

本应用不含任何游戏文件、密钥或游戏代码；编译必须使用用户自己的 1.4.0 `exefs`。

## 功能

| 功能 | 说明 |
|---|---|
| 双分支面板 | 官方版（MHGURecomp）与触屏版（gu-native），显示最新版本、APK 大小、已装版本 |
| 下载源 | 直连 + ghfast.top / gh-proxy.com / ghproxy.net / gh.llkk.cc，并发测速取最快，可手动锁定 |
| 下载 APK | 走系统下载管理器，通知栏可见进度；URL 自动套用所选源前缀 |
| 源码联网更新 | gu-native `main.zip` 按需拉取，带 `If-None-Match`，未变化时 304 跳过 |
| 内置编译 | clang 21.1.8 + lld + Python 3.14 全部打包在 APK 内，在自家进程里跑 |
| 对象缓存 | 384 个 `.o` 按「文件内容 + 参数」做键缓存，同版本重编 334s → 61s |

## 体积与结构

APK 约 **76 MB**，首次启动把工具链解到 app 私有目录（2860 个文件，约 1.3 秒）。

```
lib/arm64-v8a/
  libclang.so      118 KB   clang 驱动
  ld.lld           5.6 MB   链接器（名字必须是 ld.lld，它按 argv[0] 选驱动模式）
  libpython.so     4.5 KB   CPython 解释器
  llvm-strip       205 KB   llvm-objcopy 的软链名，用于 strip 产物
assets/
  tc.zip            72 MB   libLLVM / libclang-cpp / libc++_shared / libffi / libxml2 /
                            libiconv / libz / libzstd + clang 头与 builtins + Python 标准库
                            + sysroot（crt*.o、libunwind.a、libatomic.a、C 头文件）
  build_android.py        编译脚本，每次启动覆盖写入私有目录
  www/                    界面
```

**为什么可执行文件要放在 `lib/arm64-v8a/`**：Android 10 起禁止从 app 数据目录
`execve`（W^X），`chmod 755` 也无效；而 `nativeLibraryDir` 允许执行。实测
`filesDir` 里的 ELF 报 `error=13, Permission denied`，同一文件放进 APK 的
`lib/arm64-v8a/` 后可以正常跑起来。

## 构建

```bash
python tools/pack_toolchain.py    # payload/tc 裁剪 → jniLibs + assets/tc.zip
python tools/build_apk.py         # aapt2 → javac → d8 → zipalign → apksigner
```

无 Gradle、无 Android Studio。依赖 JDK 21、build-tools r34（aapt2 / zipalign / d8 / apksigner）。

## 两个必须知道的坑

**1. d8（R8 8.2.2）遇到某些内部类会抛 NPE**
`NullPointerException: Cannot invoke "String.length()" because "<parameter1>" is null`。
触发条件是**非静态内部类、匿名类**，以及**实现了 `Comparator` 的静态嵌套类**（桥接方法
`compare(Object,Object)` 同样会崩）。规避方式：所有嵌套类写成 `static`，不要用匿名类，
排序自己手写，不要实现 `Comparator`。

**2. `string.h` 找不到**
clang 的 `-resource-dir` 只提供编译器自带头（`stdint.h`、`arm_neon.h` 等），
C 标准库头属于 sysroot。必须把 `${PREFIX}/include` 里的 C 头一起带上，并用
`--sysroot=` 指向它；`libc.so` / `libm.so` / `libdl.so` 直接用系统的 `/system/lib64`。

## 编译流程

```
python build_android.py <游戏目录>
  --clang <nativeLibraryDir>/libclang.so
  --lld   <nativeLibraryDir>/ld.lld
  --sysroot <filesDir>/tc/sysroot
  --resource-dir <filesDir>/tc/clang/21
  --gu <filesDir>/src/gu-native
```

1. `gen.py` 把 `exefs/sdk` 与 `exefs/main` 的 ARM 代码翻成 C（两个进程并行）
2. clang 编译 384 个 C 文件（按核数并行，命中缓存的对象直接复制）
3. lld 链接 → `llvm-strip --strip-unneeded` → 写入游戏目录

实测（SD 8 Gen 5 / 8 核 / Android 17）：首次 334 秒，命中缓存 61 秒，产物
113,404,080 字节，`game code id = 3bfb26af6be34249`，与 PC 端逐字节一致。
