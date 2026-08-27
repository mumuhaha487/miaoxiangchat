package cn.vmss.aichat;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.Space;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

public final class UiKit {
    public static final int GRAPHITE = Color.rgb(48, 52, 56);
    public static final int MUTED = Color.rgb(116, 122, 126);
    public static final int ACCENT = Color.rgb(39, 125, 130);
    public static final int SURFACE_MUTED = Color.rgb(241, 242, 243);
    public static final int LINE = Color.rgb(217, 220, 222);
    public static final int DANGER = Color.rgb(179, 58, 58);

    private UiKit() {}

    public static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    public static TextView text(Context context, String value, float size, int color) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    public static TextView heading(Context context, String value) {
        TextView view = text(context, value, 19, GRAPHITE);
        view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        view.setPadding(0, dp(context, 8), 0, dp(context, 8));
        return view;
    }

    public static Button button(Context context, String label, boolean primary) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(13);
        button.setTextColor(primary ? Color.WHITE : GRAPHITE);
        button.setAllCaps(false);
        button.setMinHeight(dp(context, 42));
        button.setBackground(rounded(context, primary ? ACCENT : Color.WHITE, primary ? ACCENT : LINE, 7));
        button.setPadding(dp(context, 13), 0, dp(context, 13), 0);
        return button;
    }

    public static EditText input(Context context, String hint, boolean multiline) {
        EditText input = new EditText(context);
        input.setHint(hint);
        input.setTextSize(14);
        input.setTextColor(GRAPHITE);
        input.setHintTextColor(Color.rgb(145, 150, 154));
        input.setBackground(rounded(context, Color.WHITE, LINE, 7));
        input.setPadding(dp(context, 12), dp(context, 9), dp(context, 12), dp(context, 9));
        input.setSingleLine(!multiline);
        input.setInputType(multiline
            ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            : InputType.TYPE_CLASS_TEXT);
        if (multiline) {
            input.setGravity(Gravity.TOP | Gravity.START);
            input.setMinLines(3);
            input.setMaxLines(8);
        }
        return input;
    }

    public static GradientDrawable rounded(Context context, int fill, int stroke, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(context, radiusDp));
        drawable.setStroke(dp(context, 1), stroke);
        return drawable;
    }

    public static LinearLayout vertical(Context context, int paddingDp) {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(dp(context, paddingDp), dp(context, paddingDp), dp(context, paddingDp), dp(context, paddingDp));
        return layout;
    }

    public static Space space(Context context, int heightDp) {
        Space space = new Space(context);
        space.setLayoutParams(new LinearLayout.LayoutParams(1, dp(context, heightDp)));
        return space;
    }

    public static ProgressBar progress(Context context) {
        ProgressBar progress = new ProgressBar(context);
        progress.setIndeterminate(true);
        progress.getIndeterminateDrawable().setTint(ACCENT);
        return progress;
    }

    public static void toast(Context context, String message) {
        Toast.makeText(context, message, Toast.LENGTH_LONG).show();
    }

    public static LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    public static LinearLayout.LayoutParams weight(int weight) {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, weight);
    }
}
