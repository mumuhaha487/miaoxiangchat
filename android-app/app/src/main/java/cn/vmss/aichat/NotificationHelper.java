package cn.vmss.aichat;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

final class NotificationHelper {
    private static final String CHANNEL_CHAT = "conversation";
    private static final String CHANNEL_AGENT = "agent";
    private static final String CHANNEL_AUTOMATION = "automation";
    private static final String CHANNEL_ATTENTION = "attention";
    private static final String CHANNEL_SYSTEM = "system";

    private NotificationHelper() {}

    static void createChannels(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(channel(
            CHANNEL_CHAT, "对话", "普通对话完成通知", NotificationManager.IMPORTANCE_DEFAULT
        ));
        manager.createNotificationChannel(channel(
            CHANNEL_AGENT, "Agent", "Agent 任务完成通知", NotificationManager.IMPORTANCE_DEFAULT
        ));
        manager.createNotificationChannel(channel(
            CHANNEL_AUTOMATION, "自动化", "定时任务执行完成通知", NotificationManager.IMPORTANCE_DEFAULT
        ));
        manager.createNotificationChannel(channel(
            CHANNEL_ATTENTION, "需要关注", "任务失败、取消或等待审批", NotificationManager.IMPORTANCE_HIGH
        ));
        manager.createNotificationChannel(channel(
            CHANNEL_SYSTEM, "系统", "账户与系统通知", NotificationManager.IMPORTANCE_LOW
        ));
    }

    private static NotificationChannel channel(String id, String name, String description, int importance) {
        NotificationChannel channel = new NotificationChannel(id, name, importance);
        channel.setDescription(description);
        channel.enableVibration(importance >= NotificationManager.IMPORTANCE_DEFAULT);
        return channel;
    }

    static void publish(Context context, long notificationId, String category, String title, String body) {
        if (!new SecureSessionStore(context).advanceNotificationCursor(notificationId)) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
            && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) return;

        Intent openApp = new Intent(context, MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            context,
            (int) (notificationId & 0x7fffffff),
            openApp,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        String safeTitle = title.isBlank() ? "妙想之地" : title;
        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, channelFor(category))
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(safeTitle)
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .setCategory(android.app.Notification.CATEGORY_MESSAGE)
            .setPriority(priorityFor(category));

        try {
            NotificationManagerCompat.from(context).notify((int) (notificationId & 0x7fffffff), builder.build());
        } catch (SecurityException ignored) {
            // Android may revoke notification permission while the worker is running.
        }
    }

    private static String channelFor(String category) {
        return switch (category) {
            case "chat_completed" -> CHANNEL_CHAT;
            case "agent_completed" -> CHANNEL_AGENT;
            case "schedule_completed" -> CHANNEL_AUTOMATION;
            case "task_failed", "approval_required" -> CHANNEL_ATTENTION;
            default -> CHANNEL_SYSTEM;
        };
    }

    private static int priorityFor(String category) {
        return switch (category) {
            case "task_failed", "approval_required" -> NotificationCompat.PRIORITY_HIGH;
            case "system" -> NotificationCompat.PRIORITY_LOW;
            default -> NotificationCompat.PRIORITY_DEFAULT;
        };
    }
}
