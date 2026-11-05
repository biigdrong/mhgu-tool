package cn.biigdrong.mhgu;

import android.content.Context;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * 内置编译流水线：不再依赖 Termux。
 *
 * 源码走网络（GitHub main.zip），工具链在 APK 内，进程就是本应用自己，
 * 进度与日志直接来自子进程 stdout，不需要状态文件、不需要跨应用授权。
 */
public final class Builder {

    public static final String SRC_URL =
            "https://github.com/khorne-hub/gu-native/archive/refs/heads/main.zip";

    private static volatile String stage = "未开始";
    private static volatile int percent = 0;
    private static volatile String status = "idle";
    private static volatile boolean running = false;
    private static volatile String lastError = "";

    private static final StringBuilder LOG = new StringBuilder();
    private static java.io.PrintWriter FILE_LOG;
    private static final Object LOCK = new Object();

    private Builder() {
    }

    // ---------------------------------------------------------------- 状态

    public static boolean running() {
        return running;
    }

    public static String stateJson() {
        return "{\"stage\":" + MainActivity.quote(stage) + ",\"percent\":" + percent
                + ",\"status\":" + MainActivity.quote(status) + "}";
    }

    public static String logText() {
        synchronized (LOCK) {
            return LOG.toString();
        }
    }

    static void set(String s, int p, String st) {
        stage = s;
        percent = p;
        status = st;
    }

    static void log(String line) {
        synchronized (LOCK) {
            LOG.append(line).append('\n');
            if (LOG.length() > 120000) LOG.delete(0, LOG.length() - 100000);
            if (FILE_LOG != null) {
                FILE_LOG.println(line);
                FILE_LOG.flush();
            }
        }
    }

    /** 日志同时落盘一份，方便出问题后取回来 */
    static void openFileLog(Context c) {
        try {
            File d = c.getExternalFilesDir(null);
            if (d != null) {
                if (!d.exists()) d.mkdirs();
                FILE_LOG = new java.io.PrintWriter(new java.io.File(d, "build.log"), "UTF-8");
            }
        } catch (Throwable ignored) {
        }
    }

    // ---------------------------------------------------------------- 目录

    public static File srcRoot(Context c) {
        return new File(new File(c.getFilesDir(), "src"), "gu-native");
    }

    private static File srcZip(Context c) {
        return new File(new File(c.getFilesDir(), "src"), "gu.zip");
    }

    private static File script(Context c) {
        return new File(c.getFilesDir(), "build_android.py");
    }

    /** 源码版本标记：下载时间 */
    public static String srcStamp(Context c) {
        File f = new File(new File(c.getFilesDir(), "src"), ".stamp");
        if (!f.isFile()) return "";
        try {
            long t = Long.parseLong(readSmall(f).trim());
            return new SimpleDateFormat("MM-dd HH:mm", Locale.CHINA).format(new Date(t));
        } catch (Exception e) {
            return "";
        }
    }

    public static boolean srcReady(Context c) {
        return new File(srcRoot(c), "tools/gen.py").isFile();
    }

    // ---------------------------------------------------------------- 源码

    /** 拉取 gu-native 最新源码；force=false 且已存在时跳过 */
    static boolean ensureSource(Context c, boolean force, boolean report) throws IOException {
        File root = srcRoot(c);
        if (!force && new File(root, "tools/gen.py").isFile()) return true;

        File dir = new File(c.getFilesDir(), "src");
        if (!dir.exists() && !dir.mkdirs()) throw new IOException("建不了目录 " + dir);
        File zip = srcZip(c);
        File etagF = new File(dir, ".etag");
        String oldTag = etagF.isFile() ? readSmall(etagF).trim() : "";
        if (report) set("检查源码更新", 3, "running");
        String[] newTag = new String[1];
        int code = httpDownload(Mirrors.apply(SRC_URL, Mirrors.resolve(c)), zip, report, oldTag, newTag);
        if (code == 304) {
            log("源码无变化（304），跳过下载与解压");
            return true;
        }
        if (newTag[0] != null && !newTag[0].isEmpty()) writeSmall(etagF, newTag[0]);

        if (report) set("解压源码", 6, "running");
        File tmp = new File(dir, "unzip");
        deleteTree(tmp);
        if (!tmp.mkdirs()) throw new IOException("建不了 " + tmp);
        long files = unzipStrip(zip, tmp, "gu-native-main/");
        File inner = new File(tmp, "gu-native-main");
        if (inner.isDirectory()) {
            deleteTree(root);
            inner.renameTo(root);
        } else if (tmp.isDirectory()) {
            deleteTree(root);
            tmp.renameTo(root);
        }
        zip.delete();
        deleteTree(tmp);
        if (!new File(root, "tools/gen.py").isFile()) {
            throw new IOException("源码结构异常，解出 " + files + " 个文件但找不到 tools/gen.py");
        }
        writeSmall(new File(dir, ".stamp"), String.valueOf(System.currentTimeMillis()));
        log("源码已更新到 " + root + "（" + files + " 个文件）");
        return true;
    }

