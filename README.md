# MHGU 装机助手 · mhgu-tool

把「解包游戏包 → 校验 1.4.0 → 合并官方中文资源 → 生成游戏代码 → 装 APK → 推手机」这一长串命令行操作，
收进一个本地网页界面。双击 `启动.cmd` 就能用。

面向两条并行的安卓原生分支，**同一个工具都支持**：

| | 官方版 | 触屏版 |
|---|---|---|
| 上游 | [morerokk/MHGURecomp](https://github.com/morerokk/MHGURecomp) | [khorne-hub/gu-native](https://github.com/khorne-hub/gu-native) |
| 输入方式 | 手柄 / 虚拟手柄悬浮窗 | 屏幕触控（摇杆、拖屏转视角、锁定、弓弩专用布局） |
| APK 体积 | 约 136 MB | 约 7 MB |
| 游戏代码 | **APK 已内置** `libmhgugame.so`（113.7 MB） | **必须本机生成**，用 Android NDK 现编 |
| 部署路径 | `Android/data/io.github.morerokk.mhgurecomp/files/data` | `/sdcard/MHGU`（不能进 Android/data） |
| 首次准备 | 解包 + 推送，约 15 分钟 | 解包 + 下载 NDK 1.8 GB + 编译，约 25 分钟 |

两条支路的共同点：都要 **1.4.0 版本**的 `exefs` + `romfs`，且都要把 update 的增量 romfs 合并进基础 romfs。

## 为什么必须合并 update

MHGU 亚版 1.4.0 的更新包里有约 1000 个增量 romfs 文件，覆盖 **全部语言** 资源目录
（`chS / chT / eng / fre / ger / ita / jpn / spa`）。简体与繁体中文就是这里来的——是官方内置的多语言，
不是外挂汉化补丁。

只提取基础 romfs 而不合并更新，会丢掉中文以及 1.4.0 的全部文本修正。本工具默认完成这一步。

## 前置条件

- Windows 10/11，Python 3.10+
- 自备 **1.4.0 版**游戏文件：要么给一个 `XCI`/`NSP`（外加 `prod.keys`，用内置 hactool 解），
  要么给一个已经含 `exefs/` 与 `romfs/` 的目录
- 手机：Android 9+、arm64、Vulkan 1.1+（触屏版要求 1.2）
- 触屏版额外需要：约 1.8 GB 的 Android NDK（工具自动下载）、约 3 GB 临时磁盘、10 GB 以上可用空间

游戏文件来源不合法的话这个工具帮不了你，它只读你本机已有的文件。

## 启动

双击 **`启动.cmd`**。窗口会打印地址并自动打开浏览器到 `http://127.0.0.1:8777/`。

```
python app.py                 # 或手动启动
python app.py --port 9000     # 指定端口
python app.py --no-browser    # 不自动开浏览器
```

只监听 `127.0.0.1`，不上传任何东西。关闭窗口即退出。

## 用法

1. **选分支**：官方版 / 触屏版。按钮和后续步骤会自动适配。
2. **游戏文件**：填 `XCI/NSP` 路径（自动解包），或直接指向已有 `exefs` + `romfs` 的目录（跳过解包）。
   点「自动探测」会在 `I:\NS`、各盘根目录里找游戏包、`prod.keys` 与 `adb`。
3. **手机**（可选）：填手机 IP，点「连接 / 扫描」。无线调试端口每次会变，工具会扫 30000–50000 找；
   也可以直接填 `IP:端口`。
4. 点 **一键部署**。

流程中的四个耗时点，界面上都有进度与日志：

- 解密 NCA：约 14 GB 落盘
- 提取基础 romfs：12 GB / 35000 余文件
- 编译游戏代码（仅触屏版）：NDK 下载 1.8 GB + 编译 6 分钟起
- 推送到手机：打包成 tar 后单文件推送，比逐文件快一个档，手机上再解压

## 校验

工具在解包后会读 `exefs/main` 的头部，要求 `NSO0` 魔数且 build ID 为
`FB08F1D20FD1204F`（正是 1.4.0）。不符就停下，不会让你白等 20 分钟才发现版本错了。

## 注意事项

- **装好后不要卸载 app**。卸载会删掉 `files` 目录，12.3 GB 游戏文件与存档一起消失。升级直接覆盖安装。
- 触屏版的游戏文件夹必须放在公共目录（如 `/sdcard/MHGU`），Android 11+ 的隐私沙箱不允许别的应用读
  `Android/data` 下的内容，所以它要你自己在 app 里再选一次文件夹。
- 官方版的公网联机 hub 只和安卓玩家互通；要和 PC 朋友联机，双方在设置里填同一个 matchmaking key。

## 目录

```
mhgu-tool/
  app.py            HTTP 服务与 API（只依赖标准库）
  core.py           解包 / 校验 / 编译 / 推送 / 下载
  tools/hactool.exe XCI、NCA、RomFS 解析（SciresM/hactool 1.4.0）
  ui/index.html     单页界面
  启动.cmd          Windows 启动器（自动找 Python）
  app/              手机端管家 APK 的源码（可选，独立于 PC 工具）
  data/             配置、缓存、下载、工作目录（运行时生成）
```

## 手机端管家（app/）

`app/` 里是一个 40 KB 的 Android WebView 壳，装到手机上用来**看两个分支的版本与中文更新说明、
一键下载 APK、体检游戏文件夹是否齐**。它不含游戏数据，也不玩游戏。

构建同样不需要 Gradle，见 [`app/README.md`](app/README.md)。那里还记了一个绕不开的坑：
**build-tools r34 的 d8 处理非静态内部类会崩**，所有嵌套类必须写成 `static`。

## 合法性与致谢

本工具**不含**任何游戏文件、密钥或游戏代码，也不提供获取途径。它只处理你本机已有的、
你自己合法拥有的游戏副本，产出的 `libmhgugame.so` 也仅存在于你本机。

- [morerokk/MHGURecomp](https://github.com/morerokk/MHGURecomp) — MIT，官方安卓版与重编译运行时
- [khorne-hub/gu-native](https://github.com/khorne-hub/gu-native) — MIT，触屏分支与其 `build_game.py`
- [SciresM/hactool](https://github.com/SciresM/hactool) — Switch 容器与文件系统解析
- Monster Hunter 是 Capcom 的商标；Nintendo Switch 是任天堂的商标。本项目与二者无关联。
