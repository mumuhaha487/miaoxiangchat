package cn.vmss.aichat;

import android.net.Uri;

public final class AppConfig {
    public static final String ORIGIN = BuildConfig.PUBLIC_APP_ORIGIN.replaceAll("/+$", "");
    public static final String API_BASE = ORIGIN + "/api/v1";
    public static final String VERSION = BuildConfig.APP_VERSION_NAME;
    public static final int VERSION_CODE = BuildConfig.APP_VERSION_CODE;

    static {
        Uri origin = Uri.parse(ORIGIN);
        if (!"https".equals(origin.getScheme()) || origin.getHost() == null || origin.getHost().isBlank()) {
            throw new IllegalStateException("Public HTTPS origin is required");
        }
    }

    private AppConfig() {}

    public static String api(String path) {
        String normalized = path.startsWith("/") ? path : "/" + path;
        return API_BASE + normalized;
    }

    public static String websocket(String path) {
        String normalized = path.startsWith("/") ? path : "/" + path;
        Uri origin = Uri.parse(ORIGIN);
        return new Uri.Builder().scheme("wss").encodedAuthority(origin.getEncodedAuthority())
            .encodedPath("/api/v1" + normalized).build().toString();
    }

    public static boolean isTrustedHttps(Uri uri) {
        return uri != null
            && "https".equalsIgnoreCase(uri.getScheme())
            && Uri.parse(ORIGIN).getHost().equalsIgnoreCase(uri.getHost());
    }
}
