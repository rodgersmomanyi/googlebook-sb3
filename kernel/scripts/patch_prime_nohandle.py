"""GB_PRIME_NOHANDLE: stop vmw_buffer_prime_to_surface_base() from leaking a GEM handle per imported dma-buf.

Run on builder2 in /build/gki/common/drivers/gpu/drm/vmwgfx (python3 patch_prime_nohandle.py).
"""
p = "vmwgfx_surface.c"
s = open(p).read()
old = """	struct vmw_user_surface *user_srf;
	int ret;

	ret = drm_gem_prime_fd_to_handle(&dev_priv->drm, file_priv, fd, handle);
	if (ret) {
		drm_warn(&dev_priv->drm,
			 "Wasn't able to find user buffer for fd = %u.\\n", fd);
		return ret;
	}

	ret = vmw_user_bo_lookup(file_priv, *handle, &bo);
	if (ret) {
		drm_warn(&dev_priv->drm,
			 "Wasn't able to lookup user buffer for handle = %u.\\n", *handle);
		return ret;
	}
"""
new = """	struct vmw_user_surface *user_srf;
	struct dma_buf *dma_buf;
	int ret;

	/*
	 * GB_PRIME_NOHANDLE: a dma-buf exported by this device (every gralloc dumb buffer) maps straight to its
	 * BO, so take the BO from it. drm_gem_prime_fd_to_handle() created a GEM handle in the importing file
	 * that nothing ever deleted (userspace never sees it), so a long-lived importer such as SurfaceFlinger
	 * kept every buffer it ever imported alive, with its pages and MOB, until it exited.
	 */
	dma_buf = dma_buf_get(fd);
	if (IS_ERR(dma_buf))
		return PTR_ERR(dma_buf);
	if (drm_gem_is_prime_exported_dma_buf(&dev_priv->drm, dma_buf)) {
		struct drm_gem_object *obj = dma_buf->priv;

		drm_gem_object_get(obj);
		dma_buf_put(dma_buf);
		bo = to_vmw_bo(obj);
		*handle = 0;
	} else {
		dma_buf_put(dma_buf);
		ret = drm_gem_prime_fd_to_handle(&dev_priv->drm, file_priv, fd, handle);
		if (ret) {
			drm_warn(&dev_priv->drm,
				 "Wasn't able to find user buffer for fd = %u.\\n", fd);
			return ret;
		}

		ret = vmw_user_bo_lookup(file_priv, *handle, &bo);
		if (ret) {
			drm_warn(&dev_priv->drm,
				 "Wasn't able to lookup user buffer for handle = %u.\\n", *handle);
			return ret;
		}
	}
"""
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new)
inc = "#include <drm/ttm/ttm_placement.h>"
assert s.count(inc) == 1
if "#include <linux/dma-buf.h>" not in s:
    s = s.replace(inc, inc + "\n#include <drm/drm_prime.h>\n#include <linux/dma-buf.h>", 1)
open(p, "w").write(s)
print("patched GB_PRIME_NOHANDLE")
