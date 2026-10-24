"""GB_DUMB_BASE_OWNS: fix the use-after-free in GB_DUMB_IMPORT, plus a base-ref leak on an import error path.

A vmw_user_surface is one allocation holding both the TTM base object (prime.base) and the surface resource. Upstream
dumb buffers set prime.base.refcount_release = NULL (the base gave its resource reference to the BO) because the base
died right after creation. GB_DUMB_IMPORT keeps the base alive for the BO's lifetime so other processes can
REF_EXT-import the buffer, but with refcount_release still NULL an importer's TTM ref could outlive the surface memory
(freed when the BO dies): UNREF_SURFACE or file close then touched freed memory. Now the base keeps its own resource
reference (normal release function) and the BO owns the lookup's resource reference, so the memory lives until both
are gone. vmw_bo_free already clears res->guest_memory_bo before dropping dumb_base, so the base release's
"dumb buffers own the resource" WARN_ON cannot fire.

Run on builder2 in /build/gki/common/drivers/gpu/drm/vmwgfx (python3 patch_dumb_base_owns.py).
"""
p = "vmwgfx_surface.c"
s = open(p).read()

old = """	vbo = res->guest_memory_bo;
	vbo->is_dumb = true;
	vbo->dumb_surface = vmw_res_to_srf(res);
	drm_gem_object_put(&vbo->tbo.base);
	/*
	 * Unset the user surface dtor since this in not actually exposed
	 * to userspace. The suface is owned via the dumb_buffer's GEM handle
	 */
	struct vmw_user_surface *usurf = container_of(vbo->dumb_surface,
						struct vmw_user_surface, srf);
	usurf->prime.base.refcount_release = NULL;
	/*
	 * GB_DUMB_IMPORT: keep the surface's TTM handle object alive for the BO's lifetime (the BO, not the
	 * creating file, holds this reference) so another process can re-import the buffer's dma-buf as a 3D
	 * surface with DRM_VMW_GB_SURFACE_REF_EXT (Mesa svga importing gralloc buffers). Otherwise the object
	 * dies at the ttm_ref_object_base_unref() below and every import fails "User surface fd .. is null".
	 */
	if (kref_get_unless_zero(&usurf->prime.base.refcount))
		vbo->dumb_base = &usurf->prime.base;
err:"""
new = """	vbo = res->guest_memory_bo;
	vbo->is_dumb = true;
	vbo->dumb_surface = vmw_res_to_srf(res);
	/*
	 * GB_DUMB_BASE_OWNS: the BO keeps the lookup's resource reference (dropped in vmw_bo_free), and the base
	 * keeps its own one (its normal refcount_release is left in place). Upstream instead set the release to
	 * NULL and handed the base's reference to the BO, which is only safe while nothing outlives the BO.
	 */
	res = NULL;
	drm_gem_object_put(&vbo->tbo.base);
	struct vmw_user_surface *usurf = container_of(vbo->dumb_surface,
						struct vmw_user_surface, srf);
	/*
	 * GB_DUMB_IMPORT: keep the surface's TTM handle object alive for the BO's lifetime (the BO, not the
	 * creating file, holds this reference) so another process can re-import the buffer's dma-buf as a 3D
	 * surface with DRM_VMW_GB_SURFACE_REF_EXT (Mesa svga importing gralloc buffers). Otherwise the object
	 * dies at the ttm_ref_object_base_unref() below and every import fails "User surface fd .. is null".
	 * An importer's reference can outlive the BO; the base's own resource reference keeps the memory valid.
	 */
	if (kref_get_unless_zero(&usurf->prime.base.refcount))
		vbo->dumb_base = &usurf->prime.base;
err:"""
assert s.count(old) == 1, ("dumb_create", s.count(old))
s = s.replace(old, new)

old2 = """	base = &user_srf->prime.base;
	ret = ttm_ref_object_add(tfile, base, NULL, false);
	if (ret) {
		drm_warn(&dev_priv->drm,
			 "Couldn't add an object ref for the buffer (%d).\\n", *handle);
		goto out;
	}"""
new2 = """	base = &user_srf->prime.base;
	ret = ttm_ref_object_add(tfile, base, NULL, false);
	if (ret) {
		drm_warn(&dev_priv->drm,
			 "Couldn't add an object ref for the buffer (%d).\\n", *handle);
		/* GB_DUMB_BASE_OWNS: drop the lookup's base reference too */
		ttm_base_object_unref(&base);
		goto out;
	}"""
assert s.count(old2) == 1, ("prime_to_surface_base", s.count(old2))
s = s.replace(old2, new2)
open(p, "w").write(s)
print("patched GB_DUMB_BASE_OWNS")
