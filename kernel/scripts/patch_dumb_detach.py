"""GB_DUMB_DETACH: detach a dumb buffer's surface when the buffer's last GEM reference drops, not at TTM destroy.

A dumb surface points back at its BO through res->guest_memory_bo, which is an uncounted reference. Upstream only
clears it in vmw_bo_free(), but a BO that is still busy on the GPU when released goes through TTM's delayed destroy, so
vmw_bo_free() runs later from a worker. Until then the surface (still alive through an importer's TTM reference or a
context's view/binding references) leads to a BO whose GEM refcount is already 0. A later execbuf validating that
surface did vmw_validation_add_bo() -> GEM get on 0 ("refcount_t: addition on 0; use-after-free"), then an underflow,
then vmw_bo_free() warned "GEM refcount != 0" (vmwgfx_bo.c:45) from ttm_bo_delayed_delete. Doing the same detach as
vmw_bo_free() at GEM-free time closes the window; a surface validated after that just gets a fresh backing buffer.

Run on builder2 in /build/gki/common/drivers/gpu/drm/vmwgfx (python3 patch_dumb_detach.py). Needs GB_DUMB_BASE_OWNS.
"""
bo_c, bo_h, gem_c = "vmwgfx_bo.c", "vmwgfx_bo.h", "vmwgfx_gem.c"

s = open(bo_c).read()
old_free = """static void vmw_bo_free(struct ttm_buffer_object *bo)
{
	struct vmw_resource *res;
	struct vmw_bo *vbo = to_vmw_bo(&bo->base);

	WARN_ON(kref_read(&vbo->tbo.base.refcount) != 0);
	vmw_bo_unmap(vbo);

	xa_destroy(&vbo->detached_resources);
	WARN_ON(vbo->is_dumb && !vbo->dumb_surface);
	if (vbo->is_dumb && vbo->dumb_surface) {
		res = &vbo->dumb_surface->res;
		WARN_ON(vbo != res->guest_memory_bo);
		WARN_ON(!res->guest_memory_bo);
		if (res->guest_memory_bo) {
			/* Reserve and switch the backing mob. */
			mutex_lock(&res->dev_priv->cmdbuf_mutex);
			(void)vmw_resource_reserve(res, false, true);
			vmw_resource_mob_detach(res);
			if (res->dirty)
				res->func->dirty_free(res);
			if (res->coherent)
				vmw_bo_dirty_release(res->guest_memory_bo);
			res->guest_memory_bo = NULL;
			res->guest_memory_offset = 0;
			vmw_resource_unreserve(res, true, false, false, NULL,
					       0);
			mutex_unlock(&res->dev_priv->cmdbuf_mutex);
		}
		/* GB_DUMB_IMPORT: drop the BO's handle-object reference before the surface it lives in */
		if (vbo->dumb_base)
			ttm_base_object_unref(&vbo->dumb_base);
		vmw_surface_unreference(&vbo->dumb_surface);
	}
	WARN_ON(!RB_EMPTY_ROOT(&vbo->res_tree));"""
new_free = """/**
 * vmw_bo_detach_dumb_surface - Cut a dumb buffer loose from its surface
 *
 * @vbo: The dumb buffer object. No-op for other buffers or when already detached.
 *
 * GB_DUMB_DETACH: called when the last GEM reference drops (vmw_gem_object_free), and again from vmw_bo_free() as a
 * no-op fallback. The surface may outlive the buffer (an importer's TTM reference, a context's bindings), so it must
 * stop pointing at it before TTM's possibly delayed destroy; its next validation allocates a fresh backing buffer.
 */
void vmw_bo_detach_dumb_surface(struct vmw_bo *vbo)
{
	struct vmw_resource *res;

	if (!vbo->is_dumb || !vbo->dumb_surface)
		return;
	res = &vbo->dumb_surface->res;
	WARN_ON(vbo != res->guest_memory_bo);
	WARN_ON(!res->guest_memory_bo);
	if (res->guest_memory_bo) {
		/* Reserve and switch the backing mob. */
		mutex_lock(&res->dev_priv->cmdbuf_mutex);
		(void)vmw_resource_reserve(res, false, true);
		vmw_resource_mob_detach(res);
		if (res->dirty)
			res->func->dirty_free(res);
		if (res->coherent)
			vmw_bo_dirty_release(res->guest_memory_bo);
		res->guest_memory_bo = NULL;
		res->guest_memory_offset = 0;
		vmw_resource_unreserve(res, true, false, false, NULL,
				       0);
		mutex_unlock(&res->dev_priv->cmdbuf_mutex);
	}
	/* GB_DUMB_IMPORT: drop the BO's handle-object reference before the surface it lives in */
	if (vbo->dumb_base)
		ttm_base_object_unref(&vbo->dumb_base);
	vmw_surface_unreference(&vbo->dumb_surface);
}

static void vmw_bo_free(struct ttm_buffer_object *bo)
{
	struct vmw_bo *vbo = to_vmw_bo(&bo->base);

	WARN_ON(kref_read(&vbo->tbo.base.refcount) != 0);
	vmw_bo_unmap(vbo);

	xa_destroy(&vbo->detached_resources);
	/* Normally already done at GEM free (GB_DUMB_DETACH); dumb_surface is NULL then. */
	vmw_bo_detach_dumb_surface(vbo);
	WARN_ON(!RB_EMPTY_ROOT(&vbo->res_tree));"""
assert s.count(old_free) == 1, ("vmw_bo_free", s.count(old_free))
s = s.replace(old_free, new_free)
open(bo_c, "w").write(s)

h = open(bo_h).read()
anchor = "struct vmw_surface *vmw_bo_surface(struct vmw_bo *vbo);"
assert h.count(anchor) == 1
h = h.replace(anchor, anchor + "\nvoid vmw_bo_detach_dumb_surface(struct vmw_bo *vbo);", 1)
open(bo_h, "w").write(h)

g = open(gem_c).read()
old_gem = """static void vmw_gem_object_free(struct drm_gem_object *gobj)
{
	struct ttm_buffer_object *bo = drm_gem_ttm_of_gem(gobj);
	if (bo)
		ttm_bo_put(bo);
}"""
new_gem = """static void vmw_gem_object_free(struct drm_gem_object *gobj)
{
	struct ttm_buffer_object *bo = drm_gem_ttm_of_gem(gobj);

	if (bo) {
		/*
		 * GB_DUMB_DETACH: the GEM refcount is 0 now, but a busy BO is destroyed later by TTM's delayed-delete
		 * worker. Detach a dumb buffer's surface first, so nothing can reach this BO through it in between.
		 */
		vmw_bo_detach_dumb_surface(to_vmw_bo(gobj));
		ttm_bo_put(bo);
	}
}"""
assert g.count(old_gem) == 1, ("vmw_gem_object_free", g.count(old_gem))
g = g.replace(old_gem, new_gem)
open(gem_c, "w").write(g)
print("patched GB_DUMB_DETACH")
