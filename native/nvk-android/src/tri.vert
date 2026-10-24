#version 450
// Fixed triangle (Vulkan NDC, y down): base at y=+0.8 from x=-0.8..0.8, apex at (0,-0.8) -> 32% of the target.
void main() {
  const vec2 p[3] = vec2[3](vec2(-0.8, 0.8), vec2(0.8, 0.8), vec2(0.0, -0.8));
  gl_Position = vec4(p[gl_VertexIndex], 0.0, 1.0);
}
