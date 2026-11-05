package cn.biigdrong.mhgu;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 下载源：GitHub 直连或国内镜像前缀。
 *
 * 所有 GitHub 链接都能套前缀，例如
 *   https://ghfast.top/https://github.com/owner/repo/releases/download/v1/x.apk
 * 选「自动」时先并发测速，取首字节最快的一个。
 */
public final class Mirrors {

    /** id, 显示名, 前缀（直连为空） */
    static final String[][] LIST = {
            {"direct", "直连 GitHub", ""},
            {"ghfast", "ghfast.top", "https://ghfast.top/"},
            {"ghproxy", "gh-proxy.com", "https://gh-proxy.com/"},
            {"ghnet", "ghproxy.net", "https://ghproxy.net/"},
            {"llkk", "gh.llkk.cc", "https://gh.llkk.cc/"},
    };

    /** 测速用的探针：小文件，能代表实际下载链路 */
    static final String PROBE =
            "https://github.com/khorne-hub/gu-native/archive/refs/heads/main.zip";

    private Mirrors() {
    }

    /** 当前生效的源：auto 时用测速结果，没有结果就直连 */
    static String resolve(android.content.Context c) {
        android.content.SharedPreferences sp = c.getSharedPreferences("mhgu", 0);
        String sel = sp.getString("mirror", "auto");
        if (!"auto".equals(sel)) return sel;
        String best = sp.getString("mirrorBest", "");
        return best.isEmpty() ? "direct" : best;
    }

    static String[] byId(String id) {
        for (String[] m : LIST) {
            if (m[0].equals(id)) return m;
        }
        return LIST[0];
    }

    static String apply(String url, String id) {
        if (url == null) return null;
        if (id == null || "auto".equals(id)) return url;
        String prefix = byId(id)[2];
        if (prefix.isEmpty()) return url;
        if (url.startsWith(prefix)) return url;
        return prefix + url;
    }

    /** 单源测速：返回毫秒，失败返回 -1 */
    static long probe(String id) {
        String url = apply(PROBE, id);
        HttpURLConnection c = null;
        try {
            long t0 = System.currentTimeMillis();
            c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(4000);
            c.setReadTimeout(6000);
            c.setInstanceFollowRedirects(true);
            c.setRequestProperty("User-Agent", "mhgu-manager");
            c.setRequestProperty("Range", "bytes=0-65535");
            int code = c.getResponseCode();
            if (code >= 400) return -1;
            InputStream in = c.getInputStream();
            byte[] b = new byte[8192];
            int n = in.read(b);
            in.close();
            if (n <= 0) return -1;
            return System.currentTimeMillis() - t0;
        } catch (Throwable t) {
            return -1;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** 并发测速所有源，返回 [[id, ms], ...]，按毫秒升序 */
    static java.util.List<String[]> speedTest() {
        final java.util.List<String[]> out =
                java.util.Collections.synchronizedList(new java.util.ArrayList<String[]>());
        java.util.List<Thread> ts = new java.util.ArrayList<Thread>();
        for (String[] m : LIST) {
            Thread t = new Thread(new Probe(m[0], out));
            t.setDaemon(true);
            ts.add(t);
            t.start();
        }
        for (Thread t : ts) {
            try {
                t.join(8000);
            } catch (InterruptedException ignored) {
            }
        }
        sortByMs(out);
        return out;
    }

    static class Probe implements Runnable {
        private final String id;
        private final java.util.List<String[]> out;

        Probe(String id, java.util.List<String[]> out) {
            this.id = id;
            this.out = out;
        }

        public void run() {
            out.add(new String[]{id, String.valueOf(Mirrors.probe(id))});
        }
    }

    /** 手写插入排序：Comparator 的桥接方法会触发 R8 8.2.2 的 bug */
    static void sortByMs(java.util.List<String[]> out) {
        for (int i = 1; i < out.size(); i++) {
            String[] cur = out.get(i);
            long cm = ms(cur);
            int j = i - 1;
            while (j >= 0 && ms(out.get(j)) > cm) {
                out.set(j + 1, out.get(j));
                j--;
            }
            out.set(j + 1, cur);
        }
    }

    static long ms(String[] a) {
        long v = Long.parseLong(a[1]);
        return v < 0 ? Long.MAX_VALUE : v;
    }

    static String listJson(String selected) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < LIST.length; i++) {
            if (i > 0) sb.append(',');
            sb.append("{\"id\":\"").append(LIST[i][0])
              .append("\",\"name\":\"").append(LIST[i][1]).append("\"}");
        }
        sb.append(']');
        return "{\"list\":" + sb + ",\"selected\":\"" + (selected == null ? "auto" : selected) + "\"}";
    }
}