    /** 把 assets 里的编译脚本释放到 filesDir */
    static File ensureScript(Context c) throws IOException {
        File f = script(c);
        // 始终用 APK 内自带的版本覆盖，避免升级后仍跑旧脚本
        InputStream in = c.getAssets().open("build_android.py");
        OutputStream out = new FileOutputStream(f);
        byte[] buf = new byte[65536];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        out.close();
        in.close();
        return f;
    }

    // ---------------------------------------------------------------- 编译

    static void start(Context c, String gameDir) {
        if (running) {
            log("已有编译在跑，忽略重复请求");
            return;
        }
        new Thread(new BuildJob(c.getApplicationContext(), gameDir)).start();
    }

    static class BuildJob implements Runnable {
        private final Context ctx;
        private final String game;

        BuildJob(Context c, String game) {
            this.ctx = c;
            this.game = game;
        }

        public void run() {
            running = true;
            synchronized (LOCK) {
                LOG.setLength(0);
            }
            openFileLog(ctx);
            lastError = "";
            long t0 = System.currentTimeMillis();
            try {
                set("展开工具链", 1, "running");
                StringBuilder elog = new StringBuilder();
                if (!Toolchain.ensure(ctx, elog)) {
                    throw new IOException("工具链展开失败：" + elog);
                }
                log("工具链就绪 " + elog.toString().trim());

                ensureScript(ctx);
                ensureSource(ctx, false, true);

                List<String> cmd = new ArrayList<String>();
                cmd.add(Toolchain.python(ctx).getAbsolutePath());
                cmd.add(script(ctx).getAbsolutePath());
                cmd.add(game);
                cmd.add("--clang");
                cmd.add(Toolchain.clang(ctx).getAbsolutePath());
                cmd.add("--lld");
                cmd.add(Toolchain.lld(ctx).getAbsolutePath());
                cmd.add("--sysroot");
                cmd.add(Toolchain.sysroot(ctx).getAbsolutePath());
                cmd.add("--resource-dir");
                cmd.add(Toolchain.resDir(ctx).getAbsolutePath());
                cmd.add("--gu");
                cmd.add(srcRoot(ctx).getAbsolutePath());
                cmd.add("--jobs");
                cmd.add(String.valueOf(Math.max(2, Runtime.getRuntime().availableProcessors())));

                set("启动编译", 7, "running");
                ProcessBuilder pb = new ProcessBuilder(cmd);
                pb.redirectErrorStream(true);
                pb.directory(ctx.getFilesDir());
                Toolchain.applyEnv(pb, ctx);
                Process p = pb.start();
                java.io.BufferedReader r =
                        new java.io.BufferedReader(new java.io.InputStreamReader(p.getInputStream()));
                String line;
                while ((line = r.readLine()) != null) {
                    log(line);
                    track(line);
                }
                int code = p.waitFor();
                if (code != 0) throw new IOException("编译进程退出码 " + code);
                File so = new File(game, "libmhgugame.so");
                if (!so.isFile()) throw new IOException("没有产出 libmhgugame.so");
                set("完成", 100, "done");
                log("用时 " + ((System.currentTimeMillis() - t0) / 1000) + " 秒");
            } catch (Throwable t) {
                lastError = String.valueOf(t.getMessage());
                set("失败", 100, "failed");
                log("失败：" + t);
            } finally {
                running = false;
            }
        }

