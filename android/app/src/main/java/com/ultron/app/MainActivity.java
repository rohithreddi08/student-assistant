package com.ultron.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.view.Gravity;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

/** Shows the ULTRON interface (served by the Python server inside the app) and gives it native speech. */
public class MainActivity extends Activity {
    private static final String HOME = "http://localhost:8080/";
    private static final int REQ_PERMS = 7;

    private WebView web;
    private TextView splash;
    private PermissionRequest pendingWebRequest;
    private final Handler ui = new Handler(Looper.getMainLooper());

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.BLACK);
        getWindow().setNavigationBarColor(Color.BLACK);

        Bridge.init(this);
        UltronService.start(this);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(5, 6, 11));
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(5, 6, 11));
        web.setVisibility(View.INVISIBLE);
        splash = new TextView(this);
        splash.setText("ULTRON\n\nbooting core systems…");
        splash.setTextColor(Color.rgb(255, 60, 75));
        splash.setTextSize(20);
        splash.setGravity(Gravity.CENTER);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        root.addView(splash, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setSupportMultipleWindows(false);
        WebView.setWebContentsDebuggingEnabled(true);

        web.addJavascriptInterface(new TtsJs(), "AndroidTTS");
        if (SpeechRecognizer.isRecognitionAvailable(this)) web.addJavascriptInterface(new SpeechJs(), "AndroidSpeech");
        Bridge.jsListener = (id, type, index) -> js("window.__androidTTS&&window.__androidTTS(" + id + ",'" + type + "'," + index + ")");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return openOutside(req.getUrl());
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openOutside(Uri.parse(url));
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                web.setVisibility(View.VISIBLE);
                splash.setVisibility(View.GONE);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                ui.post(() -> grantWeb(request));
            }
        });

        askPermissions();
        waitForServerThenLoad();
    }

    /** Links to other sites open in the phone's browser / apps; the ULTRON page stays here. */
    private boolean openOutside(Uri uri) {
        String host = uri.getHost();
        if ("localhost".equals(host) || "127.0.0.1".equals(host)) return false;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception ignored) {}
        return true;
    }

    private void waitForServerThenLoad() {
        new Thread(() -> {
            for (int i = 0; i < 240; i++) {      // up to ~2 minutes on a slow first start
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(HOME + "api/config").openConnection();
                    c.setConnectTimeout(500);
                    c.setReadTimeout(1500);
                    if (c.getResponseCode() == 200) {
                        ui.post(() -> web.loadUrl(HOME));
                        return;
                    }
                } catch (Exception ignored) {}
                final int n = i;
                if (i % 4 == 0) ui.post(() -> splash.setText("ULTRON\n\nbooting core systems" + new String(new char[n / 4 % 4]).replace("\0", ".")));
                try { Thread.sleep(500); } catch (InterruptedException e) { return; }
            }
            ui.post(() -> splash.setText("ULTRON could not start.\nClose the app and open it again."));
        }).start();
    }

    // ------------------------------------------------------------------ permissions
    private String[] wanted() {
        List<String> p = new ArrayList<>();
        p.add(Manifest.permission.CAMERA);
        p.add(Manifest.permission.RECORD_AUDIO);
        p.add(Manifest.permission.READ_CONTACTS);
        p.add(Manifest.permission.CALL_PHONE);
        if (Build.VERSION.SDK_INT >= 33) p.add(Manifest.permission.POST_NOTIFICATIONS);
        return p.toArray(new String[0]);
    }

    private void askPermissions() {
        if (Build.VERSION.SDK_INT < 23) return;
        List<String> missing = new ArrayList<>();
        for (String p : wanted()) if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) missing.add(p);
        if (!missing.isEmpty()) requestPermissions(missing.toArray(new String[0]), REQ_PERMS);
    }

    private boolean has(String p) {
        return Build.VERSION.SDK_INT < 23 || checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED;
    }

    /** The page asked for camera / microphone: grant it if Android allows, otherwise ask the user first. */
    private void grantWeb(PermissionRequest req) {
        List<String> need = new ArrayList<>();
        for (String r : req.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r) && !has(Manifest.permission.CAMERA)) need.add(Manifest.permission.CAMERA);
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r) && !has(Manifest.permission.RECORD_AUDIO)) need.add(Manifest.permission.RECORD_AUDIO);
        }
        if (need.isEmpty()) { req.grant(req.getResources()); return; }
        pendingWebRequest = req;
        requestPermissions(need.toArray(new String[0]), REQ_PERMS + 1);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code == REQ_PERMS + 1 && pendingWebRequest != null) {
            PermissionRequest req = pendingWebRequest;
            pendingWebRequest = null;
            List<String> ok = new ArrayList<>();
            for (String r : req.getResources()) {
                if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r) && has(Manifest.permission.CAMERA)) ok.add(r);
                if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r) && has(Manifest.permission.RECORD_AUDIO)) ok.add(r);
            }
            if (ok.isEmpty()) req.deny(); else req.grant(ok.toArray(new String[0]));
        }
    }

    // ------------------------------------------------------------------ page <-> native helpers
    private void js(String code) {
        ui.post(() -> { if (web != null) web.evaluateJavascript(code, null); });
    }

    /** Text-to-speech for the page (window.AndroidTTS). */
    class TtsJs {
        @JavascriptInterface
        public void speak(int id, String text, String lang, float pitch, float rate) {
            Bridge.ttsSpeak("js:" + id, text, lang, pitch, rate);
        }

        @JavascriptInterface
        public void stop() {
            Bridge.ttsStop();
        }
    }

    /** Android speech recognition for the page (window.AndroidSpeech). Must run on the main thread. */
    class SpeechJs implements RecognitionListener {
        private SpeechRecognizer sr;
        private int current = 0;

        @JavascriptInterface
        public void start(int id, String lang, boolean partial, int maxAlts) {
            ui.post(() -> {
                if (!has(Manifest.permission.RECORD_AUDIO)) { send(id, "error", "9"); return; }
                if (sr == null) {
                    sr = SpeechRecognizer.createSpeechRecognizer(MainActivity.this);
                    sr.setRecognitionListener(this);
                } else {
                    sr.cancel();
                }
                current = id;
                Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                        .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                        .putExtra(RecognizerIntent.EXTRA_LANGUAGE, lang)
                        .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, partial)
                        .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, Math.max(1, maxAlts))
                        .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getPackageName())
                        .putExtra("android.speech.extra.SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS", 1500);
                try {
                    sr.startListening(i);
                } catch (Exception e) {
                    send(id, "error", "5");
                }
            });
        }

        @JavascriptInterface
        public void stop(int id) {
            ui.post(() -> { if (sr != null && current == id) sr.stopListening(); });
        }

        @JavascriptInterface
        public void cancel(int id) {
            ui.post(() -> { if (sr != null && current == id) { sr.cancel(); current = 0; } });
        }

        private void send(int id, String type, String data) {
            js("window.__androidSR&&window.__androidSR(" + id + ",'" + type + "'," + data + ")");
        }

        private String alts(Bundle b) {
            JSONArray out = new JSONArray();
            ArrayList<String> texts = b == null ? null : b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            float[] conf = b == null ? null : b.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES);
            if (texts != null) {
                for (int k = 0; k < texts.size(); k++) {
                    try {
                        JSONObject o = new JSONObject();
                        o.put("t", texts.get(k));
                        o.put("c", conf != null && k < conf.length ? (double) conf[k] : 0.0);
                        out.put(o);
                    } catch (Exception ignored) {}
                }
            }
            return out.toString();
        }

        @Override public void onReadyForSpeech(Bundle params) { send(current, "start", "0"); }
        @Override public void onBeginningOfSpeech() {}
        @Override public void onRmsChanged(float rmsdB) {}
        @Override public void onBufferReceived(byte[] buffer) {}
        @Override public void onEndOfSpeech() {}
        @Override public void onError(int error) { if (current != 0) send(current, "error", String.valueOf(error)); }
        @Override public void onResults(Bundle results) { if (current != 0) send(current, "final", alts(results)); }
        @Override public void onPartialResults(Bundle partial) { if (current != 0) send(current, "partial", alts(partial)); }
        @Override public void onEvent(int eventType, Bundle params) {}
    }

    // ------------------------------------------------------------------ lifecycle
    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else moveTaskToBack(true);      // keep ULTRON running instead of closing it
    }

    @Override
    protected void onDestroy() {
        Bridge.jsListener = null;
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
