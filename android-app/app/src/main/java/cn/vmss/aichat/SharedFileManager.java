package cn.vmss.aichat;

import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SharedFileManager {
    public interface CaptureCallback {
        void onCompleted(String warning);
    }

    public interface UploadCallback {
        void onSuccess(String conversationId, JSONArray attachments);
        void onError(String message);
    }

    private static final int MAX_FILES = 8;
    private static final long MAX_BYTES = 20L * 1024L * 1024L;
    private final Context context;
    private final File directory;
    private final ExecutorService files = Executors.newSingleThreadExecutor();
    private JSONArray pending = new JSONArray();

    SharedFileManager(Context context) {
        this.context = context.getApplicationContext();
        this.directory = new File(this.context.getCacheDir(), "shared-files");
        if (!directory.isDirectory()) directory.mkdirs();
        clearCachedFiles();
    }

    public void capture(Intent intent, CaptureCallback completed) {
        if (intent == null || (!Intent.ACTION_SEND.equals(intent.getAction()) && !Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction()))) {
            completed.onCompleted(null);
            return;
        }
        ArrayList<Uri> uris = new ArrayList<>();
        if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Uri> values = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (values != null) uris.addAll(values);
        } else {
            Uri value = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (value != null) uris.add(value);
        }
        ClipData clipData = intent.getClipData();
        if (clipData != null) {
            for (int index = 0; index < clipData.getItemCount(); index++) {
                Uri value = clipData.getItemAt(index).getUri();
                if (value != null && !uris.contains(value)) uris.add(value);
            }
        }
        files.execute(() -> {
            JSONArray queue = queue();
            String warning = null;
            for (Uri uri : uris) {
                if (queue.length() >= MAX_FILES) {
                    warning = "最多可暂存 8 个分享文件";
                    break;
                }
                try {
                    JSONObject item = copyUri(uri, intent.getType());
                    if (item != null) queue.put(item);
                } catch (Exception error) {
                    if (warning == null || warning.isBlank()) {
                        warning = error.getMessage();
                        if (warning == null || warning.isBlank()) warning = "无法读取分享文件";
                    }
                }
            }
            save(queue);
            completed.onCompleted(warning);
        });
    }

    public synchronized String pendingJson() {
        JSONArray source = queue();
        JSONArray result = new JSONArray();
        for (int index = 0; index < source.length(); index++) {
            JSONObject item = source.optJSONObject(index);
            if (item == null) continue;
            try {
                result.put(new JSONObject()
                    .put("id", item.optString("id"))
                    .put("filename", item.optString("filename"))
                    .put("mimeType", item.optString("mimeType"))
                    .put("sizeBytes", item.optLong("sizeBytes")));
            } catch (Exception ignored) {}
        }
        return result.toString();
    }

    public void upload(String conversationId, ApiClient api, UploadCallback callback) {
        if (conversationId == null || conversationId.isBlank()) {
            callback.onError("请选择对话");
            return;
        }
        JSONArray snapshot = queue();
        if (snapshot.length() == 0) {
            callback.onError("没有待上传文件");
            return;
        }
        uploadNext(conversationId, api, snapshot, 0, new JSONArray(), callback);
    }

    public synchronized void discardAll() {
        JSONArray current = queue();
        for (int index = 0; index < current.length(); index++) {
            JSONObject item = current.optJSONObject(index);
            if (item != null) new File(directory, item.optString("cacheName")).delete();
        }
        pending = new JSONArray();
        clearCachedFiles();
    }

    private void uploadNext(String conversationId, ApiClient api, JSONArray snapshot, int index, JSONArray uploaded, UploadCallback callback) {
        if (index >= snapshot.length()) {
            callback.onSuccess(conversationId, uploaded);
            return;
        }
        JSONObject item = snapshot.optJSONObject(index);
        if (item == null) {
            uploadNext(conversationId, api, snapshot, index + 1, uploaded, callback);
            return;
        }
        File source = new File(directory, item.optString("cacheName"));
        api.uploadFile(
            "/conversations/" + conversationId + "/attachments",
            source,
            item.optString("filename", "file"),
            item.optString("mimeType", "application/octet-stream"),
            new ApiClient.JsonCallback() {
                @Override public void onSuccess(JSONObject data) {
                    JSONObject attachment = data.optJSONObject("attachment");
                    if (attachment != null) uploaded.put(attachment);
                    remove(item.optString("id"));
                    uploadNext(conversationId, api, snapshot, index + 1, uploaded, callback);
                }

                @Override public void onError(ApiClient.ApiException error) {
                    callback.onError(error.getMessage());
                }
            }
        );
    }

    private JSONObject copyUri(Uri uri, String fallbackMime) throws Exception {
        String filename = queryName(uri);
        String mime = context.getContentResolver().getType(uri);
        if (mime == null || mime.isBlank()) mime = fallbackMime;
        if (mime == null || mime.isBlank()) mime = "application/octet-stream";
        String id = UUID.randomUUID().toString();
        String extension = extension(filename);
        String cacheName = id + extension;
        File target = new File(directory, cacheName);
        long total = 0;
        try (InputStream input = context.getContentResolver().openInputStream(uri); FileOutputStream output = new FileOutputStream(target)) {
            if (input == null) throw new IllegalArgumentException("无法读取分享文件");
            byte[] buffer = new byte[16_384];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                total += read;
                if (total > MAX_BYTES) throw new IllegalArgumentException("分享文件超过 20 MB");
                output.write(buffer, 0, read);
            }
        } catch (Exception error) {
            target.delete();
            throw error;
        }
        if (total <= 0) {
            target.delete();
            return null;
        }
        return new JSONObject()
            .put("id", id)
            .put("filename", filename)
            .put("mimeType", mime)
            .put("sizeBytes", total)
            .put("cacheName", cacheName);
    }

    private String queryName(Uri uri) {
        String value = "shared-file";
        try (Cursor cursor = context.getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String found = cursor.getString(0);
                if (found != null && !found.isBlank()) value = found;
            }
        } catch (Exception ignored) {}
        value = value.replace('\\', '/');
        value = value.substring(value.lastIndexOf('/') + 1).replaceAll("[\\r\\n]", "").trim();
        return value.isBlank() ? "shared-file" : value.substring(0, Math.min(value.length(), 180));
    }

    private String extension(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot < 0 ? "" : filename.substring(dot).toLowerCase(Locale.ROOT);
    }

    private synchronized JSONArray queue() {
        try { return new JSONArray(pending.toString()); }
        catch (Exception ignored) { return new JSONArray(); }
    }

    private synchronized void save(JSONArray queue) {
        try { pending = new JSONArray(queue.toString()); }
        catch (Exception ignored) { pending = new JSONArray(); }
    }

    private synchronized void remove(String id) {
        JSONArray current = queue();
        JSONArray next = new JSONArray();
        for (int index = 0; index < current.length(); index++) {
            JSONObject item = current.optJSONObject(index);
            if (item == null || id.equals(item.optString("id"))) {
                if (item != null) new File(directory, item.optString("cacheName")).delete();
            } else {
                next.put(item);
            }
        }
        save(next);
    }

    private synchronized void clearCachedFiles() {
        File[] cached = directory.listFiles();
        if (cached != null) for (File file : cached) file.delete();
    }

    public void close() {
        discardAll();
        files.shutdownNow();
    }
}
