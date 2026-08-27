package cn.vmss.aichat;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;

public final class MainActivity extends AppCompatActivity {
    private static final String LOCAL_ORIGIN = "https://appassets.androidplatform.net";
    private static final String START_URL = LOCAL_ORIGIN + "/index.html";

    private WebView webView;
    private ProgressBar progress;
    private ValueCallback<Uri[]> pendingFiles;
    private UpdateManager updates;
    private SharedFileManager sharedFiles;
    private boolean automaticUpdateChecked;

    private final ActivityResultLauncher<Intent> fileChooser = registerForActivityResult(
        new ActivityResultContracts.StartActivityForResult(),
        result -> {
            ValueCallback<Uri[]> callback = pendingFiles;
            pendingFiles = null;
            if (callback == null) return;
            callback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData()));
        }
    );
    private final ActivityResultLauncher<String> notificationPermission = registerForActivityResult(
        new ActivityResultContracts.RequestPermission(),
        granted -> {}
    );

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();
        NotificationHelper.createChannels(this);
        requestNotifications();
        updates = new UpdateManager(this);
        sharedFiles = new SharedFileManager(this);
        buildWebUi();
        captureSharedFiles(getIntent());
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack();
                else finish();
            }
        });
    }

    private void configureSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        controller.setAppearanceLightStatusBars(true);
        controller.setAppearanceLightNavigationBars(true);
    }

    private void buildWebUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(
                WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout()
            );
            Insets keyboard = windowInsets.getInsets(WindowInsetsCompat.Type.ime());
            view.setPadding(
                Math.max(bars.left, keyboard.left),
                bars.top,
                Math.max(bars.right, keyboard.right),
                Math.max(bars.bottom, keyboard.bottom)
            );
            return WindowInsetsCompat.CONSUMED;
        });

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(245, 246, 247));
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setUserAgentString(settings.getUserAgentString() + " MiaoxiangZhiDi/" + AppConfig.VERSION + " AndroidApp");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) settings.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        WebView.setWebContentsDebuggingEnabled(false);

        WebViewAssetLoader assetLoader = new WebViewAssetLoader.Builder()
            .setDomain("appassets.androidplatform.net")
            .addPathHandler("/", new WebViewAssetLoader.AssetsPathHandler(this))
            .build();
        SecureSessionStore session = new SecureSessionStore(this);
        webView.addJavascriptInterface(new WebAppBridge(this, session, updates, sharedFiles), "VMSSAndroid");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return assetLoader.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isLocalAsset(uri)) return false;
                openExternalOrPreview(uri);
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                if (!automaticUpdateChecked && START_URL.equals(url)) {
                    automaticUpdateChecked = true;
                    view.postDelayed(() -> updates.check(false), 1_200);
                }
                dispatchWebEvent("vmss-shared-files-changed", new JSONObject());
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                progress.setVisibility(value >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFiles != null) pendingFiles.onReceiveValue(null);
                pendingFiles = callback;
                try {
                    fileChooser.launch(params.createIntent());
                    return true;
                } catch (Exception error) {
                    pendingFiles = null;
                    callback.onReceiveValue(null);
                    UiKit.toast(MainActivity.this, "无法打开文件选择器");
                    return false;
                }
            }
        });
        webView.setDownloadListener((url, userAgent, disposition, mimeType, size) -> {
            Uri uri = Uri.parse(url);
            if (AppConfig.isTrustedHttps(uri)) startActivity(new Intent(Intent.ACTION_VIEW, uri));
            else UiKit.toast(this, "下载地址无效");
        });

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.rgb(57, 112, 116)));
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        root.addView(progress, new FrameLayout.LayoutParams(-1, UiKit.dp(this, 2)));
        setContentView(root);
        ViewCompat.requestApplyInsets(root);
        webView.loadUrl(START_URL);
    }

    private boolean isLocalAsset(Uri uri) {
        return uri != null
            && "https".equalsIgnoreCase(uri.getScheme())
            && "appassets.androidplatform.net".equalsIgnoreCase(uri.getHost());
    }

    private void openExternalOrPreview(Uri uri) {
        if (AppConfig.isTrustedHttps(uri)) {
            startActivity(new Intent(this, PreviewActivity.class).putExtra(PreviewActivity.EXTRA_URL, uri.toString()));
            return;
        }
        String scheme = uri == null ? "" : String.valueOf(uri.getScheme());
        if ("https".equalsIgnoreCase(scheme) || "mailto".equalsIgnoreCase(scheme)) {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        }
    }

    private void requestNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
            && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS);
        }
    }

    private void captureSharedFiles(Intent intent) {
        if (sharedFiles == null) return;
        sharedFiles.capture(intent, warning -> {
            dispatchWebEvent("vmss-shared-files-changed", new JSONObject());
            if (warning != null && !warning.isBlank()) {
                runOnUiThread(() -> UiKit.toast(this, warning));
            }
        });
    }

    public void dispatchWebEvent(String name, JSONObject detail) {
        runOnUiThread(() -> {
            if (webView == null) return;
            String script = "window.dispatchEvent(new CustomEvent(" + JSONObject.quote(name) + ", {detail:" + detail + "}));";
            webView.evaluateJavascript(script, null);
        });
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        captureSharedFiles(intent);
    }

    @Override protected void onResume() {
        super.onResume();
        if (updates != null) updates.resumePendingInstall();
    }

    @Override protected void onDestroy() {
        if (pendingFiles != null) pendingFiles.onReceiveValue(null);
        pendingFiles = null;
        if (webView != null) {
            webView.removeJavascriptInterface("VMSSAndroid");
            webView.stopLoading();
            webView.setWebChromeClient(null);
            webView.setWebViewClient(null);
            webView.destroy();
        }
        if (updates != null) updates.close();
        if (sharedFiles != null) sharedFiles.close();
        super.onDestroy();
    }
}
