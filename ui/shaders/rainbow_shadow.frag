#version 440

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 itemSize;
    float cornerRadius;
    float shadowSpread;
    float phase;
    float shadowOpacity;
};

float sdRoundedRect(vec2 coord, vec2 halfSize, float radius) {
    vec2 cornerCoord = abs(coord) - (halfSize - vec2(radius));
    float outside = length(max(cornerCoord, vec2(0.0))) - radius;
    float inside = min(max(cornerCoord.x, cornerCoord.y), 0.0);
    return outside + inside;
}

vec3 rainbow(float t) {
    t = fract(t);
    vec3 c0 = vec3(0.573, 0.475, 0.933); // #9279ee (purple)
    vec3 c1 = vec3(0.898, 0.533, 0.729); // #e588ba (pink)
    vec3 c2 = vec3(0.922, 0.737, 0.467); // #ebbc77 (gold)
    vec3 c3 = vec3(0.400, 0.800, 0.643); // #66cca4 (green)
    vec3 c4 = vec3(0.345, 0.725, 0.937); // #58b9ef (blue)

    float f = t * 5.0;
    int idx = int(floor(f));
    float s = fract(f);

    if (idx == 0) return mix(c0, c1, s);
    if (idx == 1) return mix(c1, c2, s);
    if (idx == 2) return mix(c2, c3, s);
    if (idx == 3) return mix(c3, c4, s);
    return mix(c4, c0, s);
}

void main() {
    vec2 pixelPos = qt_TexCoord0 * itemSize;
    vec2 center = itemSize * 0.5;
    vec2 coord = pixelPos - center;
    vec2 innerHalf = center - vec2(shadowSpread);
    float r = min(cornerRadius, min(innerHalf.x, innerHalf.y));

    float sd = sdRoundedRect(coord, innerHalf, r);

    // Soft falloff from border outward and gentle fade inward under antialiased border
    const float innerFade = 3.0;
    if (sd < -innerFade || sd > shadowSpread) {
        fragColor = vec4(0.0);
        return;
    }

    float alpha = 1.0;
    if (sd > 0.0) {
        float norm = sd / shadowSpread;
        alpha = (1.0 - norm) * (1.0 - norm);
    } else {
        alpha = smoothstep(-innerFade, 0.0, sd);
    }

    // Angle around center for seamless rotating rainbow
    float angle = atan(coord.y, coord.x);
    float u = fract(angle / 6.283185307 + 0.5 + phase);
    vec3 col = rainbow(u);

    float finalAlpha = alpha * shadowOpacity * qt_Opacity;
    fragColor = vec4(col * finalAlpha, finalAlpha);
}
