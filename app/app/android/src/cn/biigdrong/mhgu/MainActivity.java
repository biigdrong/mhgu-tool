package cn.biigdrong.mhgu;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.StatFs;
import android.content.SharedPreferences;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * MHGU 手机管家。
 *
 * 界面全部在 assets/www，Java 只提供能力（下载、状态查询、唤起 Termux）。
 *
 * ★ 重要约束：所有嵌套类必须是 static。
 *   build-tools r34 自带的 d8（R8 8.2.2）处理**非静态**内部类会抛内部错误：
 *   `NullPointerException: Cannot invoke "String.length()" because "<parameter1>" is null`
 *   静态嵌套类不生成 this$0，可绕开。已用最小样例验证。
 */
public class MainActivity extends Activity {

    public static final String PKG_OFFICIAL = "io.github.morerokk.mhgurecomp";
    public static final String PKG_TOUCH = "com.mhgu.recomp";
    public static final String REPO_OFFICIAL = "morerokk/MHGURecomp";
    public static final String REPO_TOUCH = "khorne-hub/gu-native";
    public static final String GAME_DIR = "/sdcard/111/MHGU";

    private static final int REQ_TREE = 1001;
    private static final int REQ_ALLFILES = 1002;
    private static final int REQ_RUNCMD = 1003;
    /** Termux 的自定义危险权限，不在 android.Manifest.permission 里 */
    static final String PERM_RUN_COMMAND = "com.termux.permission.RUN_COMMAND";
    private String pendingDir;

    private WebView web;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences sp;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        sp = getSharedPreferences("mhgu", MODE_PRIVATE);
        web = new WebView(this);
        web.setBackgroundColor(0xFF0F1115);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setSupportZoom(false);
        s.setTextZoom(100);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebView.setWebContentsDebuggingEnabled(true);
        web.setWebViewClient(new MyClient(this));
        web.addJavascriptInterface(new Bridge(this), "App");
        setContentView(web, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        syncAssets();
        web.loadUrl("file://" + wwwDir().getAbsolutePath() + "/index.html");
    }

    private File wwwDir() {
        return new File(getFilesDir(), "www");
    }

    /** assets/www → filesDir/www；跳过 _ 开头的目录（留给编译中间产物） */
    private void syncAssets() {
        File dst = wwwDir();
        if (!dst.exists() && !dst.mkdirs()) return;
        try {
            String[] names = getAssets().list("www");
            if (names == null) return;
            for (String n : names) {
                if (n.startsWith("_")) continue;
                copyAsset("www/" + n, new File(dst, n));
            }
        } catch (IOException ignored) {
        }
    }

    private void copyAsset(String assetPath, File out) throws IOException {
        String[] children;
        try {
            children = getAssets().list(assetPath);
        } catch (IOException e) {
            children = null;
        }
        if (children != null && children.length > 0) {
            if (!out.exists() && !out.mkdirs()) return;
            for (String c : children) {
                copyAsset(assetPath + "/" + c, new File(out, c));
            }
            return;
        }
        File parent = out.getParentFile();
        if (parent != null && !parent.exists()) parent.mkdirs();
        InputStream in = null;
        FileOutputStream fos = null;
        try {
            in = getAssets().open(assetPath);
            fos = new FileOutputStream(out);
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) fos.write(buf, 0, n);
        } finally {
            try { if (fos != null) fos.close(); } catch (IOException ignored) {}
            try { if (in != null) in.close(); } catch (IOException ignored) {}
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    // ================================================================ static 嵌套类

    private static class MyClient extends WebViewClient {
        private final MainActivity act;

        MyClient(MainActivity a) {
            this.act = a;
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView v, String url) {
            return act.routeUrl(url);
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
            return act.routeUrl(req == null ? null : req.getUrl().toString());
        }
    }

    private static class JsCall implements Runnable {
        private final WebView web;
        private final String fn;
        private final String json;

        JsCall(WebView web, String fn, String json) {
            this.web = web;
            this.fn = fn;
            this.json = json;
        }

        public void run() {
            if (web == null) return;
            String js = "try{window." + fn + "&&window." + fn + "(" + json + ")}catch(e){}";
            web.evaluateJavascript(js, null);
        }
    }

    private static class FetchJob implements Runnable {
        private final MainActivity act;
        private final String repo;
        private final String branch;

        FetchJob(MainActivity a, String repo, String branch) {
            this.act = a;
            this.repo = repo;
            this.branch = branch;
        }

        public void run() {
            act.doFetch(repo, branch);
        }
    }

    private static class DownloadJob implements Runnable {
        private final MainActivity act;
        private final String url;
        private final String name;

        DownloadJob(MainActivity a, String url, String name) {
            this.act = a;
            this.url = url;
            this.name = name;
        }

        public void run() {
            act.doDownload(url, name);
        }
    }

    private static class ToastJob implements Runnable {
        private final MainActivity act;
        private final String msg;

        ToastJob(MainActivity a, String msg) {
            this.act = a;
            this.msg = msg;
        }

        public void run() {
            Toast.makeText(act, msg, Toast.LENGTH_SHORT).show();
        }
    }

    private static class ClipJob implements Runnable {
        private final MainActivity act;
        private final String text;

        ClipJob(MainActivity a, String text) {
            this.act = a;
            this.text = text;
        }

        public void run() {
            ClipboardManager cm = (ClipboardManager) act.getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null) {
                cm.setPrimaryClip(ClipData.newPlainText("mhgu", text));
                Toast.makeText(act, "已复制", Toast.LENGTH_SHORT).show();
            }
        }
    }