        private void track(String line) {
            if (line.contains("阶段 1/3")) {
                set("翻译 ARM → C", 8, "running");
            } else if (line.contains("阶段 2/3")) {
                set("编译 C", 30, "running");
            } else if (line.contains("阶段 3/3")) {
                set("链接", 93, "running");
            } else if (line.contains("已有同版本")) {
                set("已是最新", 100, "done");
            } else if (line.contains("完成，用时")) {
                set("完成", 100, "done");
            } else {
                int i = line.indexOf(" / ");
                if (i > 0) {
                    String n = line.substring(0, i).trim();
                    try {
                        int done = Integer.parseInt(n);
                        int total = Integer.parseInt(line.substring(i + 3).trim());
                        if (total > 0) set("编译 C", 30 + done * 60 / total, "running");
                    } catch (NumberFormatException ignored) {
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- 工具

    static void httpDownload(String url, File dest, boolean report) throws IOException {
        httpDownload(url, dest, report, null, null);
    }

    /** 带 If-None-Match 的下载；返回 HTTP 码（304 表示未变化，不写文件） */
    static int httpDownload(String url, File dest, boolean report,
                            String ifNoneMatch, String[] outEtag) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(20000);
        conn.setReadTimeout(30000);
        conn.setInstanceFollowRedirects(true);
        conn.setRequestProperty("User-Agent", "mhgu-manager");
        if (ifNoneMatch != null && !ifNoneMatch.isEmpty()) {
            conn.setRequestProperty("If-None-Match", ifNoneMatch);
        }
        int code = conn.getResponseCode();
        if (outEtag != null) outEtag[0] = conn.getHeaderField("ETag");
        if (code == 304) return 304;
        if (code != 200) throw new IOException("HTTP " + code + " " + url);
        long total = conn.getContentLength();
        InputStream in = new BufferedInputStream(conn.getInputStream(), 1 << 16);
        OutputStream out = new FileOutputStream(dest);
        byte[] buf = new byte[1 << 16];
        long got = 0;
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            got += n;
            if (report && total > 0) {
                int pct = 3 + (int) (got * 5 / total);
                set("下载源码 " + (got * 100 / total) + "%", pct, "running");
            }
        }
        out.close();
        in.close();
        conn.disconnect();
        return code;
    }

    static long unzipStrip(File zip, File destDir, String strip) throws IOException {
        ZipInputStream z = new ZipInputStream(new BufferedInputStream(new java.io.FileInputStream(zip), 1 << 16));
        byte[] buf = new byte[1 << 16];
        ZipEntry e;
        long files = 0;
        while ((e = z.getNextEntry()) != null) {
            String name = e.getName();
            if (strip != null && name.startsWith(strip)) name = name.substring(strip.length());
            if (name.isEmpty() || e.isDirectory()) {
                if (!name.isEmpty()) new File(destDir, name).mkdirs();
                z.closeEntry();
                continue;
            }
            File f = new File(destDir, name);
            File parent = f.getParentFile();
            if (parent != null && !parent.exists()) parent.mkdirs();
            FileOutputStream o = new FileOutputStream(f);
            int n;
            while ((n = z.read(buf)) > 0) o.write(buf, 0, n);
            o.close();
            z.closeEntry();
            files++;
        }
        z.close();
        return files;
    }

    static void deleteTree(File f) {
        if (f == null || !f.exists()) return;
        File[] kids = f.listFiles();
        if (kids != null) {
            for (File k : kids) deleteTree(k);
        }
        f.delete();
    }

    private static String readSmall(File f) {
        try {
            byte[] b = new byte[(int) Math.min(256, f.length())];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = in.read(b);
            in.close();
            return new String(b, 0, Math.max(0, n), "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    private static void writeSmall(File f, String s) {
        try {
            File p = f.getParentFile();
            if (p != null && !p.exists()) p.mkdirs();
            FileOutputStream o = new FileOutputStream(f);
            o.write(s.getBytes("UTF-8"));
            o.close();
        } catch (Exception ignored) {
        }
    }
}
