"""GB_SHADER_CACHE: give Mesa's svga gallium driver a disk shader cache hook (run inside src/gallium/drivers/svga)."""
h = open("svga_screen.h").read()
if "disk_shader_cache" not in h:
    old = "      uint64_t num_resources;\n   } hud;\n};"
    assert h.count(old) == 1
    h = h.replace(old, "      uint64_t num_resources;\n   } hud;\n\n"
                  "   /* GB_SHADER_CACHE: lets st/mesa cache linked programs (EGL_ANDROID_blob_cache on Android) */\n"
                  "   struct disk_cache *disk_shader_cache;\n};")
    open("svga_screen.h", "w").write(h)

c = open("svga_screen.c").read()
if "GB_SHADER_CACHE" not in c:
    c = c.replace('#include "util/u_math.h"\n',
                  '#include "util/u_math.h"\n#include "util/disk_cache.h"\n#include "util/hex.h"\n'
                  '#include "util/mesa-sha1.h"\n', 1)
    c = c.replace("   svga_screen_cache_cleanup(svgascreen);\n",
                  "   svga_screen_cache_cleanup(svgascreen);\n   disk_cache_destroy(svgascreen->disk_shader_cache);\n", 1)
    fn = r'''
/* GB_SHADER_CACHE: give st/mesa a disk cache so linked GLSL programs (and GL program binaries, used by HWUI's own
 * shader cache) persist. On Android there is no writable cache dir, so it lives entirely in the EGL loader's
 * per-app blob cache (EGL_ANDROID_blob_cache, which is only exposed when this hook exists). */
static struct disk_cache *
svga_get_disk_shader_cache(struct pipe_screen *pscreen)
{
   return svga_screen(pscreen)->disk_shader_cache;
}

static void
svga_disk_cache_create(struct svga_screen *svgascreen)
{
   struct mesa_sha1 ctx;
   unsigned char sha1[20];
   char cache_id[20 * 2 + 1];

   _mesa_sha1_init(&ctx);
   if (!disk_cache_get_function_identifier(svga_disk_cache_create, &ctx))
      return;
   _mesa_sha1_final(&ctx, sha1);
   mesa_bytes_to_hex(cache_id, sha1, 20);
   svgascreen->disk_shader_cache = disk_cache_create("svga", cache_id, 0);
}

'''
    anchor = "\n/**\n * Create a new svga_screen object\n */"
    assert c.count(anchor) == 1
    c = c.replace(anchor, fn + "/**\n * Create a new svga_screen object\n */", 1)
    c = c.replace("   screen->destroy = svga_destroy_screen;\n",
                  "   screen->destroy = svga_destroy_screen;\n"
                  "   screen->get_disk_shader_cache = svga_get_disk_shader_cache;\n"
                  "   svga_disk_cache_create(svgascreen);\n", 1)
    assert "svga_disk_cache_create(svgascreen);" in c and "util/disk_cache.h" in c
    open("svga_screen.c", "w").write(c)
print("patched")
