# mhgu-app —— MHGU 手机管家（Android）

一个 40 KB 的 WebView 壳，把两件事收到一部手机上：**看两个分支的版本与中文更新说明**、**下载对应 APK**。
它自己不玩游戏，也不含任何游戏数据。

## 它能做什么

| 功能 | 说明 |
|---|---|
| 双分支面板 | 官方版（MHGURecomp）与触屏版（gu-native），各自显示最新版本、APK 大小、已装版本 |
| 中文更新说明 | 每个版本的要点用中文写死在内置数据里，英文原文可展开对照 |
| 下载 APK | 走系统下载管理器，通知栏可见，可断点；下载完直接点装 |
| 环境体检 | 读设备型号 / Android 版本 / ABI / 剩余空间，看 `/sdcard/MHGU` 里 exefs、romfs、so 是否齐 |
| 唤起 Termux | 触屏版的 so 需要本机生成，可一键把脚本交给 Termux 跑 |

## 构建

无需 Gradle / Android Studio，只用到 JDK + build-tools + platform：

```bash
python tools/make_icons.py     # 生成各密度图标（纯标准库）
python tools/build_apk.py      # aapt2 → javac → d8 → zipalign → apksigner
```

产物在 `dist/MHGU-Manager-v0.1.0.apk`。

工具链路径写在 `tools/build_apk.py` 顶部，按自己的机器改：
`JDK`、`BT`（build-tools r34）、`ANDROID_JAR`（platform-34）。

首跑会自动生成签名密钥到 `build/keystore/`，**这个目录不要提交**，丢了就没法覆盖升级。

## 一个必须知道的坑：d8 与非静态内部类

build-tools r34 自带的 **d8（R8 8.2.2）处理非静态内部类会崩**：

```
Error in MainActivity$Bridge.class:
java.lang.NullPointerException: Cannot invoke "String.length()" because "<parameter1>" is null
```

用最小样例验证过：静态嵌套类正常，非静态内部类（带 `this$0`）必崩。匿名类也一样。

所以本工程所有嵌套类都是 `static`，需要外部能力就显式传 `Activity`。
改动时**不要**图省事写 `private class Foo` 或 `new Runnable(){...}`。

## 前端

`app/www` 是纯静态页面，`assets/www` 首次启动同步到 `filesDir/www`（跳过 `_` 开头目录），
之后从可写目录加载，方便后续做增量更新。

Java 侧通过 `App` 对象暴露能力，JS 侧用 `window.__onRelease` / `window.__onDownload` 接收回调。
注意 JS 桥方法跑在 JavaBridge 线程，网络请求都在 `Thread` 里做完再 `evaluateJavascript` 回推。

## 关于「手机本地编译 so」

界面里提供了入口，但**这条路没有在真机上跑通过**，原因写在页面提示里：

- `gu-native` 的 `build_game.py` 把主机平台写死成 `windows-x86_64` / `linux-x86_64`，
  Android 上拿不到对应的 NDK 主机工具链；
- 手机本地只能走 Termux 的 clang + Python 手动拼编译命令，官方参数见 `build_game.py` 的
  `CFLAGS` 与链接段；
- 实测成本：PC 上 6 分钟，手机通常 40–70 分钟，峰值约需 3 GB 空间。

所以页面把它标成「实验性」，并建议有电脑的人用 [mhgu-tool](../README.md) 编一次再把 so 拷过来。
