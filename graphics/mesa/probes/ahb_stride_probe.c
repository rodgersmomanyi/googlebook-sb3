// ahb_stride_probe: does a CPU-written AHardwareBuffer sample correctly on the GPU (Mesa svga) for odd widths?
// For each size: allocate RGBA_8888 AHB (GPU_SAMPLED|CPU_WRITE), write pixel(x,y) = (x, y, 0x80, 0xff) at the
// reported stride, import via eglGetNativeClientBufferANDROID + EGLImage, draw it 1:1 into an FBO and read back.
// Prints stride and how many pixels came back wrong. Run from /data/local/tmp. Lines prefixed GBAHB.
#define EGL_EGLEXT_PROTOTYPES 1
#define GL_GLEXT_PROTOTYPES 1
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <android/hardware_buffer.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define P(...) do { printf("GBAHB " __VA_ARGS__); printf("\n"); fflush(stdout); } while (0)

static GLuint prog;
static GLuint mk(GLenum t, const char *s) { GLuint h = glCreateShader(t); glShaderSource(h, 1, &s, NULL); glCompileShader(h); return h; }

static void test(EGLDisplay d, int w, int h) {
    AHardwareBuffer_Desc desc = {0};
    desc.width = w; desc.height = h; desc.layers = 1; desc.format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM;
    desc.usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE | AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN;
    AHardwareBuffer *ahb = NULL;
    if (AHardwareBuffer_allocate(&desc, &ahb)) { P("%dx%d allocate FAILED", w, h); return; }
    AHardwareBuffer_Desc got; AHardwareBuffer_describe(ahb, &got);
    unsigned char *p = NULL;
    if (AHardwareBuffer_lock(ahb, AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN, -1, NULL, (void **)&p) || !p) { P("lock failed"); return; }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            unsigned char *px = p + ((size_t)y * got.stride + x) * 4;
            px[0] = (unsigned char)x; px[1] = (unsigned char)y; px[2] = 0x80; px[3] = 0xff;
        }
    AHardwareBuffer_unlock(ahb, NULL);

    EGLClientBuffer cb = eglGetNativeClientBufferANDROID(ahb);
    EGLint attr[] = {EGL_IMAGE_PRESERVED_KHR, EGL_TRUE, EGL_NONE};
    EGLImageKHR img = eglCreateImageKHR(d, EGL_NO_CONTEXT, EGL_NATIVE_BUFFER_ANDROID, cb, attr);
    if (img == EGL_NO_IMAGE_KHR) { P("%dx%d stride=%u eglCreateImage FAILED 0x%x", w, h, got.stride, eglGetError()); AHardwareBuffer_release(ahb); return; }
    GLuint tex; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC tgt = (PFNGLEGLIMAGETARGETTEXTURE2DOESPROC)eglGetProcAddress("glEGLImageTargetTexture2DOES");
    tgt(GL_TEXTURE_2D, img);

    GLuint ot, fb; glGenTextures(1, &ot); glBindTexture(GL_TEXTURE_2D, ot);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, ot, 0);
    glViewport(0, 0, w, h);
    glBindTexture(GL_TEXTURE_2D, tex);
    static const float q[] = {-1, -1, 0, 0, 1, -1, 1, 0, -1, 1, 0, 1, 1, 1, 1, 1};
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 16, q); glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 16, q + 2); glEnableVertexAttribArray(1);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    unsigned char *rb = malloc((size_t)w * h * 4);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rb);
    int bad = 0, firstx = -1, firsty = -1;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            unsigned char *px = rb + ((size_t)y * w + x) * 4;
            if (px[0] != (unsigned char)x || px[1] != (unsigned char)y || px[2] != 0x80) { if (!bad) { firstx = x; firsty = y; } bad++; }
        }
    unsigned char *f = rb + ((size_t)(firsty < 0 ? 0 : firsty) * w + (firstx < 0 ? 0 : firstx)) * 4;
    P("%4dx%-4d stride=%-4u -> %s bad=%d/%d first=(%d,%d) got=[%d %d %d %d] glerr=0x%x", w, h, got.stride,
      bad ? "CORRUPT" : "OK", bad, w * h, firstx, firsty, f[0], f[1], f[2], f[3], glGetError());
    if (bad) {   /* where did sample points come from? (r,g) = source (x,y) mod 256 */
        int pts[][2] = {{1, 1}, {10, 10}, {w / 4, h / 4}, {w / 2, h / 2}, {w - 2, h / 2}, {w / 2, h - 2}, {w - 2, h - 2}};
        char line[512]; int n = 0;
        for (int i = 0; i < 7; i++) {
            int x = pts[i][0], y = pts[i][1];
            unsigned char *q = rb + ((size_t)y * w + x) * 4;
            n += snprintf(line + n, sizeof line - n, " (%d,%d)<-(%d,%d)a%d", x, y, q[0], q[1], q[3]);
        }
        P("   map:%s", line);
    }
    free(rb);
    glDeleteFramebuffers(1, &fb); glDeleteTextures(1, &ot); glDeleteTextures(1, &tex);
    eglDestroyImageKHR(d, img); AHardwareBuffer_release(ahb);
}

int main(int argc, char **argv) {
    EGLDisplay d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    eglInitialize(d, NULL, NULL);
    EGLint ca[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
    EGLConfig cfg; EGLint n; eglChooseConfig(d, ca, &cfg, 1, &n);
    EGLint cx[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    EGLContext c = eglCreateContext(d, cfg, EGL_NO_CONTEXT, cx);
    EGLint pa[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
    EGLSurface s = eglCreatePbufferSurface(d, cfg, pa);
    if (!eglMakeCurrent(d, s, s, c)) eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c);
    P("renderer=%s", glGetString(GL_RENDERER));
    const char *vs = "attribute vec2 p; attribute vec2 t; varying vec2 v; void main(){ v=t; gl_Position=vec4(p,0.,1.); }";
    const char *fs = "precision mediump float; varying vec2 v; uniform sampler2D s; void main(){ gl_FragColor=texture2D(s,v); }";
    prog = glCreateProgram(); glAttachShader(prog, mk(GL_VERTEX_SHADER, vs)); glAttachShader(prog, mk(GL_FRAGMENT_SHADER, fs));
    glBindAttribLocation(prog, 0, "p"); glBindAttribLocation(prog, 1, "t"); glLinkProgram(prog); glUseProgram(prog);
    int sizes[][2] = {{78, 78}, {100, 100}, {96, 96}, {126, 126}, {128, 128}, {129, 129}, {192, 192}, {200, 120}, {256, 256}, {300, 200}, {640, 400}, {1280, 800}};
    for (unsigned i = 0; i < sizeof sizes / sizeof sizes[0]; i++) test(d, sizes[i][0], sizes[i][1]);
    P("done");
    return 0;
}
