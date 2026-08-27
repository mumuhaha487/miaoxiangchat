package cn.vmss.aichat;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.UUID;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class SecureSessionStore {
    private static final String PREFS = "yisuanshixue_native";
    private static final String KEY_ALIAS = "yisuanshixue_session_v1";
    private static final String TOKEN = "session_token";
    private static final String TRUST = "trusted_device_token";
    private static final String USER = "current_user";
    private static final String DEVICE_ID = "device_id";
    private static final String CURSOR = "notification_cursor";
    private static final String CURSOR_INITIALIZED = "notification_cursor_initialized";
    private final Context context;
    private final SharedPreferences preferences;

    public SecureSessionStore(Context context) {
        this.context = context.getApplicationContext();
        this.preferences = this.context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public synchronized String token() { return readEncrypted(TOKEN); }
    public synchronized String trustToken() { return readEncrypted(TRUST); }
    public synchronized String userJson() { return preferences.getString(USER, ""); }

    public synchronized void setToken(String token) {
        if (token == null || token.isBlank()) {
            clearSession();
            return;
        }
        writeEncrypted(TOKEN, token);
        NotificationWorker.schedule(context);
    }

    public synchronized void setTrustToken(String token) {
        writeEncrypted(TRUST, token);
    }

    public synchronized void saveSession(String token, String userJson, String deviceCredential) {
        writeEncrypted(TOKEN, token);
        if (deviceCredential != null && !deviceCredential.isBlank()) writeEncrypted(TRUST, deviceCredential);
        preferences.edit().putString(USER, userJson == null ? "" : userJson).apply();
        NotificationWorker.schedule(context);
    }

    public synchronized void clearSession() {
        preferences.edit().remove(TOKEN).remove(USER).remove(CURSOR).remove(CURSOR_INITIALIZED).apply();
        NotificationWorker.cancel(context);
    }

    public synchronized void clearTrust() {
        preferences.edit().remove(TRUST).apply();
    }

    public synchronized String deviceId() {
        String value = preferences.getString(DEVICE_ID, "");
        if (value == null || value.isBlank()) {
            value = UUID.randomUUID().toString();
            preferences.edit().putString(DEVICE_ID, value).apply();
        }
        return value;
    }

    public String deviceName() {
        String value = (Build.MANUFACTURER + " " + Build.MODEL).trim();
        return value.length() <= 120 ? value : value.substring(0, 120);
    }

    public synchronized long notificationCursor() { return preferences.getLong(CURSOR, 0L); }
    public synchronized boolean notificationCursorInitialized() { return preferences.getBoolean(CURSOR_INITIALIZED, false); }

    public synchronized void initializeNotificationCursor(long value) {
        preferences.edit().putLong(CURSOR, Math.max(notificationCursor(), value)).putBoolean(CURSOR_INITIALIZED, true).apply();
    }

    public synchronized boolean advanceNotificationCursor(long value) {
        if (value <= notificationCursor()) return false;
        preferences.edit().putLong(CURSOR, value).apply();
        return true;
    }

    private void writeEncrypted(String key, String value) {
        if (value == null || value.isBlank()) {
            preferences.edit().remove(key).apply();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, secretKey());
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            byte[] iv = cipher.getIV();
            byte[] payload = new byte[1 + iv.length + encrypted.length];
            payload[0] = (byte) iv.length;
            System.arraycopy(iv, 0, payload, 1, iv.length);
            System.arraycopy(encrypted, 0, payload, 1 + iv.length, encrypted.length);
            preferences.edit().putString(key, Base64.encodeToString(payload, Base64.NO_WRAP)).apply();
        } catch (Exception error) {
            throw new IllegalStateException("Unable to protect session", error);
        }
    }

    private String readEncrypted(String key) {
        String encoded = preferences.getString(key, "");
        if (encoded == null || encoded.isBlank()) return "";
        try {
            byte[] payload = Base64.decode(encoded, Base64.NO_WRAP);
            int ivLength = payload[0] & 0xff;
            if (ivLength < 12 || ivLength >= payload.length) throw new IllegalArgumentException("Invalid payload");
            byte[] iv = new byte[ivLength];
            byte[] encrypted = new byte[payload.length - ivLength - 1];
            System.arraycopy(payload, 1, iv, 0, ivLength);
            System.arraycopy(payload, 1 + ivLength, encrypted, 0, encrypted.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
        } catch (Exception error) {
            preferences.edit().remove(key).apply();
            return "";
        }
    }

    private SecretKey secretKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        if (keyStore.containsAlias(KEY_ALIAS)) {
            return ((KeyStore.SecretKeyEntry) keyStore.getEntry(KEY_ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
}
