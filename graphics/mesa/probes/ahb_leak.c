// ahb_leak: isolate where gralloc (vmwgfx dumb) buffer memory leaks. Allocates N 1280x800 RGBA AHardwareBuffers and
// releases them, optionally importing each into GLES first (EGLImage + texture, deleted before release).
// Usage: ahb_leak MODE N   MODE 0 = allocate/release only, 1 = also EGL-import + bind + delete, 2 = import, render into
// it (FBO clear + glFinish, which binds the surface to a MOB) + delete. Compare vmwgfx
// mob_ttm "used" pages before/after (each buffer = 1000 pages).
#define EGL_EGLEXT_PROTOTYPES 1
#define GL_GLEXT_PROTOTYPES 1
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <android/hardware_buffer.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    int mode = argc > 1 ? atoi(argv[1]) : 0, n = argc > 2 ? atoi(argv[2]) : 4;
    EGLDisplay d = EGL_NO_DISPLAY;
    if (mode) {
        d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
        eglInitialize(d, NULL, NULL);
        EGLint ca[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_NONE};
        EGLConfig cfg; EGLint nc; eglChooseConfig(d, ca, &cfg, 1, &nc);
        EGLint cx[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
        EGLContext c = eglCreateContext(d, cfg, EGL_NO_CONTEXT, cx);
        EGLint pa[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
        EGLSurface s = eglCreatePbufferSurface(d, cfg, pa);
        eglMakeCurrent(d, s, s, c);
    }
    for (int i = 0; i < n; i++) {
        AHardwareBuffer_Desc desc = {0};
        desc.width = 1280; desc.height = 800; desc.layers = 1; desc.format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM;
        desc.usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT;
        AHardwareBuffer *ahb = NULL;
        if (AHardwareBuffer_allocate(&desc, &ahb)) { printf("allocate failed\n"); return 1; }
        if (mode) {
            EGLClientBuffer cb = eglGetNativeClientBufferANDROID(ahb);
            EGLint attr[] = {EGL_IMAGE_PRESERVED_KHR, EGL_TRUE, EGL_NONE};
            EGLImageKHR img = eglCreateImageKHR(d, EGL_NO_CONTEXT, EGL_NATIVE_BUFFER_ANDROID, cb, attr);
            GLuint tex; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
            glEGLImageTargetTexture2DOES(GL_TEXTURE_2D, img);
            if (mode == 2) {
                GLuint fb; glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
                glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
                glClearColor(0.2f, 0.4f, 0.6f, 1.0f); glClear(GL_COLOR_BUFFER_BIT);
                glFinish();
                glBindFramebuffer(GL_FRAMEBUFFER, 0); glDeleteFramebuffers(1, &fb);
            }
            glFinish();
            glDeleteTextures(1, &tex);
            eglDestroyImageKHR(d, img);
            glFinish();
        }
        AHardwareBuffer_release(ahb);
    }
    printf("ahb_leak mode=%d n=%d done\n", mode, n);
    return 0;
}
