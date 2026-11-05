package cn.biigdrong.mhgu;

import android.content.Context;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * 内置工具链：把 clang / lld / python 打包进 APK，装完即可在本机编译，不依赖 Termux。
 *
 * 布局约定：
 *   APK lib/arm64-v8a/libclang.so    → 安装后位于 nativeLibraryDir（唯一可直接 execve 的位置）
 *   APK lib/arm64-v8a/liblld.so      → 同上
 *   APK lib/arm64-v8a/libpython.so   → 同上
 *   assets/tc.zip                    → 首次启动解到 filesDir/tc（纯数据与 .so，不需要可执行权限）
 *     lib/libLLVM.so, lib/libclang-cpp.so, lib/libc++_shared.so, lib/libpython3.14.so, …
 *     clang/21/…                     → 头文件与 builtins
 *     py/…                           → CPython 标准库
 */
public final class Toolchain {

    private static final String MARK = ".ready";
    /** 载荷版本：改动 assets/tc.zip 布局时必须加一，强制已装用户重解 */
    private static final String TC_VERSION = "2026-11-05.5";

    private Toolchain() {
    }

    public static File root(Context c) {
        return new File(c.getFilesDir(), "tc");
    }

    public static File libDir(Context c) {
        return new File(root(c), "lib");
    }

    public static File sysroot(Context c) {
        return new File(root(c), "sysroot");
    }

    public static File resDir(Context c) {
        return new File(root(c), "clang/21");
    }

    public static File pyDir(Context c) {
        return new File(root(c), "py");
    }

    public static String nativeDir(Context c) {
        return c.getApplicationInfo().nativeLibraryDir;
    }

    public static boolean ready(Context c) {
        File m = new File(root(c), MARK);
        if (!m.isFile()) return false;
        return TC_VERSION.equals(readSmall(m).trim());
    }

    private static String readSmall(File f) {
        try {
            byte[] b = new byte[(int) Math.min(256, f.length())];
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            int n = in.read(b);
            in.close();
            return new String(b, 0, Math.max(0, n), "UTF-8");
        } catch (Throwable t) {
            return "";
        }
    }

    /** 首次启动展开 assets/tc.zip；失败时把原因写进 log 并返回 false */
    public static boolean ensure(Context c, StringBuilder log) {
        if (ready(c)) return true;
        File tmp = new File(c.getFilesDir(), "tc_unzip");
        deleteTree(tmp);
        if (!tmp.mkdirs()) {
            log.append("cannot create ").append(tmp).append('\n');
            return false;
        }
        long t0 = System.currentTimeMillis();
        try {
            InputStream in = new BufferedInputStream(c.getAssets().open("tc.zip"), 1 << 16);
            ZipInputStream z = new ZipInputStream(in);
            byte[] buf = new byte[1 << 16];
            ZipEntry e;
            int files = 0;
            while ((e = z.getNextEntry()) != null) {
                String name = e.getName();
                File f = new File(tmp, name);
                if (e.isDirectory()) {
                    f.mkdirs();
                    continue;
                }
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
            deleteTree(root(c));
            if (!tmp.renameTo(root(c))) {
                log.append("rename failed\n");
                return false;
            }
            FileOutputStream m = new FileOutputStream(new File(root(c), MARK));
            m.write(TC_VERSION.getBytes("UTF-8"));
            m.close();
            log.append("unzipped ").append(files).append(" files in ")
               .append(System.currentTimeMillis() - t0).append(" ms\n");
            return true;
        } catch (Throwable t) {
            log.append("unzip failed: ").append(t).append('\n');
            return false;
        }
    }

    /** 编译/运行时共用的环境变量 */
    public static List<String> env(Context c) {
        List<String> e = new ArrayList<String>();
        String ld = nativeDir(c) + ":" + libDir(c).getAbsolutePath();
        e.add("LD_LIBRARY_PATH=" + ld);
        e.add("PATH=" + nativeDir(c) + ":/system/bin");
        e.add("HOME=" + c.getFilesDir().getAbsolutePath());
        e.add("TMPDIR=" + c.getCacheDir().getAbsolutePath());
        e.add("PYTHONHOME=" + root(c).getAbsolutePath());
        e.add("PYTHONDONTWRITEBYTECODE=1");
        e.add("LANG=en_US.UTF-8");
        return e;
    }

    public static File clang(Context c) {
        return new File(nativeDir(c), "libclang.so");
    }

    public static File lld(Context c) {
        return new File(nativeDir(c), "ld.lld");
    }

    public static File python(Context c) {
        return new File(nativeDir(c), "libpython.so");
    }

    public static void applyEnv(ProcessBuilder pb, Context c) {
        for (String kv : env(c)) {
            int i = kv.indexOf('=');
            pb.environment().put(kv.substring(0, i), kv.substring(i + 1));
        }
    }

    public static void deleteTree(File f) {
        if (f == null || !f.exists()) return;
        File[] kids = f.listFiles();
        if (kids != null) {
            for (File k : kids) deleteTree(k);
        }
        f.delete();
    }
}
