#version 450
// Per-pixel unique, exactly representable pattern for the bit-exact gates (ahb-i915-readback, a17-swapchain-model):
// texel (x, y) = (x & 255, y & 255, (x ^ y) & 255, (x + 3y) & 255) as UNORM8. k/255 converts back to k exactly with
// round-to-nearest, so any layout/stride/tiling mistake shows up as a mismatch.
layout(location = 0) out vec4 o_color;
void main() {
  uint x = uint(gl_FragCoord.x), y = uint(gl_FragCoord.y);
  o_color = vec4(float(x & 255u), float(y & 255u), float((x ^ y) & 255u), float((x + 3u * y) & 255u)) / 255.0;
}
