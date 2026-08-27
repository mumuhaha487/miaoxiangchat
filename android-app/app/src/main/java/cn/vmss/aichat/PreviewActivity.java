package cn.vmss.aichat;

import android.content.Intent;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;

public final class PreviewActivity extends AppCompatActivity {
    public static final String EXTRA_URL = "preview_url";
    private WebView webView;
    private TextView address;
    private ProgressBar progress;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        String url = getIntent().getStringExtra(EXTRA_URL);
        if (!AppConfig.isTrustedHttps(Uri.parse(url == null ? "" : url))) { finish(); return; }
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(getColor(R.color.window_background));
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(UiKit.dp(this, 6), UiKit.dp(this, 4), UiKit.dp(this, 6), UiKit.dp(this, 4));
        Button back = UiKit.button(this, "后退", false);
        Button reload = UiKit.button(this, "刷新", false);
        Button external = UiKit.button(this, "浏览器", false);
        Button close = UiKit.button(this, "关闭", false);
        address = UiKit.text(this, url, 11, UiKit.MUTED);
        address.setSingleLine(true);
        back.setOnClickListener(view -> { if (webView.canGoBack()) webView.goBack(); else finish(); });
        reload.setOnClickListener(view -> webView.reload());
        external.setOnClickListener(view -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(webView.getUrl()))));
        close.setOnClickListener(view -> finish());
        toolbar.addView(back, new LinearLayout.LayoutParams(-2, UiKit.dp(this, 42)));
        toolbar.addView(reload, new LinearLayout.LayoutParams(-2, UiKit.dp(this, 42)));
        toolbar.addView(address, new LinearLayout.LayoutParams(0, UiKit.dp(this, 42), 1));
        toolbar.addView(external, new LinearLayout.LayoutParams(-2, UiKit.dp(this, 42)));
        toolbar.addView(close, new LinearLayout.LayoutParams(-2, UiKit.dp(this, 42)));
        root.addView(toolbar, new LinearLayout.LayoutParams(-1, -2));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        root.addView(progress, new LinearLayout.LayoutParams(-1, UiKit.dp(this, 3)));
        webView = new WebView(this);
        configure();
        root.addView(webView, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        ViewCompat.requestApplyInsets(root);
        webView.loadUrl(url);
    }

    private void configure() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (AppConfig.isTrustedHttps(uri)) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); } catch (Exception ignored) {}
                return true;
            }
            @Override public void onPageStarted(WebView view, String url, Bitmap icon) {
                progress.setVisibility(View.VISIBLE); address.setText(url);
            }
            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE); address.setText(url);
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) UiKit.toast(PreviewActivity.this, "页面加载失败：" + error.getDescription());
            }
        });
        webView.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) { progress.setProgress(value); }
        });
    }

    @Override protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.setWebChromeClient(null);
            webView.setWebViewClient(null);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