    // ================================================================ 选游戏文件夹

    private boolean hasAllFiles() {
        return Build.VERSION.SDK_INT < 30 || Environment.isExternalStorageManager();
    }

    /** Termux 的 RunCommandService 受 dangerous 权限保护，必须先拿到授权才能下发命令 */
    boolean hasRunCmd() {
        try {
            return checkSelfPermission(PERM_RUN_COMMAND) == 0;
        } catch (Exception e) {
            return false;
        }
    }

    @Override
    public void onRequestPermissionsResult(int req, String[] perms, int[] res) {
        if (req == REQ_RUNCMD) {
            if (res.length > 0 && res[0] == 0) {
                toastNow("已授权，继续编译");
                if (pendingDir != null) {
                    final String d = pendingDir;
                    pendingDir = null;
                    new Thread(new PrepJob(this, d)).start();
                }
            } else {
                toastNow("没有授权就无法把脚本交给 Termux");
            }
            return;
        }
        super.onRequestPermissionsResult(req, perms, res);
    }

    private void askAllFiles() {
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                Intent i = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + getPackageName()));
                startActivityForResult(i, REQ_ALLFILES);
                return;
            } catch (Exception ignored) {
            }
        }
        toastNow("这个系统版本无法授予目录权限");
    }

    private void openTreePicker() {
        try {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            startActivityForResult(i, REQ_TREE);
        } catch (Exception e) {
            toastNow("打不开文件夹选择器：" + e.getMessage());
        }
    }

    private void toastNow(String m) {
        ui.post(new ToastJob(this, m));
    }

    /** tree URI → 真实路径（"primary:111" → /storage/emulated/0/111） */
    private String treeToPath(Uri tree) {
        try {
            String docId = DocumentsContract.getTreeDocumentId(tree);
            String[] p = docId.split(":", 2);
            if (p.length < 2) return null;
            String vol = p[0];
            String rel = p[1];
            if ("primary".equalsIgnoreCase(vol)) {
                return Environment.getExternalStorageDirectory().getAbsolutePath()
                        + (rel.isEmpty() ? "" : "/" + rel);
            }
            return "/storage/" + vol + (rel.isEmpty() ? "" : "/" + rel);
        } catch (Exception e) {
            return null;
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == REQ_ALLFILES) {
            if (hasAllFiles()) openTreePicker();
            else toastNow("没有授予「所有文件访问权限」，读不了游戏文件夹");
            return;
        }
        if (req == REQ_TREE) {
            if (res != RESULT_OK || data == null || data.getData() == null) return;
            Uri tree = data.getData();
            try {
                getContentResolver().takePersistableUriPermission(tree,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (Exception ignored) {
            }
            String path = treeToPath(tree);
            if (path == null) {
                toastNow("解析不出真实路径，换一个文件夹再试");
                return;
            }
            sp.edit().putString("gameDir", path).apply();
            call("__onGameDir", "{\"ok\":true,\"path\":" + quote(path) + "}");
            return;
        }
        super.onActivityResult(req, res, data);
    }

    private void runTermuxFile(String path) {
        Intent i = new Intent();
        i.setClassName("com.termux", "com.termux.app.RunCommandService");
        i.setAction("com.termux.RUN_COMMAND");
        i.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash");
        i.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{path});
        i.putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home");
        i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
        startService(i);
    }

    private static String readAll(File f) {
        try {
            byte[] b = new byte[(int) Math.min(262144, f.length())];
            FileInputStream in = new FileInputStream(f);
            int n = in.read(b);
            in.close();
            return new String(b, 0, Math.max(0, n), "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    /** 生成 run.sh：源码与脚本由 App 下载解压好，Termux 只负责编译 */
    static String buildScript(String gameDir) {
        StringBuilder b = new StringBuilder();
        b.append("#!/data/data/com.termux/files/usr/bin/bash\n");
        b.append("G=\"").append(gameDir).append("\"\n");
        b.append("B=\"$G/_build\"\n");
        b.append("GU=\"$B/src/gu-native\"\n");
        b.append("mkdir -p \"$B\"\n");
        b.append("export PREFIX=/data/data/com.termux/files/usr\n");
        b.append("export PATH=$PREFIX/bin:/system/bin\n");
        b.append("export HOME=/data/data/com.termux/files/home\n");
        b.append("export TMPDIR=$PREFIX/tmp\n");
        b.append("mkdir -p \"$TMPDIR\"; chmod 700 \"$TMPDIR\" 2>/dev/null\n");
        b.append("st() { printf '{\"stage\":\"%s\",\"percent\":%s,\"status\":\"%s\"}' \"$1\" \"$2\" \"$3\" > \"$B/status.tmp\" && mv -f \"$B/status.tmp\" \"$B/status.json\"; }\n");
        b.append("st \"安装依赖\" 20 running\n");
        b.append("pkg install -y python clang lz4 >\"$B/pkg.log\" 2>&1\n");
        b.append("termux-wake-lock 2>/dev/null\n");
        b.append("st \"编译中\" 25 running\n");
        b.append("cd \"$GU\" || { st \"源码缺失\" 0 failed; exit 1; }\n");
        b.append("python tools/build_android.py \"$G\" 2>&1 | tee -a \"$B/build.log\" | while IFS= read -r ln; do\n");
        b.append("  case \"$ln\" in\n");
        b.append("    *\"阶段 1/3\"*) st \"翻译 ARM 到 C\" 30 running ;;\n");
        b.append("    *\"阶段 2/3\"*) st \"编译 C\" 45 running ;;\n");
        b.append("    *\"阶段 3/3\"*) st \"链接\" 93 running ;;\n");
        b.append("    *\"已有同版本\"*) st \"已是最新版本\" 100 done ;;\n");
        b.append("    *\"完成，用时\"*) st \"完成\" 100 done ;;\n");
        b.append("  esac\n");
        b.append("  case \"$ln\" in\n");
        b.append("    *\"/ \"*) n=$(printf '%s' \"$ln\" | tr -d ' ' | cut -d/ -f1); case \"$n\" in ''|*[!0-9]*) ;; *) st \"编译 C\" $((45 + n * 45 / 384)) running ;; esac ;;\n");
        b.append("  esac\n");
        b.append("done\n");
        b.append("termux-wake-unlock 2>/dev/null\n");
        b.append("if [ -f \"$G/libmhgugame.so\" ]; then st \"完成\" 100 done; else st \"失败，看日志\" 100 failed; fi\n");
        return b.toString();
    }

    /** 写一个最小 status.json，供 App 轮询 */
    private void setStage(File buildDir, String stage, int pct, String status) {
        try {
            String js = "{\"stage\":" + quote(stage) + ",\"percent\":" + pct
                    + ",\"status\":" + quote(status) + "}";
            File tmp = new File(buildDir, "status.tmp");
            FileOutputStream fos = new FileOutputStream(tmp);
            fos.write(js.getBytes("UTF-8"));
            fos.close();
            tmp.renameTo(new File(buildDir, "status.json"));
        } catch (Exception ignored) {
        }
    }

    /** 用系统 HttpURLConnection 下载，不依赖 Termux 的 curl */
    private void httpDownload(String url, File dest) throws IOException {
        File parent = dest.getParentFile();
        if (parent != null && !parent.exists()) parent.mkdirs();
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(20000);
        c.setReadTimeout(60000);
        c.setInstanceFollowRedirects(true);
        c.setRequestProperty("User-Agent", "mhgu-app/0.1.0");
        try {
            int code = c.getResponseCode();
            if (code >= 400) throw new IOException("HTTP " + code);
            InputStream in = c.getInputStream();
            FileOutputStream out = new FileOutputStream(dest);
            byte[] buf = new byte[128 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.close();
            in.close();
        } finally {
            c.disconnect();
        }
    }

    /** 用系统 java.util.zip 解压，不依赖 Termux 的 unzip */
    private void unzipTo(File zip, File destDir) throws IOException {
        java.util.zip.ZipFile zf = new java.util.zip.ZipFile(zip);
        try {
            java.util.Enumeration<? extends java.util.zip.ZipEntry> en = zf.entries();
            while (en.hasMoreElements()) {
                java.util.zip.ZipEntry e = en.nextElement();
                File out = new File(destDir, e.getName());
                if (e.isDirectory()) {
                    out.mkdirs();
                    continue;
                }
                File p = out.getParentFile();
                if (p != null && !p.exists()) p.mkdirs();
                InputStream in = zf.getInputStream(e);
                FileOutputStream fos = new FileOutputStream(out);
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) fos.write(buf, 0, n);
                fos.close();
                in.close();
            }
        } finally {
            zf.close();
        }
    }

    private static void writeText(File f, String s) throws IOException {
        File p = f.getParentFile();
        if (p != null && !p.exists()) p.mkdirs();
        FileOutputStream fos = new FileOutputStream(f);
        fos.write(s.getBytes("UTF-8"));
        fos.close();
    }

    /** 下载源码与脚本并解压，然后交给 Termux */
    void doPrepare(String dir) {
        File bd = new File(dir, "_build");
        File src = new File(bd, "src");
        File gu = new File(src, "gu-native");
        try {
            if (!new File(gu, "tools/gen.py").isFile()) {
                setStage(bd, "下载源码", 4, "running");
                src.mkdirs();
                File zip = new File(bd, "gu.zip");
                httpDownload("https://github.com/khorne-hub/gu-native/archive/refs/heads/main.zip", zip);
                setStage(bd, "解压源码", 12, "running");
                unzipTo(zip, src);
                File inner = new File(src, "gu-native-main");
                if (inner.isDirectory()) {
                    if (gu.exists()) deleteTree(gu);
                    inner.renameTo(gu);
                }
                zip.delete();
            }
            setStage(bd, "准备编译脚本", 16, "running");
            File py = new File(gu, "tools/build_android.py");
            httpDownload("https://raw.githubusercontent.com/biigdrong/mhgu-tool/main/app/tools/build_android.py", py);
            File sh = new File(bd, "run.sh");
            writeText(sh, buildScript(dir));
            setStage(bd, "启动 Termux 编译", 18, "running");
            runTermuxFile(sh.getAbsolutePath());
        } catch (Exception e) {
            setStage(bd, "准备失败：" + e.getMessage(), 0, "failed");
        }
    }

    private static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) {
            for (File k : kids) deleteTree(k);
        }
        f.delete();
    }

    private static class PrepJob implements Runnable {
        private final MainActivity act;
        private final String dir;

        PrepJob(MainActivity a, String d) {
            this.act = a;
            this.dir = d;
        }

        public void run() {
            act.doPrepare(dir);
        }
    }

    // ================================================================ 能力

    private boolean routeUrl(String url) {
        if (url == null) return false;
        if (url.startsWith("http://") || url.startsWith("https://")) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (Exception ignored) {
            }
            return true;
        }
        return false;
    }

    private void call(String fn, String json) {
        ui.post(new JsCall(web, fn, json));
    }

    private void doFetch(String repo, String branch) {
        String url = "https://api.github.com/repos/" + repo + "/releases?per_page=8";
        try {
            String body = httpGet(url);
            JSONArray arr = new JSONArray(body);
            JSONObject first = arr.getJSONObject(0);
            JSONObject out = new JSONObject();
            out.put("ok", true);
            out.put("tag", first.optString("tag_name"));
            out.put("name", first.optString("name"));
            out.put("published", first.optString("published_at"));
            out.put("notes", first.optString("body"));
            out.put("prerelease", first.optBoolean("prerelease"));
            JSONArray assets = new JSONArray();
            JSONArray raw = first.optJSONArray("assets");
            if (raw != null) {
                for (int i = 0; i < raw.length(); i++) {
                    JSONObject a = raw.getJSONObject(i);
                    String nm = a.optString("name");
                    if (!nm.endsWith(".apk")) continue;
                    JSONObject x = new JSONObject();
                    x.put("name", nm);
                    x.put("size", a.optLong("size"));
                    x.put("url", a.optString("browser_download_url"));
                    assets.put(x);
                }
            }
            out.put("assets", assets);
            JSONArray hist = new JSONArray();
            for (int i = 1; i < arr.length() && i < 6; i++) {
                JSONObject h = arr.getJSONObject(i);
                JSONObject x = new JSONObject();
                x.put("tag", h.optString("tag_name"));
                x.put("published", h.optString("published_at"));
                hist.put(x);
            }
            out.put("history", hist);
            call("__onRelease", quote(branch) + "," + out);
        } catch (Exception e) {
            call("__onRelease", quote(branch) + ",{\"ok\":false,\"error\":" + quote(e.toString()) + "}");
        }
    }

    private void doDownload(String url, String name) {
        try {
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.setTitle(name);
            r.setDescription("MHGU 手机管家正在下载");
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            r.setMimeType("application/vnd.android.package-archive");
            long id = dm.enqueue(r);
            call("__onDownload", "{\"ok\":true,\"id\":" + id + ",\"name\":" + quote(name) + "}");
        } catch (Exception e) {
            call("__onDownload", "{\"ok\":false,\"error\":" + quote(e.toString()) + "}");
        }
    }

    private String httpGet(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setRequestProperty("User-Agent", "mhgu-app/0.1.0");
        c.setRequestProperty("Accept", "application/vnd.github+json");
        try {
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            if (in == null) throw new IOException("HTTP " + code);
            BufferedReader r = new BufferedReader(new InputStreamReader(in, "UTF-8"));
            StringBuilder b = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) b.append(line).append('\n');
            r.close();
            return b.toString();
        } finally {
            c.disconnect();
        }
    }

    static String quote(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"') b.append("\\\"");
            else if (c == '\\') b.append("\\\\");
            else if (c == '\n') b.append("\\n");
            else if (c == '\r') b.append("\\r");
            else if (c == '\t') b.append("\\t");
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }

    private JSONObject pkgState(String pkg) {
        JSONObject o = new JSONObject();
        try {
            PackageManager pm = getPackageManager();
            PackageInfo pi = pm.getPackageInfo(pkg, 0);
            o.put("installed", true);
            o.put("ver", pi.versionName == null ? "?" : pi.versionName);
            o.put("code", Build.VERSION.SDK_INT >= 28 ? pi.getLongVersionCode() : pi.versionCode);
        } catch (Exception e) {
            try {
                o.put("installed", false);
            } catch (Exception ignored) {
            }
        }
        return o;
    }

    private static String readSmall(File f) {
        try {
            byte[] b = new byte[(int) Math.min(64, f.length())];
            FileInputStream in = new FileInputStream(f);
            int n = in.read(b);
            in.close();
            return new String(b, 0, Math.max(0, n)).trim();
        } catch (Exception e) {
            return "";
        }
    }

    // ================================================================ JS 桥

    public static class Bridge {
        private final MainActivity act;

        Bridge(MainActivity a) {
            this.act = a;
        }

        @JavascriptInterface
        public String deviceInfo() {
            JSONObject o = new JSONObject();
            try {
                o.put("sdk", Build.VERSION.SDK_INT);
                o.put("release", Build.VERSION.RELEASE);
                o.put("model", Build.MODEL);
                o.put("brand", Build.BRAND);
                JSONArray abis = new JSONArray();
                for (String a : Build.SUPPORTED_ABIS) abis.put(a);
                o.put("abis", abis);
                try {
                    StatFs st = new StatFs(Environment.getExternalStorageDirectory().getPath());
                    long free = st.getAvailableBytes();
                    o.put("freeGB", Math.round(free / 1073741824.0 * 10) / 10.0);
                } catch (Exception ignored) {
                }
            } catch (Exception ignored) {
            }
            return o.toString();
        }

        @JavascriptInterface
        public String installed() {
            JSONObject all = new JSONObject();
            try {
                all.put("official", act.pkgState(PKG_OFFICIAL));
                all.put("touch", act.pkgState(PKG_TOUCH));
            } catch (Exception ignored) {
            }
            return all.toString();
        }

        @JavascriptInterface
        public void fetchRelease(String branch) {
            String repo = "touch".equals(branch) ? REPO_TOUCH : REPO_OFFICIAL;
            new Thread(new FetchJob(act, repo, branch)).start();
        }

        @JavascriptInterface
        public void downloadApk(String url, String name) {
            new Thread(new DownloadJob(act, Mirrors.apply(url, Mirrors.resolve(act)), name)).start();
        }

        @JavascriptInterface
        public String mirrorInfo() {
            String sel = act.sp.getString("mirror", "auto");
            String best = act.sp.getString("mirrorBest", "");
            String json = Mirrors.listJson(sel);
            return json.substring(0, json.length() - 1) + ",\"best\":" + quote(best) + "}";
        }

        @JavascriptInterface
        public void setMirror(String id) {
            act.sp.edit().putString("mirror", id).apply();
        }

        @JavascriptInterface
        public void testMirrors() {
            new Thread(new SpeedJob(act)).start();
        }

        @JavascriptInterface
        public void openApp(String branch) {
            String pkg = "touch".equals(branch) ? PKG_TOUCH : PKG_OFFICIAL;
            try {
                Intent i = act.getPackageManager().getLaunchIntentForPackage(pkg);
                if (i == null) {
                    toast("还没装这个版本");
                    return;
                }
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                act.startActivity(i);
            } catch (Exception e) {
                toast("启动失败：" + e.getMessage());
            }
        }

        @JavascriptInterface
        public void openUrl(String url) {
            try {
                act.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (Exception e) {
                toast("打不开浏览器");
            }
        }

        @JavascriptInterface
        public void openDownloads() {
            try {
                Intent i = new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                act.startActivity(i);
            } catch (Exception e) {
                toast("请手动打开文件管理器");
            }
        }

        @JavascriptInterface
        public String gameDirState() {
            JSONObject o = new JSONObject();
            try {
                boolean chosen = act.sp.contains("gameDir");
                String dir = chosen ? act.sp.getString("gameDir", GAME_DIR) : GAME_DIR;
                File d = new File(dir);
                o.put("path", dir);
                o.put("chosen", chosen);
                o.put("allFiles", act.hasAllFiles());
                o.put("exists", d.isDirectory());
                o.put("exefs", new File(d, "exefs/main").isFile());
                o.put("romfs", new File(d, "romfs/nativeNX").isDirectory());
                File so = new File(d, "libmhgugame.so");
                o.put("so", so.isFile());
                o.put("soMB", so.isFile() ? Math.round(so.length() / 1048576.0 * 10) / 10.0 : 0);
                File idf = new File(d, "libmhgugame.id");
                o.put("soId", idf.isFile() ? readSmall(idf) : "");
            } catch (Exception ignored) {
            }
            return o.toString();
        }

        @JavascriptInterface
        public void pickGameDir() {
            if (!act.hasAllFiles()) {
                act.askAllFiles();
                return;
            }
            act.openTreePicker();
        }

        /** 内置工具链在本进程内编译，进度与日志直接来自子进程输出 */
        @JavascriptInterface
        public void buildSo() {
            try {
                String dir = act.sp.getString("gameDir", GAME_DIR);
                File g = new File(dir);
                if (!new File(g, "exefs/main").isFile()) {
                    toast("这个文件夹没有 exefs/main，先选对目录");
                    return;
                }
                if (Builder.running()) {
                    toast("已经在编译了");
                    return;
                }
                Builder.start(act, dir);
                toast("开始编译");
            } catch (Exception e) {
                toast("启动失败：" + e.getMessage());
            }
        }

        @JavascriptInterface
        public String buildStatus() {
            try {
                String dir = act.sp.getString("gameDir", GAME_DIR);
                File so = new File(dir, "libmhgugame.so");
                return "{\"ok\":true,\"dir\":" + quote(dir)
                        + ",\"soMB\":" + (so.isFile() ? so.length() / 1048576 : 0)
                        + ",\"state\":" + Builder.stateJson() + "}";
            } catch (Exception e) {
                return "{\"ok\":false,\"error\":" + quote(e.toString()) + "}";
            }
        }

        @JavascriptInterface
        public String buildLog() {
            String s = Builder.logText();
            return s.length() > 12000 ? s.substring(s.length() - 12000) : s;
        }

        /** 编译引擎与源码状态 */
        @JavascriptInterface
        public String engineInfo() {
            JSONObject o = new JSONObject();
            try {
                o.put("toolchain", Toolchain.ready(act));
                o.put("src", Builder.srcReady(act));
                o.put("srcDate", Builder.srcStamp(act));
                o.put("running", Builder.running());
            } catch (Exception ignored) {
            }
            return o.toString();
        }

        /** 强制重新拉取 gu-native 源码 */
        @JavascriptInterface
        public void updateSource() {
            new Thread(new SourceJob(act)).start();
            toast("正在拉取最新源码");
        }

        @JavascriptInterface
        public boolean hasTermux() {
            try {
                act.getPackageManager().getPackageInfo("com.termux", 0);
                return true;
            } catch (Exception e) {
                return false;
            }
        }

        @JavascriptInterface
        public void runInTermux(String script) {
            if (!hasTermux()) {
                toast("没装 Termux，先看下面的安装说明");
                return;
            }
            try {
                Intent i = new Intent();
                i.setClassName("com.termux", "com.termux.app.RunCommandService");
                i.setAction("com.termux.RUN_COMMAND");
                i.putExtra("com.termux.RUN_COMMAND_PATH",
                        "/data/data/com.termux/files/usr/bin/bash");
                i.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", script});
                i.putExtra("com.termux.RUN_COMMAND_WORKDIR",
                        "/data/data/com.termux/files/home");
                i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", false);
                act.startService(i);
                toast("已交给 Termux 执行，切过去看进度");
            } catch (Exception e) {
                toast("Termux 拒绝执行：检查 allow-external-apps=true");
            }
        }

        @JavascriptInterface
        public void toast(String msg) {
            act.ui.post(new ToastJob(act, msg));
        }

        @JavascriptInterface
        public void copyToClipboard(String text) {
            act.ui.post(new ClipJob(act, text));
        }
    }

    // ================================================================ exec 探测
    //
    // 目的：确认「把可执行文件当 lib*.so 打进 APK」后，本进程能否直接 execve。
    // 这决定成品能否自带 clang / python，不再依赖 Termux。
    //   Android 10 起 App 数据目录被 W^X 拦住（filesDir 里的 ELF 不能执行），
    //   而 nativeLibraryDir（/data/app/…/lib/arm64）通常允许执行。
    // 结果写到 getExternalFilesDir()/exec_probe.txt，adb 可直接读。

    static class ProbeJob implements Runnable {
        private final Activity act;

        ProbeJob(Activity a) {
            this.act = a;
        }

        /** 只用 arm_neon.h 里的东西，验证 gen.py 产出的代码能否被内置 clang 编译 */
        private static final String NEON_SRC =
                "#include <arm_neon.h>\n"
              + "float32x4_t f(float32x4_t a, float32x4_t b) {\n"
              + "    float32x4_t m = vmulq_f32(a, b);\n"
              + "    return vaddq_f32(m, vdupq_n_f32(1.0f));\n"
              + "}\n"
              + "int g(const signed char *p) {\n"
              + "    int8x16_t v = vld1q_s8(p);\n"
              + "    return vaddvq_s8(v);\n"
              + "}\n";

        public void run() {
            StringBuilder sb = new StringBuilder();
            File outDir = act.getExternalFilesDir(null);
            if (outDir != null && !outDir.exists()) outDir.mkdirs();
            try {
                sb.append("sdk=").append(android.os.Build.VERSION.SDK_INT).append('\n');
                sb.append("nativeLibraryDir=").append(Toolchain.nativeDir(act)).append('\n');

                StringBuilder elog = new StringBuilder();
                long t0 = System.currentTimeMillis();
                boolean ok = Toolchain.ensure(act, elog);
                sb.append("ensure=").append(ok).append(' ').append(elog);
                sb.append("elapsed=").append(System.currentTimeMillis() - t0).append(" ms\n");

                sb.append("\n=== clang --version\n");
                java.util.ArrayList<String> v = new java.util.ArrayList<String>();
                v.add(Toolchain.clang(act).getAbsolutePath());
                v.add("--version");
                sb.append(run(v));

                sb.append("\n=== python --version\n");
                java.util.ArrayList<String> pv = new java.util.ArrayList<String>();
                pv.add(Toolchain.python(act).getAbsolutePath());
                pv.add("--version");
                sb.append(run(pv));

                sb.append("\n=== python 跑一段脚本\n");
                java.util.ArrayList<String> ps = new java.util.ArrayList<String>();
                ps.add(Toolchain.python(act).getAbsolutePath());
                ps.add("-c");
                ps.add("import sys, argparse, json, hashlib, struct, shutil; print('PY_OK', sys.version.split()[0], hashlib.sha256(b'x').hexdigest()[:8])");
                sb.append(run(ps));

                sb.append("\n=== clang 编译并链接 NEON 测试\n");
                File c = new File(act.getCacheDir(), "neon_test.c");
                writeText(c, NEON_SRC);
                File so = new File(act.getCacheDir(), "neon_test.so");
                so.delete();
                java.util.ArrayList<String> cc = new java.util.ArrayList<String>();
                cc.add(Toolchain.clang(act).getAbsolutePath());
                cc.add("--target=aarch64-linux-android29");
                cc.add("-O1");
                cc.add("-shared");
                cc.add("-fPIC");
                cc.add("-resource-dir=" + Toolchain.resDir(act).getAbsolutePath());
                cc.add("--sysroot=" + Toolchain.sysroot(act).getAbsolutePath());
                cc.add("--ld-path=" + Toolchain.lld(act).getAbsolutePath());
                cc.add("-o");
                cc.add(so.getAbsolutePath());
                cc.add(c.getAbsolutePath());
                sb.append(run(cc));
                sb.append("so_exists=").append(so.isFile()).append(" size=").append(so.length()).append('\n');
                if (so.isFile()) {
                    byte[] h = new byte[20];
                    FileInputStream fi = new FileInputStream(so);
                    int n = fi.read(h);
                    fi.close();
                    sb.append("elf_magic=").append(h[0] == 0x7f && h[1] == 'E').append(" machine=0x")
                      .append(Integer.toHexString(h[18] & 0xff)).append(Integer.toHexString(h[19] & 0xff)).append('\n');
                    so.delete();
                }
                c.delete();
            } catch (Throwable t) {
                sb.append("EXCEPTION ").append(t).append('\n');
            }
            try {
                if (outDir != null) {
                    FileOutputStream fos = new FileOutputStream(new File(outDir, "exec_probe.txt"));
                    fos.write(sb.toString().getBytes("UTF-8"));
                    fos.close();
                }
            } catch (Throwable ignored) {
            }
        }

        private String run(java.util.ArrayList<String> cmd) {
            StringBuilder sb = new StringBuilder();
            try {
                ProcessBuilder pb = new ProcessBuilder(cmd);
                pb.redirectErrorStream(true);
                Toolchain.applyEnv(pb, act);
                pb.directory(act.getCacheDir());
                Process p = pb.start();
                BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
                String line;
                while ((line = r.readLine()) != null) sb.append("  ").append(line).append('\n');
                sb.append("  exit=").append(p.waitFor()).append('\n');
            } catch (Throwable t) {
                sb.append("  EXEC_FAIL ").append(t).append('\n');
            }
            return sb.toString();
        }

        private static void writeText(File f, String s) {
            try {
                FileOutputStream o = new FileOutputStream(f);
                o.write(s.getBytes("UTF-8"));
                o.close();
            } catch (Throwable ignored) {
            }
        }
    }

    /** 后台拉取源码，完成后回调界面刷新 */
    static class SourceJob implements Runnable {
        private final MainActivity act;

        SourceJob(MainActivity a) {
            this.act = a;
        }

        public void run() {
            String err = "";
            try {
                Builder.ensureSource(act.getApplicationContext(), true, false);
            } catch (Throwable t) {
                err = String.valueOf(t.getMessage());
            }
            final String e = err;
            act.ui.post(new Done(act, e));
        }

        static class Done implements Runnable {
            private final MainActivity act;
            private final String err;

            Done(MainActivity a, String e) {
                this.act = a;
                this.err = e;
            }

            public void run() {
                act.call("__onSource", "{\"ok\":" + (err.isEmpty() ? "true" : "false")
                        + ",\"error\":" + MainActivity.quote(err) + "}");
            }
        }
    }

    /** 并发测速所有下载源，取最快的存下来 */
    static class SpeedJob implements Runnable {
        private final MainActivity act;

        SpeedJob(MainActivity a) {
            this.act = a;
        }

        public void run() {
            java.util.List<String[]> r = Mirrors.speedTest();
            StringBuilder sb = new StringBuilder("[");
            String best = "";
            long bestMs = Long.MAX_VALUE;
            for (int i = 0; i < r.size(); i++) {
                String[] m = r.get(i);
                long ms = Long.parseLong(m[1]);
                if (ms >= 0 && ms < bestMs) {
                    bestMs = ms;
                    best = m[0];
                }
                if (i > 0) sb.append(',');
                sb.append("{\"id\":\"").append(m[0]).append("\",\"ms\":").append(ms).append('}');
            }
            sb.append(']');
            act.sp.edit().putString("mirrorBest", best).apply();
            act.call("__onSpeed", "{\"result\":" + sb + ",\"best\":\"" + best + "\"}");
        }
    }
}
