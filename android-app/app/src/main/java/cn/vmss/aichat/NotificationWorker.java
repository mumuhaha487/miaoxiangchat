package cn.vmss.aichat;

import android.content.Context;

import androidx.annotation.NonNull;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

public final class NotificationWorker extends Worker {
    private static final String PERIODIC_WORK = "aichat-notifications-periodic";
    private static final String IMMEDIATE_WORK = "aichat-notifications-immediate";

    public NotificationWorker(@NonNull Context context, @NonNull WorkerParameters parameters) {
        super(context, parameters);
    }

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build();

        OneTimeWorkRequest immediate = new OneTimeWorkRequest.Builder(NotificationWorker.class)
            .setConstraints(constraints)
            .build();
        WorkManager.getInstance(context).enqueueUniqueWork(
            IMMEDIATE_WORK,
            ExistingWorkPolicy.REPLACE,
            immediate
        );

        PeriodicWorkRequest periodic = new PeriodicWorkRequest.Builder(
            NotificationWorker.class,
            15,
            TimeUnit.MINUTES
        ).setConstraints(constraints).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_WORK,
            ExistingPeriodicWorkPolicy.UPDATE,
            periodic
        );
    }

    static void cancel(Context context) {
        WorkManager manager = WorkManager.getInstance(context);
        manager.cancelUniqueWork(IMMEDIATE_WORK);
        manager.cancelUniqueWork(PERIODIC_WORK);
    }

    @NonNull
    @Override
    public Result doWork() {
        SecureSessionStore session = new SecureSessionStore(getApplicationContext());
        String token = session.token();
        if (token.isBlank()) return Result.success();

        long current = session.notificationCursor();
        HttpURLConnection connection = null;
        try {
            URL url = new URL(AppConfig.api("/notifications?after=" + current + "&limit=100"));
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("GET");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setConnectTimeout(15_000);
            connection.setReadTimeout(20_000);

            int status = connection.getResponseCode();
            if (status == HttpURLConnection.HTTP_UNAUTHORIZED || status == HttpURLConnection.HTTP_FORBIDDEN) {
                session.clearSession();
                return Result.success();
            }
            if (status >= 500 || status == 429) return Result.retry();
            if (status != HttpURLConnection.HTTP_OK) return Result.success();

            JSONObject envelope = new JSONObject(readAll(connection.getInputStream()));
            JSONArray notifications = envelope.getJSONObject("data").getJSONArray("notifications");
            long maximum = current;
            boolean baselineOnly = !session.notificationCursorInitialized();
            for (int index = 0; index < notifications.length(); index++) {
                JSONObject notification = notifications.getJSONObject(index);
                long id = notification.getLong("id");
                maximum = Math.max(maximum, id);
                if (!baselineOnly) {
                    NotificationHelper.publish(
                        getApplicationContext(),
                        id,
                        notification.optString("category", "system"),
                        notification.optString("title", "妙想之地"),
                        notification.optString("body", "")
                    );
                }
            }
            if (baselineOnly) session.initializeNotificationCursor(maximum);
            return Result.success();
        } catch (Exception error) {
            return getRunAttemptCount() < 4 ? Result.retry() : Result.failure();
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static String readAll(InputStream stream) throws IOException {
        StringBuilder body = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
            new InputStreamReader(stream, StandardCharsets.UTF_8)
        )) {
            String line;
            while ((line = reader.readLine()) != null) body.append(line);
        }
        return body.toString();
    }
}
