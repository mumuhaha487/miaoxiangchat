package cn.vmss.aichat;

import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Environment;
import android.provider.Settings;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

final class UpdateManager {
    private static final long MAX_APK_BYTES = 200L * 1024L * 1024L;
    private final MainActivity activity;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final OkHttpClient http = new OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .build();
    private File pendingInstall;

    UpdateManager(MainActivity activity) { this.activity = activity; }

    void check(boolean manual) {
        worker.execute(() -> {
            try (Response response = http.newCall(new Request.Builder()
                .url(AppConfig.api("/app/android-release"))
                .header("Accept", "application/json")
                .header("User-Agent", "MiaoxiangZhiDi/" + AppConfig.VERSION + " Android")
                .build()).execute()) {
                if (!response.isSuccessful() || response.body() == null) throw new IllegalStateException("更新服务暂不可用");
                JSONObject envelope = new JSONObject(response.body().string());
                JSONObject release = envelope.getJSONObject("data");
                if (!release.optBoolean("available", false)) {
                    if (manual) toast("暂未发布可用更新");
                    return;
                }
                int versionCode = release.getInt("versionCode");
                if (versionCode <= AppConfig.VERSION_CODE) {
                    if (manual) toast("当前已是最新版本 " + AppConfig.VERSION);
                    return;
                }
                String versionName = release.getString("versionName");
                String sha256 = release.getString("sha256").toLowerCase(Locale.ROOT);
                String downloadUrl = release.getString("downloadUrl");
                long sizeBytes = release.getLong("sizeBytes");
                validateRelease(downloadUrl, sha256, sizeBytes);
                activity.runOnUiThread(() -> new AlertDialog.Builder(activity)
                    .setTitle("发现新版本 " + versionName)
                    .setMessage("更新包 " + readableSize(sizeBytes) + "，下载并校验后将打开 Android 安装界面。")
                    .setNegativeButton("稍后", null)
                    .setPositiveButton("立即更新", (dialog, which) -> download(downloadUrl, sha256, sizeBytes, versionName))
                    .show());
            } catch (Exception error) {
                if (manual) toast(error.getMessage() == null ? "检查更新失败" : error.getMessage());
            }
        });
    }

    private void download(String url, String expectedHash, long expectedSize, String versionName) {
        toast("正在下载版本 " + versionName);
        worker.execute(() -> {
            File directory = new File(activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "updates");
            if (!directory.isDirectory() && !directory.mkdirs()) {
                toast("无法创建更新目录");
                return;
            }
            File partial = new File(directory, "MiaoxiangZhiDi-update.apk.part");
            File target = new File(directory, "MiaoxiangZhiDi-update.apk");
            try (Response response = http.newCall(new Request.Builder().url(url).build()).execute()) {
                if (!response.isSuccessful() || response.body() == null) throw new IllegalStateException("更新包下载失败");
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                long total = 0;
                try (InputStream input = response.body().byteStream(); FileOutputStream output = new FileOutputStream(partial)) {
                    byte[] buffer = new byte[32_768];
                    int read;
                    while ((read = input.read(buffer)) >= 0) {
                        total += read;
                        if (total > MAX_APK_BYTES) throw new IllegalStateException("更新包超过大小限制");
                        digest.update(buffer, 0, read);
                        output.write(buffer, 0, read);
                    }
                }
                String actualHash = hex(digest.digest());
                if (total != expectedSize || !actualHash.equals(expectedHash)) throw new IllegalStateException("更新包校验失败");
                if (target.exists() && !target.delete()) throw new IllegalStateException("无法替换旧更新包");
                if (!partial.renameTo(target)) throw new IllegalStateException("无法保存更新包");
                pendingInstall = target;
                activity.runOnUiThread(this::installPending);
            } catch (Exception error) {
                if (partial.exists()) partial.delete();
                toast(error.getMessage() == null ? "更新失败" : error.getMessage());
            }
        });
    }

    void resumePendingInstall() {
        if (pendingInstall != null && pendingInstall.isFile()) installPending();
    }

    private void installPending() {
        File apk = pendingInstall;
        if (apk == null || !apk.isFile()) return;
        if (!activity.getPackageManager().canRequestPackageInstalls()) {
            Intent permission = new Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + activity.getPackageName())
            );
            activity.startActivity(permission);
            toast("请允许此应用安装更新，然后返回继续");
            return;
        }
        Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".updates", apk);
        Intent install = new Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        activity.startActivity(install);
        pendingInstall = null;
    }

    void close() { worker.shutdownNow(); }

    private void validateRelease(String url, String hash, long size) {
        Uri uri = Uri.parse(url);
        if (!AppConfig.isTrustedHttps(uri) || !"/downloads/AIchatMUMU-arm64.apk".equals(uri.getPath())) {
            throw new IllegalArgumentException("更新地址无效");
        }
        if (!hash.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("更新校验值无效");
        if (size <= 0 || size > MAX_APK_BYTES) throw new IllegalArgumentException("更新包大小无效");
    }

    private void toast(String message) { activity.runOnUiThread(() -> UiKit.toast(activity, message)); }

    private static String hex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte item : bytes) value.append(String.format(Locale.ROOT, "%02x", item & 0xff));
        return value.toString();
    }

    private static String readableSize(long bytes) {
        return String.format(Locale.CHINA, "%.1f MB", bytes / 1024d / 1024d);
    }
}
