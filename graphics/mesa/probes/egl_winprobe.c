// egl_winprobe: reproduce what HWUI does with a window surface, through Android's real EGL loader (libEGL.so ->
// ro.hardware.egl driver), on an AImageReader-backed ANativeWindow (a BufferQueue Surface like an app window).
// Run it from /data/local/tmp (system linker namespace, so libmediandk is visible). Lines are prefixed GBWIN.
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <android/native_window.h>
#include <media/NdkImage.h>
#include <media/NdkImageReader.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define P(...) do { printf("GBWIN " __VA_ARGS__); printf("\n"); fflush(stdout); } while (0)

static void dump_configs(EGLDisplay d) {
    EGLint n = 0;
    eglGetConfigs(d, NULL, 0, &n);
    EGLConfig *c = calloc(n, sizeof(*c));
    eglGetConfigs(d, c, n, &n);
    P("%d configs", n);
    for (int i = 0; i < n && i < 40; i++) {
        EGLint r, g, b, a, dep, st, vis, st_type, rt, cav, smp;
        eglGetConfigAttrib(d, c[i], EGL_RED_SIZE, &r); eglGetConfigAttrib(d, c[i], EGL_GREEN_SIZE, &g);
        eglGetConfigAttrib(d, c[i], EGL_BLUE_SIZE, &b); eglGetConfigAttrib(d, c[i], EGL_ALPHA_SIZE, &a);
        eglGetConfigAttrib(d, c[i], EGL_DEPTH_SIZE, &dep); eglGetConfigAttrib(d, c[i], EGL_STENCIL_SIZE, &st);
        eglGetConfigAttrib(d, c[i], EGL_NATIVE_VISUAL_ID, &vis); eglGetConfigAttrib(d, c[i], EGL_SURFACE_TYPE, &st_type);
        eglGetConfigAttrib(d, c[i], EGL_RENDERABLE_TYPE, &rt); eglGetConfigAttrib(d, c[i], EGL_CONFIG_CAVEAT, &cav);
        eglGetConfigAttrib(d, c[i], EGL_SAMPLES, &smp);
        P(" cfg%-2d rgba=%d%d%d%d d=%d s=%d ms=%d vis=0x%x surf=0x%x rend=0x%x caveat=0x%x", i, r, g, b, a, dep, st, smp, vis,
          st_type, rt, cav);
    }
    free(c);
}

static int run(EGLDisplay d, EGLContext ctx, EGLConfig cfg, int fmt, int stencil, int colorspace, const char *name) {
    AImageReader *rd = NULL;
    media_status_t ms = AImageReader_newWithUsage(320, 200, fmt,
        AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, 4, &rd);
    if (ms != AMEDIA_OK) { P("[%s] AImageReader_new failed %d", name, ms); return 1; }
    ANativeWindow *win = NULL;
    AImageReader_getWindow(rd, &win);
    EGLint attr[] = {EGL_GL_COLORSPACE_KHR, EGL_GL_COLORSPACE_LINEAR_KHR, EGL_NONE};
    EGLSurface s = eglCreateWindowSurface(d, cfg, win, colorspace ? attr : NULL);
    EGLint e = eglGetError();
    P("[%s] eglCreateWindowSurface -> %p err=0x%x (win fmt now %d)", name, s, e, ANativeWindow_getFormat(win));
    if (s == EGL_NO_SURFACE) { AImageReader_delete(rd); return 1; }
    if (!eglMakeCurrent(d, s, s, ctx)) { P("[%s] eglMakeCurrent FAILED err=0x%x", name, eglGetError()); return 1; }
    EGLint age = -1;
    eglQuerySurface(d, s, EGL_BUFFER_AGE_EXT, &age);
    P("[%s] current; buffer_age=%d err=0x%x renderer=%s", name, age, eglGetError(), glGetString(GL_RENDERER));
    for (int f = 0; f < 3; f++) {
        glClearColor(0.2f, 0.4f, 0.6f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        EGLBoolean ok = eglSwapBuffers(d, s);
        P("[%s] swap%d -> %d err=0x%x glerr=0x%x", name, f, ok, eglGetError(), glGetError());
    }
    usleep(200000);
    AImage *img = NULL;
    ms = AImageReader_acquireLatestImage(rd, &img);
    if (ms == AMEDIA_OK && img) {
        uint8_t *data = NULL; int len = 0;
        AImage_getPlaneData(img, 0, &data, &len);
        if (data) P("[%s] readback pixel0=[%d %d %d %d] (want ~51 102 153 255)", name, data[0], data[1], data[2], data[3]);
        AImage_delete(img);
    } else P("[%s] acquireLatestImage failed %d", name, ms);
    eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx);
    eglDestroySurface(d, s);
    AImageReader_delete(rd);
    (void)stencil;
    return 0;
}

int main(void) {
    EGLDisplay d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLint maj = 0, min = 0;
    if (!eglInitialize(d, &maj, &min)) { P("eglInitialize FAILED 0x%x", eglGetError()); return 1; }
    P("EGL %d.%d vendor=%s version=%s", maj, min, eglQueryString(d, EGL_VENDOR), eglQueryString(d, EGL_VERSION));
    dump_configs(d);
    for (int stencil = 0; stencil <= 8; stencil += 8) {
        // HWUI's load8BitsConfig
        EGLint want[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8,
                         EGL_ALPHA_SIZE, 8, EGL_DEPTH_SIZE, 0, EGL_CONFIG_CAVEAT, EGL_NONE, EGL_STENCIL_SIZE, stencil,
                         EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_NONE};
        EGLConfig cfg; EGLint n = 0;
        if (!eglChooseConfig(d, want, &cfg, 1, &n) || n < 1) { P("choose(stencil=%d) found none err=0x%x", stencil, eglGetError()); continue; }
        EGLint vis = 0; eglGetConfigAttrib(d, cfg, EGL_NATIVE_VISUAL_ID, &vis);
        P("choose(stencil=%d) -> vis=0x%x", stencil, vis);
        EGLint cattr[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
        EGLContext ctx = eglCreateContext(d, cfg, EGL_NO_CONTEXT, cattr);
        if (ctx == EGL_NO_CONTEXT) { P("createContext FAILED 0x%x", eglGetError()); continue; }
        char nm[64];
        snprintf(nm, sizeof nm, "s%d rgba cs=linear", stencil); run(d, ctx, cfg, AIMAGE_FORMAT_RGBA_8888, stencil, 1, nm);
        snprintf(nm, sizeof nm, "s%d rgba no-attr", stencil);   run(d, ctx, cfg, AIMAGE_FORMAT_RGBA_8888, stencil, 0, nm);
        snprintf(nm, sizeof nm, "s%d rgbx", stencil);           run(d, ctx, cfg, AIMAGE_FORMAT_RGBX_8888, stencil, 1, nm);
        eglDestroyContext(d, ctx);
    }
    P("done");
    return 0;
}
