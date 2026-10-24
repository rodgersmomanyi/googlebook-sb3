// egl_probe: go/no-go test for Mesa's gallium "svga" EGL/GLES driver on the Googlebook VM, without switching the
// system over. dlopen()s the Mesa EGL library directly (not via Android's EGL loader / ANGLE), brings up a
// surfaceless/pbuffer GLES context, renders and reads back a pixel, then tries to import gralloc AHardwareBuffers
// (RGBA_8888 / BGRA_8888) as EGLImages and render into them -- the path SurfaceFlinger and HWUI will need.
// Every line is prefixed GBEGL so it can be grepped out of the serial console.
#include <android/hardware_buffer.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

#define EGL_EGLEXT_PROTOTYPES 0
#define GL_GLEXT_PROTOTYPES 0
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>

#define P(...) do { printf("GBEGL " __VA_ARGS__); printf("\n"); fflush(stdout); } while (0)

static void *egl;
#define SYM(name) name##_fn = (__typeof__(name##_fn))dlsym(egl, #name)

static EGLDisplay (*eglGetDisplay_fn)(EGLNativeDisplayType);
static EGLBoolean (*eglInitialize_fn)(EGLDisplay, EGLint *, EGLint *);
static const char *(*eglQueryString_fn)(EGLDisplay, EGLint);
static EGLBoolean (*eglChooseConfig_fn)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *);
static EGLSurface (*eglCreatePbufferSurface_fn)(EGLDisplay, EGLConfig, const EGLint *);
static EGLContext (*eglCreateContext_fn)(EGLDisplay, EGLConfig, EGLContext, const EGLint *);
static EGLBoolean (*eglMakeCurrent_fn)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);
static EGLint (*eglGetError_fn)(void);
static __eglMustCastToProperFunctionPointerType (*eglGetProcAddress_fn)(const char *);

static PFNGLGETSTRINGPROC glGetString_p;
static PFNGLCLEARCOLORPROC glClearColor_p;
static PFNGLCLEARPROC glClear_p;
static PFNGLREADPIXELSPROC glReadPixels_p;
static PFNGLFINISHPROC glFinish_p;
static PFNGLGENTEXTURESPROC glGenTextures_p;
static PFNGLBINDTEXTUREPROC glBindTexture_p;
static PFNGLGENFRAMEBUFFERSPROC glGenFramebuffers_p;
static PFNGLBINDFRAMEBUFFERPROC glBindFramebuffer_p;
static PFNGLFRAMEBUFFERTEXTURE2DPROC glFramebufferTexture2D_p;
static PFNGLCHECKFRAMEBUFFERSTATUSPROC glCheckFramebufferStatus_p;
static PFNGLGETERRORPROC glGetError_p;
static PFNGLEGLIMAGETARGETTEXTURE2DOESPROC glEGLImageTargetTexture2DOES_p;
static PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC eglGetNativeClientBufferANDROID_p;
static PFNEGLCREATEIMAGEKHRPROC eglCreateImageKHR_p;

#define GLP(name) name##_p = (__typeof__(name##_p))eglGetProcAddress_fn(#name)

