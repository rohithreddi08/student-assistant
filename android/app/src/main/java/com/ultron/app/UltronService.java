package com.ultron.app;

import android.app.Notification;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Runs the ULTRON Python server (http://localhost:8080) as a foreground service, so reminders, alarms
 * and phone-laptop sync keep working when the app is in the background.
 */
public class UltronService extends Service {
    private static final String TAG = "ULTRON";
    private static volatile boolean pythonStarted = false;
    private PowerManager.WakeLock wakeLock;

    public static void start(Context c) {
        Intent i = new Intent(c, UltronService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
            else c.startService(i);
        } catch (Exception e) {
            Log.w(TAG, "could not start service", e);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Bridge.init(this);
        Notification n = Bridge.builder(Bridge.CH_SERVICE)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle("ULTRON is online")
                .setContentText("Reminders and sync are running")
                .setOngoing(true)
                .setContentIntent(Bridge.openAppIntent())
                .build();
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        else startForeground(1, n);

        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ultron:server");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();

        if (!pythonStarted) {
            pythonStarted = true;
            Thread t = new Thread(this::runPython, "ultron-python");
            t.setDaemon(true);
            t.start();
        }
    }

    private void runPython() {
        try {
            if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
            String staticDir = unpackStatic();
            Python.getInstance().getModule("android_main")
                    .callAttr("start", getFilesDir().getAbsolutePath(), staticDir);
        } catch (Throwable e) {
            Log.e(TAG, "ULTRON server stopped", e);
            pythonStarted = false;
        }
    }

    /** Copies the web interface (assets/static) to app storage once per app version. */
    private String unpackStatic() throws Exception {
        File dir = new File(getFilesDir(), "static");
        File stamp = new File(dir, ".version");
        PackageInfo pi = getPackageManager().getPackageInfo(getPackageName(), 0);
        String version = pi.versionName + "-" + pi.lastUpdateTime;
        if (stamp.exists() && version.equals(read(stamp))) return dir.getAbsolutePath();
        deleteTree(dir);
        copyAssets(getAssets(), "static", dir);
        try (OutputStream o = new FileOutputStream(stamp)) { o.write(version.getBytes("UTF-8")); }
        return dir.getAbsolutePath();
    }

    private static void copyAssets(AssetManager am, String path, File to) throws Exception {
        String[] list = am.list(path);
        if (list == null || list.length == 0) {          // a file
            to.getParentFile().mkdirs();
            try (InputStream in = am.open(path); OutputStream out = new FileOutputStream(to)) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            return;
        }
        to.mkdirs();
        for (String name : list) copyAssets(am, path + "/" + name, new File(to, name));
    }

    private static String read(File f) {
        try (InputStream in = new java.io.FileInputStream(f)) {
            byte[] b = new byte[(int) f.length()];
            int n = in.read(b);
            return new String(b, 0, Math.max(n, 0), "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    private static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteTree(k);
        f.delete();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
