/*
 * Copyright 2025 Kyant
 * SPDX-License-Identifier: Apache-2.0
 *
 * Adapted from AndroidLiquidGlass-kmp / Backdrop:
 * backdrop/src/commonMain/kotlin/com/kyant/backdrop/internal/Shaders.kt
 * RoundedRectSDF and RoundedRectRefractionShaderString.
 *
 * Modified for Qt Quick: AGSL -> GLSL, normalized padded texture coordinates,
 * a shared corner radius, safe zero-length normals, and Qt layer uniforms.
 * See licenses/LICENSE-APACHE-2.0 for the license text.
 */
#version 440

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 glassSize;
    vec2 sampleSize;
    vec2 sampleInset;
    float cornerRadius;
    float refractionHeight;
    float refractionAmount;
};
layout(binding = 1) uniform sampler2D source;

float sdRoundedRect(vec2 coord, vec2 halfSize, float radius) {
    vec2 cornerCoord = abs(coord) - (halfSize - vec2(radius));
    float outside = length(max(cornerCoord, 0.0)) - radius;
    float inside = min(max(cornerCoord.x, cornerCoord.y), 0.0);
    return outside + inside;
}

vec2 gradSdRoundedRect(vec2 coord, vec2 halfSize, float radius) {
    vec2 cornerCoord = abs(coord) - (halfSize - vec2(radius));
    if (cornerCoord.x >= 0.0 || cornerCoord.y >= 0.0) {
        vec2 normal = max(cornerCoord, 0.0);
        return sign(coord) * normal / max(length(normal), 0.0001);
    }
    float gradX = step(cornerCoord.y, cornerCoord.x);
    return sign(coord) * vec2(gradX, 1.0 - gradX);
}

float circleMap(float x) {
    return 1.0 - sqrt(max(0.0, 1.0 - x * x));
}

void main() {
    vec2 coord = qt_TexCoord0 * sampleSize;
    vec2 halfSize = glassSize * 0.5;
    vec2 centeredCoord = coord - sampleInset - halfSize;
    float radius = min(cornerRadius, min(halfSize.x, halfSize.y));
    float sd = sdRoundedRect(centeredCoord, halfSize, radius);
    vec2 refractedCoord = coord;
    if (-sd < refractionHeight) {
        sd = min(sd, 0.0);
        // Lens.kt passes a negative amount: bend the sample toward the glass.
        float d = -circleMap(1.0 - -sd / refractionHeight) * refractionAmount;
        float gradRadius = min(radius * 1.5, min(halfSize.x, halfSize.y));
        vec2 grad = gradSdRoundedRect(centeredCoord, halfSize, gradRadius);
        grad /= max(length(grad), 0.0001);
        refractedCoord += d * grad;
    }
    fragColor = texture(source, refractedCoord / sampleSize) * qt_Opacity;
}
