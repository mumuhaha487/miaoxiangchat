package cn.vmss.aichat;

final class NativeMarker {
    static {
        System.loadLibrary("aichat_native");
    }

    private NativeMarker() {}

    static native String version();
}
