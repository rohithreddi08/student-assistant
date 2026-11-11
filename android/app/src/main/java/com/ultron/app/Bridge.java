package com.ultron.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.AlarmClock;
import android.provider.ContactsContract;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Phone features for the Python side (phone.py calls these through Chaquopy) and the shared
 * text-to-speech engine (also used by the page through MainActivity's AndroidTTS interface).
 */
public final class Bridge {
    private static final String TAG = "ULTRON";
    public static final String CH_REMINDERS = "reminders";
    public static final String CH_SERVICE = "service";

    private static Context ctx;
    private static TextToSpeech tts;
    private static volatile boolean ttsReady = false;
    private static final List<Object[]> pending = new ArrayList<>();
    private static int pyUtterance = 0;

    /** Receives page speech events: (jsId, type: start|word|done|error, charIndex). */
    public interface TtsListener { void onEvent(int jsId, String type, int index); }
    public static volatile TtsListener jsListener;

    private Bridge() {}

    public static synchronized void init(Context c) {
        if (ctx != null) return;
        ctx = c.getApplicationContext();
        createChannels();
        initTts();
    }

    static void createChannels() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        NotificationChannel r = new NotificationChannel(CH_REMINDERS, "Reminders", NotificationManager.IMPORTANCE_HIGH);
        r.enableVibration(true);
        r.setVibrationPattern(new long[]{0, 500, 300, 500});
        nm.createNotificationChannel(r);
        NotificationChannel s = new NotificationChannel(CH_SERVICE, "ULTRON running", NotificationManager.IMPORTANCE_MIN);
        s.setShowBadge(false);
        nm.createNotificationChannel(s);
    }

    static PendingIntent openAppIntent() {
        Intent i = new Intent(ctx, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        return PendingIntent.getActivity(ctx, 0, i, flags);
    }

    @SuppressWarnings("deprecation")
    static Notification.Builder builder(String channel) {
        return Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(ctx, channel) : new Notification.Builder(ctx);
    }

    // ------------------------------------------------------------------ notifications
    @SuppressWarnings("deprecation")
    public static void notify(String title, String text, int id) {
        try {
            Notification.Builder b = builder(CH_REMINDERS)
                    .setSmallIcon(R.drawable.ic_stat)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setAutoCancel(true)
                    .setContentIntent(openAppIntent());
            if (Build.VERSION.SDK_INT < 26) {
                b.setPriority(Notification.PRIORITY_HIGH).setVibrate(new long[]{0, 500, 300, 500})
                        .setDefaults(Notification.DEFAULT_SOUND);
            }
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            nm.notify(id >= 0 ? id : (int) (System.currentTimeMillis() % 100000) + 1000, b.build());
        } catch (Exception e) {
            Log.w(TAG, "notify failed", e);
        }
    }

    // ------------------------------------------------------------------ text to speech
    private static void initTts() {
        tts = new TextToSpeech(ctx, status -> {
            ttsReady = status == TextToSpeech.SUCCESS;
            if (ttsReady) {
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String id) { fire(id, "start", 0); }
                    @Override public void onDone(String id) { fire(id, "done", 0); }
                    @Override public void onError(String id) { fire(id, "error", 0); }
                    @Override public void onStop(String id, boolean interrupted) { fire(id, "done", 0); }
                    @Override public void onRangeStart(String id, int start, int end, int frame) { fire(id, "word", start); }
                });
                synchronized (pending) {
                    for (Object[] p : pending) ttsSpeak((String) p[0], (String) p[1], (String) p[2], (float) p[3], (float) p[4]);
                    pending.clear();
                }
            }
        });
    }

    private static void fire(String utteranceId, String type, int index) {
        if (utteranceId == null || !utteranceId.startsWith("js:")) return;
        TtsListener l = jsListener;
        if (l != null) l.onEvent(Integer.parseInt(utteranceId.substring(3)), type, index);
    }

    public static void ttsSpeak(String utteranceId, String text, String lang, float pitch, float rate) {
        if (!ttsReady) {
            synchronized (pending) { pending.add(new Object[]{utteranceId, text, lang, pitch, rate}); }
            return;
        }
        try {
            if (lang != null && !lang.isEmpty()) {
                Locale loc = Locale.forLanguageTag(lang);
                if (tts.isLanguageAvailable(loc) >= TextToSpeech.LANG_AVAILABLE) tts.setLanguage(loc);
            }
            tts.setPitch(pitch);
            tts.setSpeechRate(rate);
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId);
        } catch (Exception e) {
            Log.w(TAG, "tts failed", e);
            fire(utteranceId, "error", 0);
        }
    }

    public static void ttsStop() {
        if (tts != null) tts.stop();
    }

    /** Called from Python (spoken reminders). */
    public static void speak(String text) {
        ttsSpeak("py:" + (++pyUtterance), text, "", 0.8f, 1.0f);
    }

    // ------------------------------------------------------------------ alarms, apps, links, calls
    private static boolean start(Intent i) {
        try {
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(i);
            return true;
        } catch (ActivityNotFoundException | SecurityException e) {
            Log.w(TAG, "cannot start " + i, e);
            return false;
        }
    }

    public static boolean setAlarm(int hour, int minute, String label) {
        Intent i = new Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, hour)
                .putExtra(AlarmClock.EXTRA_MINUTES, minute)
                .putExtra(AlarmClock.EXTRA_MESSAGE, label)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        return start(i);
    }

    public static boolean openAction(String action) {
        return start(new Intent(action));
    }

    /** Opens an installed app by its name ("whatsapp", "calculator", "spotify"...). */
    public static boolean launchApp(String name) {
        String want = name.toLowerCase(Locale.ROOT).trim();
        if (want.isEmpty()) return false;
        PackageManager pm = ctx.getPackageManager();
        Intent main = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> apps = pm.queryIntentActivities(main, 0);
        ResolveInfo best = null;
        int bestScore = 0;
        for (ResolveInfo ri : apps) {
            String label = String.valueOf(ri.loadLabel(pm)).toLowerCase(Locale.ROOT).trim();
            int score = label.equals(want) ? 3 : label.startsWith(want) ? 2 : label.contains(want) ? 1 : 0;
            if (score > bestScore) { best = ri; bestScore = score; }
        }
        if (best == null) return false;
        Intent launch = pm.getLaunchIntentForPackage(best.activityInfo.packageName);
        return launch != null && start(launch);
    }

    public static boolean openUrl(String url) {
        return start(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
    }

    public static boolean call(String number) {
        boolean allowed = Build.VERSION.SDK_INT < 23
                || ctx.checkSelfPermission(Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED;
        Intent i = new Intent(allowed ? Intent.ACTION_CALL : Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(number)));
        return start(i);
    }

    /** All contacts with phone numbers as JSON: [{"name": ..., "number": ...}]. */
    public static String contactsJson() {
        JSONArray out = new JSONArray();
        if (Build.VERSION.SDK_INT >= 23
                && ctx.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
            return out.toString();
        }
        String[] cols = {ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER};
        try (Cursor c = ctx.getContentResolver().query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, cols,
                null, null, ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC")) {
            while (c != null && c.moveToNext()) {
                JSONObject o = new JSONObject();
                o.put("name", c.getString(0));
                o.put("number", c.getString(1));
                out.put(o);
            }
        } catch (Exception e) {
            Log.w(TAG, "contacts failed", e);
        }
        return out.toString();
    }
}