static void test_ahb(EGLDisplay dpy, uint32_t fmt, const char *name) {
    AHardwareBuffer_Desc d = {0};
    d.width = 256; d.height = 256; d.layers = 1; d.format = fmt;
    d.usage = AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
              AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN;
    AHardwareBuffer *ahb = NULL;
    int r = AHardwareBuffer_allocate(&d, &ahb);
    if (r != 0 || !ahb) { P("AHB %s allocate FAILED r=%d", name, r); return; }
    // eglGetNativeClientBufferANDROID is implemented by Android's EGL wrapper (libEGL.so), not by the driver, and
    // this probe talks to Mesa directly: do what the wrapper does (AHardwareBuffer -> ANativeWindowBuffer).
    static void *(*ahb_to_anwb)(AHardwareBuffer *);
    if (!ahb_to_anwb) ahb_to_anwb = (void *(*)(AHardwareBuffer *))dlsym(RTLD_DEFAULT, "AHardwareBuffer_to_ANativeWindowBuffer");
    // AHardwareBuffer_to_ANativeWindowBuffer isn't exported to the vendor namespace; it is only a cast in AOSP: an
    // AHardwareBuffer is a GraphicBuffer, whose first base (ANativeObjectBase<ANativeWindowBuffer,...>) is the
    // ANativeWindowBuffer, so the same pointer value is the EGL_NATIVE_BUFFER_ANDROID client buffer.
    // A plain cast is wrong (Itanium ABI puts the dynamic RefBase first, so the ANativeWindowBuffer is NOT at offset
    // 0). Find it by its android_native_base_t magic: ANDROID_NATIVE_BUFFER_MAGIC = '_bfr' = 0x5f626672.
    EGLClientBuffer cb = NULL;
    int off = -1;
    if (ahb_to_anwb) {
        cb = (EGLClientBuffer)ahb_to_anwb(ahb);
    } else {
        const unsigned char *base = (const unsigned char *)ahb;
        for (int o = 0; o <= 256; o += 4)
            if (*(const uint32_t *)(base + o) == 0x5f626672u) { off = o; cb = (EGLClientBuffer)(base + o); break; }
    }
    P("AHB %s client buffer via %s (magic offset %d)", name,
      ahb_to_anwb ? "AHardwareBuffer_to_ANativeWindowBuffer" : "magic scan", off);
    if (!cb) { P("AHB %s no ANativeWindowBuffer found", name); AHardwareBuffer_release(ahb); return; }
    const EGLint attrs[] = { EGL_IMAGE_PRESERVED_KHR, EGL_TRUE, EGL_NONE };
    EGLImageKHR img = cb ? eglCreateImageKHR_p(dpy, EGL_NO_CONTEXT, EGL_NATIVE_BUFFER_ANDROID, cb, attrs)
                         : EGL_NO_IMAGE_KHR;
    if (img == EGL_NO_IMAGE_KHR) {
        P("AHB %s eglCreateImageKHR FAILED egl_err=0x%x (cb=%p)", name, eglGetError_fn(), cb);
        AHardwareBuffer_release(ahb); return;
    }
    GLuint tex = 0, fbo = 0;
    glGenTextures_p(1, &tex); glBindTexture_p(GL_TEXTURE_2D, tex);
    glEGLImageTargetTexture2DOES_p(GL_TEXTURE_2D, (GLeglImageOES)img);
    GLenum ge = glGetError_p();
    glGenFramebuffers_p(1, &fbo); glBindFramebuffer_p(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D_p(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    GLenum st = glCheckFramebufferStatus_p(GL_FRAMEBUFFER);
    glClearColor_p(1.0f, 0.5f, 0.25f, 1.0f); glClear_p(GL_COLOR_BUFFER_BIT); glFinish_p();
    void *ptr = NULL;
    r = AHardwareBuffer_lock(ahb, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, -1, NULL, &ptr);
    unsigned char *px = (unsigned char *)ptr;
    if (r == 0 && px)
        P("AHB %s import OK teximage_glerr=0x%x fbo=0x%x cpu_pixel=[%u %u %u %u] (want ~255 128 64 255 for RGBA)",
          name, ge, st, px[0], px[1], px[2], px[3]);
    else
        P("AHB %s import OK but CPU lock failed r=%d fbo=0x%x", name, r, st);
    if (r == 0) AHardwareBuffer_unlock(ahb, NULL);
    glBindFramebuffer_p(GL_FRAMEBUFFER, 0);
    AHardwareBuffer_release(ahb);
}

int main(int argc, char **argv) {
    const char *lib = argc > 1 ? argv[1] : "/vendor/lib64/egl/libEGL_mesa.so";
    egl = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
    if (!egl) { P("dlopen %s FAILED: %s", lib, dlerror()); return 1; }
    P("dlopen %s OK", lib);
    SYM(eglGetDisplay); SYM(eglInitialize); SYM(eglQueryString); SYM(eglChooseConfig);
    SYM(eglCreatePbufferSurface); SYM(eglCreateContext); SYM(eglMakeCurrent); SYM(eglGetError);
    SYM(eglGetProcAddress);
    if (!eglGetDisplay_fn || !eglInitialize_fn || !eglGetProcAddress_fn) { P("missing core EGL symbols"); return 1; }

    EGLDisplay dpy = eglGetDisplay_fn(EGL_DEFAULT_DISPLAY);
    EGLint maj = 0, min = 0;
    if (!eglInitialize_fn(dpy, &maj, &min)) { P("eglInitialize FAILED err=0x%x", eglGetError_fn()); return 2; }
    P("eglInitialize OK %d.%d vendor=[%s] version=[%s] apis=[%s]", maj, min,
      eglQueryString_fn(dpy, EGL_VENDOR), eglQueryString_fn(dpy, EGL_VERSION), eglQueryString_fn(dpy, EGL_CLIENT_APIS));
    const char *eext = eglQueryString_fn(dpy, EGL_EXTENSIONS);
    for (size_t off = 0, len = eext ? strlen(eext) : 0; off < len; off += 300)
        P("EGL_EXTENSIONS[%zu]=%.300s", off / 300, eext + off);
    P("has surfaceless_context=%d wait_sync=%d native_fence_sync=%d image_native_buffer=%d",
      eext && strstr(eext, "EGL_KHR_surfaceless_context") != NULL, eext && strstr(eext, "EGL_KHR_wait_sync") != NULL,
      eext && strstr(eext, "EGL_ANDROID_native_fence_sync") != NULL, eext && strstr(eext, "EGL_ANDROID_image_native_buffer") != NULL);

    const EGLint cfg_attr[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
                                EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE };
    EGLConfig cfg; EGLint n = 0;
    if (!eglChooseConfig_fn(dpy, cfg_attr, &cfg, 1, &n) || n < 1) { P("eglChooseConfig FAILED n=%d err=0x%x", n, eglGetError_fn()); return 3; }
    const EGLint pb_attr[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
    EGLSurface pb = eglCreatePbufferSurface_fn(dpy, cfg, pb_attr);
    EGLContext ctx = EGL_NO_CONTEXT;
    for (int ver = 3; ver >= 2 && ctx == EGL_NO_CONTEXT; ver--) {
        const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, ver, EGL_NONE };
        ctx = eglCreateContext_fn(dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
    }
    if (ctx == EGL_NO_CONTEXT || !eglMakeCurrent_fn(dpy, pb, pb, ctx)) { P("context/makeCurrent FAILED err=0x%x", eglGetError_fn()); return 4; }

    GLP(glGetString); GLP(glClearColor); GLP(glClear); GLP(glReadPixels); GLP(glFinish); GLP(glGenTextures);
    GLP(glBindTexture); GLP(glGenFramebuffers); GLP(glBindFramebuffer); GLP(glFramebufferTexture2D);
    GLP(glCheckFramebufferStatus); GLP(glGetError); GLP(glEGLImageTargetTexture2DOES);
    eglGetNativeClientBufferANDROID_p = (PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC)eglGetProcAddress_fn("eglGetNativeClientBufferANDROID");
    eglCreateImageKHR_p = (PFNEGLCREATEIMAGEKHRPROC)eglGetProcAddress_fn("eglCreateImageKHR");
    P("GL_VENDOR=[%s] GL_RENDERER=[%s] GL_VERSION=[%s]", glGetString_p(GL_VENDOR), glGetString_p(GL_RENDERER), glGetString_p(GL_VERSION));
    const char *ext = (const char *)glGetString_p(GL_EXTENSIONS);
    P("GL_EXTENSIONS(len=%zu) EGL_image_external=%d", ext ? strlen(ext) : 0, ext && strstr(ext, "GL_OES_EGL_image_external") != NULL);

    glClearColor_p(0.2f, 0.4f, 0.6f, 1.0f); glClear_p(GL_COLOR_BUFFER_BIT);
    unsigned char px[4] = {0};
    glReadPixels_p(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px); glFinish_p();
    P("pbuffer render+readback pixel=[%u %u %u %u] (want ~51 102 153 255) glerr=0x%x", px[0], px[1], px[2], px[3], glGetError_p());

    // Isolate GPU rendering from window-system buffers: render into a plain GL texture through an FBO.
    {
        GLuint t = 0, f = 0;
        PFNGLTEXIMAGE2DPROC glTexImage2D_p = (PFNGLTEXIMAGE2DPROC)eglGetProcAddress_fn("glTexImage2D");
        glGenTextures_p(1, &t); glBindTexture_p(GL_TEXTURE_2D, t);
        glTexImage2D_p(GL_TEXTURE_2D, 0, GL_RGBA, 64, 64, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        glGenFramebuffers_p(1, &f); glBindFramebuffer_p(GL_FRAMEBUFFER, f);
        glFramebufferTexture2D_p(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, t, 0);
        GLenum fst = glCheckFramebufferStatus_p(GL_FRAMEBUFFER);
        glClearColor_p(0.2f, 0.4f, 0.6f, 1.0f); glClear_p(GL_COLOR_BUFFER_BIT);
        unsigned char q[4] = {0};
        glReadPixels_p(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, q); glFinish_p();
        P("FBO(texture) render+readback status=0x%x pixel=[%u %u %u %u] (want ~51 102 153 255) glerr=0x%x",
          fst, q[0], q[1], q[2], q[3], glGetError_p());
        glBindFramebuffer_p(GL_FRAMEBUFFER, 0);
    }

    test_ahb(dpy, AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM, "RGBA_8888");
    test_ahb(dpy, 5 /* HAL_PIXEL_FORMAT_BGRA_8888 */, "BGRA_8888");
    P("DONE");
    return 0;
}
