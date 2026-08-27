package cn.vmss.aichat;

import android.content.Intent;
import android.net.Uri;
import android.webkit.JavascriptInterface;

public final class WebAppBridge {
    private final MainActivity activity;
    private final SecureSessionStore session;
    private final ApiClient api;
    private final UpdateManager updates;
    private final SharedFileManager sharedFiles;

    WebAppBridge(MainActivity activity, SecureSessionStore session, UpdateManager updates, SharedFileManager sharedFiles) {
        this.activity = activity;
        this.session = session;
        this.api = new ApiClient(activity, session);
        this.updates = updates;
        this.sharedFiles = sharedFiles;
    }

    @JavascriptInterface public void setAuthToken(String token) { session.setToken(token); }
    @JavascriptInterface public void clearAuthToken() { session.clearSession(); }
    @JavascriptInterface public String getDeviceId() { return session.deviceId(); }
    @JavascriptInterface public String getDeviceName() { return session.deviceName(); }
    @JavascriptInterface public String getTrustedDeviceToken() { return session.trustToken(); }
    @JavascriptInterface public void setTrustedDeviceToken(String token) { session.setTrustToken(token); }
    @JavascriptInterface public void clearTrustedDeviceToken() { session.clearTrust(); }
    @JavascriptInterface public String getAppVersion() { return AppConfig.VERSION; }

    @JavascriptInterface
    public void initializeNotificationCursor(long notificationId) {
        session.initializeNotificationCursor(notificationId);
    }

    @JavascriptInterface
    public void showNotification(long id, String category, String title, String body) {
        activity.runOnUiThread(() -> NotificationHelper.publish(activity, id, category, title, body));
    }

    @JavascriptInterface
    public void checkForUpdates() {
        activity.runOnUiThread(() -> updates.check(true));
    }

    @JavascriptInterface public String getPendingSharedFiles() { return sharedFiles.pendingJson(); }

    @JavascriptInterface
    public void discardPendingSharedFiles() {
        sharedFiles.discardAll();
        activity.runOnUiThread(() -> activity.dispatchWebEvent("vmss-shared-files-changed", new org.json.JSONObject()));
    }

    @JavascriptInterface
    public void uploadPendingSharedFiles(String conversationId) {
        sharedFiles.upload(conversationId, api, new SharedFileManager.UploadCallback() {
            @Override public void onSuccess(String selectedConversationId, org.json.JSONArray attachments) {
                org.json.JSONObject detail = new org.json.JSONObject();
                try {
                    detail.put("conversationId", selectedConversationId);
                    detail.put("attachments", attachments);
                } catch (org.json.JSONException ignored) {}
                activity.dispatchWebEvent("vmss-shared-files-uploaded", detail);
                activity.dispatchWebEvent("vmss-shared-files-changed", new org.json.JSONObject());
            }

            @Override public void onError(String message) {
                org.json.JSONObject detail = new org.json.JSONObject();
                try { detail.put("message", message); } catch (org.json.JSONException ignored) {}
                activity.dispatchWebEvent("vmss-shared-files-failed", detail);
            }
        });
    }

    @JavascriptInterface
    public void downloadAuthenticatedFile(String url, String filename) {
        Uri uri = Uri.parse(url);
        String path = uri.getEncodedPath();
        if (!AppConfig.isTrustedHttps(uri) || path == null || !path.startsWith("/api/v1/workspace/download")) {
            activity.runOnUiThread(() -> UiKit.toast(activity, "下载地址无效"));
            return;
        }
        String relative = path.substring("/api/v1".length());
        if (uri.getEncodedQuery() != null) relative += "?" + uri.getEncodedQuery();
        api.download(relative, filename, new ApiClient.DownloadCallback() {
            @Override public void onSuccess(Uri saved) { UiKit.toast(activity, "文件已保存到下载目录"); }
            @Override public void onError(ApiClient.ApiException error) { UiKit.toast(activity, error.getMessage()); }
        });
    }

    @JavascriptInterface
    public void shareAuthenticatedFile(String url, String filename, String mimeType) {
        Uri uri = Uri.parse(url);
        String path = uri.getEncodedPath();
        if (!AppConfig.isTrustedHttps(uri) || path == null || !path.startsWith("/api/v1/workspace/download")) {
            activity.runOnUiThread(() -> UiKit.toast(activity, "分享地址无效"));
            return;
        }
        String relative = path.substring("/api/v1".length());
        if (uri.getEncodedQuery() != null) relative += "?" + uri.getEncodedQuery();
        api.downloadToCache(relative, filename, new ApiClient.DownloadCallback() {
            @Override public void onSuccess(Uri saved) {
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType(mimeType == null || mimeType.isBlank() ? "application/octet-stream" : mimeType);
                send.putExtra(Intent.EXTRA_STREAM, saved);
                send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                activity.startActivity(Intent.createChooser(send, "分享文件到其他应用"));
            }
            @Override public void onError(ApiClient.ApiException error) { UiKit.toast(activity, error.getMessage()); }
        });
    }
}
