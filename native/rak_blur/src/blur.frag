#version 330 core
in vec2 uv;
out vec4 result;
uniform sampler2D source;
uniform vec2 direction;
uniform int pairs;
uniform float weights[33];
uniform float offsets[33];
void main() {
    vec4 value = texture(source, uv) * weights[0];
    for (int i = 1; i <= pairs; ++i) {
        vec2 delta = direction * offsets[i];
        value += (texture(source, uv + delta) + texture(source, uv - delta)) * weights[i];
    }
    result = value;
}
