#include <jni.h>

extern "C" JNIEXPORT jstring JNICALL
Java_cn_vmss_aichat_NativeMarker_version(JNIEnv *env, jclass) {
    return env->NewStringUTF("MiaoxiangZhiDi web-parity");
}
