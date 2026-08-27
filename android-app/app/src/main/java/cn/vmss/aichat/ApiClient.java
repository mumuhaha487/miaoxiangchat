package cn.vmss.aichat;

import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Environment;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;

import androidx.core.content.FileProvider;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.io.File;
import java.io.FileOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.MultipartBody;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.ResponseBody;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

public final class ApiClient {
    public interface JsonCallback {
        void onSuccess(JSONObject data);
        void onError(ApiException error);
    }

    public interface DownloadCallback {
        void onSuccess(Uri uri);
        void onError(ApiException error);
    }

    public static final class ApiException extends Exception {
        public final int status;
        ApiException(String message, int status) { super(message); this.status = status; }
    }

    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private final Context context;
    private final SecureSessionStore session;
    private final OkHttpClient http;
    private final ExecutorService files = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());

    public ApiClient(Context context, SecureSessionStore session) {
        this.context = context.getApplicationContext();
        this.session = session;
        this.http = new OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build();
    }

    public void get(String path, JsonCallback callback) { execute("GET", path, null, true, callback); }
    public void getPublic(String path, JsonCallback callback) { execute("GET", path, null, false, callback); }
    public void post(String path, JSONObject body, JsonCallback callback) { execute("POST", path, body, true, callback); }
    public void postPublic(String path, JSONObject body, JsonCallback callback) { execute("POST", path, body, false, callback); }
    public void patch(String path, JSONObject body, JsonCallback callback) { execute("PATCH", path, body, true, callback); }
    public void delete(String path, JsonCallback callback) { execute("DELETE", path, null, true, callback); }

    public void execute(String method, String path, JSONObject body, boolean authorized, JsonCallback callback) {
        Request.Builder builder = baseRequest(path, authorized);
        RequestBody requestBody = body == null ? RequestBody.create(new byte[0], JSON) : RequestBody.create(body.toString(), JSON);
        switch (method) {
            case "GET" -> builder.get();
            case "POST" -> builder.post(requestBody);
            case "PATCH" -> builder.patch(requestBody);
            case "DELETE" -> builder.delete(body == null ? null : requestBody);
            default -> throw new IllegalArgumentException("Unsupported method");
        }
        http.newCall(builder.build()).enqueue(jsonCallback(callback));
    }

    public void upload(String path, Uri source, String filename, JsonCallback callback) {
        upload("POST", path, source, filename, callback);
    }

    public void uploadPut(String path, Uri source, String filename, JsonCallback callback) {
        upload("PUT", path, source, filename, callback);
    }

    public void uploadFile(String path, File source, String filename, String mimeType, JsonCallback callback) {
        if (!source.isFile()) {
            deliverError(callback, new ApiException("分享文件不存在", 0));
            return;
        }
        RequestBody fileBody = RequestBody.create(source, MediaType.get(
            mimeType == null || mimeType.isBlank() ? "application/octet-stream" : mimeType
        ));
        RequestBody multipart = new MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", filename, fileBody).build();
        Request request = baseRequest(path, true).post(multipart).build();
        http.newCall(request).enqueue(jsonCallback(callback));
    }

    private void upload(String method, String path, Uri source, String filename, JsonCallback callback) {
        files.execute(() -> {
            try (InputStream input = context.getContentResolver().openInputStream(source)) {
                if (input == null) throw new IOException("无法读取文件");
                byte[] content = readAll(input);
                String mime = context.getContentResolver().getType(source);
                RequestBody fileBody = RequestBody.create(content, MediaType.get(mime == null ? "application/octet-stream" : mime));
                RequestBody multipart = new MultipartBody.Builder().setType(MultipartBody.FORM)
                    .addFormDataPart("file", filename, fileBody).build();
                Request.Builder builder = baseRequest(path, true);
                Request request = ("PUT".equals(method) ? builder.put(multipart) : builder.post(multipart)).build();
                http.newCall(request).enqueue(jsonCallback(callback));
            } catch (Exception error) {
                deliverError(callback, new ApiException(error.getMessage() == null ? "文件读取失败" : error.getMessage(), 0));
            }
        });
    }

    public void download(String path, String filename, DownloadCallback callback) {
        http.newCall(baseRequest(path, true).get().build()).enqueue(new Callback() {
            @Override public void onFailure(Call call, IOException error) {
                deliverDownloadError(callback, new ApiException(error.getMessage(), 0));
            }

            @Override public void onResponse(Call call, Response response) {
                try (response) {
                    if (!response.isSuccessful() || response.body() == null) {
                        throw apiError(response);
                    }
                    Uri target;
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        ContentValues values = new ContentValues();
                        values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                        values.put(MediaStore.Downloads.MIME_TYPE, response.header("Content-Type", "application/octet-stream"));
                        values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/妙想之地");
                        target = context.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                        if (target == null) throw new IOException("无法创建下载文件");
                        try (InputStream input = response.body().byteStream(); OutputStream output = context.getContentResolver().openOutputStream(target)) {
                            if (output == null) throw new IOException("无法写入下载文件");
                            copy(input, output);
                        }
                    } else {
                        File directory = new File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "妙想之地");
                        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("无法创建下载目录");
                        File file = new File(directory, filename.replaceAll("[^A-Za-z0-9._\\-\\u4e00-\\u9fa5]", "_"));
                        try (InputStream input = response.body().byteStream(); OutputStream output = new FileOutputStream(file)) {
                            copy(input, output);
                        }
                        target = Uri.fromFile(file);
                    }
                    main.post(() -> callback.onSuccess(target));
                } catch (Exception error) {
                    deliverDownloadError(callback, error instanceof ApiException api ? api : new ApiException(error.getMessage(), 0));
                }
            }
        });
    }

    public void downloadToCache(String path, String filename, DownloadCallback callback) {
        http.newCall(baseRequest(path, true).get().build()).enqueue(new Callback() {
            @Override public void onFailure(Call call, IOException error) {
                deliverDownloadError(callback, new ApiException(error.getMessage(), 0));
            }

            @Override public void onResponse(Call call, Response response) {
                try (response) {
                    if (!response.isSuccessful() || response.body() == null) throw apiError(response);
                    File directory = new File(context.getCacheDir(), "shared-previews");
                    if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("无法创建分享缓存目录");
                    String safeName = filename.replaceAll("[^A-Za-z0-9._\\-\\u4e00-\\u9fa5]", "_");
                    if (safeName.isBlank()) safeName = "file";
                    File file = new File(directory, safeName);
                    try (InputStream input = response.body().byteStream(); OutputStream output = new FileOutputStream(file)) {
                        copy(input, output);
                    }
                    Uri target = FileProvider.getUriForFile(context, context.getPackageName() + ".updates", file);
                    main.post(() -> callback.onSuccess(target));
                } catch (Exception error) {
                    deliverDownloadError(callback, error instanceof ApiException api ? api : new ApiException(error.getMessage(), 0));
                }
            }
        });
    }

    public WebSocket websocket(String url, WebSocketListener listener) {
        Uri parsed = Uri.parse(url);
        if (!"wss".equalsIgnoreCase(parsed.getScheme())
            || !Uri.parse(AppConfig.ORIGIN).getHost().equalsIgnoreCase(parsed.getHost())) {
            throw new IllegalArgumentException("Only the configured public WSS origin is allowed");
        }
        return http.newWebSocket(new Request.Builder().url(url).header("User-Agent", userAgent()).build(), listener);
    }

    public JSONObject getSync(String path, String token) throws ApiException {
        Request request = baseRequest(path, token).get().build();
        try (Response response = http.newCall(request).execute()) {
            return parseEnvelope(response);
        } catch (ApiException error) {
            throw error;
        } catch (Exception error) {
            throw new ApiException(error.getMessage() == null ? "网络请求失败" : error.getMessage(), 0);
        }
    }

    private Request.Builder baseRequest(String path, boolean authorized) {
        return baseRequest(path, authorized ? session.token() : "");
    }

    private Request.Builder baseRequest(String path, String token) {
        String url = AppConfig.api(path);
        HttpUrl parsed = HttpUrl.parse(url);
        if (parsed == null || !parsed.isHttps()
            || !Uri.parse(AppConfig.ORIGIN).getHost().equalsIgnoreCase(parsed.host())) {
            throw new IllegalArgumentException("Invalid API origin");
        }
        Request.Builder builder = new Request.Builder().url(parsed)
            .header("Accept", "application/json")
            .header("User-Agent", userAgent());
        if (token != null && !token.isBlank()) builder.header("Authorization", "Bearer " + token);
        return builder;
    }

    private Callback jsonCallback(JsonCallback callback) {
        return new Callback() {
            @Override public void onFailure(Call call, IOException error) {
                deliverError(callback, new ApiException(error.getMessage() == null ? "网络连接失败" : error.getMessage(), 0));
            }

            @Override public void onResponse(Call call, Response response) {
                try (response) {
                    JSONObject data = parseEnvelope(response);
                    main.post(() -> callback.onSuccess(data));
                } catch (ApiException error) {
                    deliverError(callback, error);
                }
            }
        };
    }

    private JSONObject parseEnvelope(Response response) throws ApiException {
        try {
            ResponseBody body = response.body();
            String raw = body == null ? "" : body.string();
            JSONObject envelope = raw.isBlank() ? new JSONObject() : new JSONObject(raw);
            if (!response.isSuccessful() || !envelope.optBoolean("ok", false)) {
                String message = envelope.optJSONObject("error") == null ? "" : envelope.optJSONObject("error").optString("message");
                if (message.isBlank()) message = envelope.optString("detail");
                if (message.isBlank()) message = "请求失败 (" + response.code() + ")";
                throw new ApiException(message, response.code());
            }
            JSONObject data = envelope.optJSONObject("data");
            return data == null ? new JSONObject() : data;
        } catch (ApiException error) {
            throw error;
        } catch (Exception error) {
            throw new ApiException("服务器响应格式错误", response.code());
        }
    }

    private ApiException apiError(Response response) {
        try { return new ApiException(response.message(), response.code()); }
        catch (Exception ignored) { return new ApiException("下载失败", response.code()); }
    }

    private void deliverError(JsonCallback callback, ApiException error) {
        main.post(() -> callback.onError(error));
    }

    private void deliverDownloadError(DownloadCallback callback, ApiException error) {
        main.post(() -> callback.onError(error));
    }

    private String userAgent() {
        return "MiaoxiangZhiDi/" + AppConfig.VERSION + " Android";
    }

    private static byte[] readAll(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        copy(input, output);
        return output.toByteArray();
    }

    private static void copy(InputStream input, OutputStream output) throws IOException {
        byte[] buffer = new byte[16_384];
        int read;
        while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
    }
}
