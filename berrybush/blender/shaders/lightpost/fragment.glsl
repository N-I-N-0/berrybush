// post passes of the game's scene lighting: bloom (.bblm) & depth of field (.bdof)
// each pass repeats one draw or frame buffer copy the game makes, at the game's sizes
// values are the frame buffer's, so everything here happens before the conversion to linear color

const float TAU = 6.2831853;

vec4 copyFilter(vec2 uv) {
    // the smoothing filter the game sets for its copies: the row above, the row itself and the
    // row below, weighted 21, 22 and 21 of 64 (copies clamp at the top & bottom of the copied area)
    float texelV = 1.0 / float(textureSize(tex0, 0).y);
    return (texture(tex0, uv - vec2(0.0, texelV)) * 21.0
          + texture(tex0, uv) * 22.0
          + texture(tex0, uv + vec2(0.0, texelV)) * 21.0) / 64.0;
}

float eyeDepth(float bufferDepth, float near, float far, bool isOrtho) {
    // distance in front of the camera for a depth buffer value
    if (isOrtho) {
        return near + bufferDepth * (far - near);
    }
    float ndc = bufferDepth * 2.0 - 1.0;
    return 2.0 * near * far / (far + near - ndc * (far - near));
}

vec3 ringGroup(vec2 uv, vec2 texel, float radius, int taps, int first, float weight) {
    // one draw of the game's blur: 8 samples of a ring of taps, each times the weight, cut off at
    // white at the end
    vec3 sum = vec3(0.0);
    for (int i = first; i < first + 8 && i < taps; i++) {
        float angle = TAU * float(i) / float(taps);
        sum += texture(tex0, uv + vec2(cos(angle), sin(angle)) * radius * texel).rgb * weight;
    }
    return min(sum, vec3(1.0));
}

void main() {
    vec2 uv = fragPosition / 2 + .5;
    switch (mode) {
        case 0: // copy (at half the size: the box filtered copy, as it samples between 4 texels)
            fragOutput = texture(tex0, uv);
            break;
        case 1: { // copy w/ the smoothing filter
            fragOutput = copyFilter(uv);
            break;
        }
        case 2: { // copy to a smaller size: the average of the area each output pixel covers
            // p0.xy = output size
            vec2 corner = floor(gl_FragCoord.xy) / p0.xy;
            vec2 step = 1.0 / (p0.xy * 8.0);
            vec4 sum = vec4(0.0);
            for (int y = 0; y < 8; y++) {
                for (int x = 0; x < 8; x++) {
                    sum += texture(tex0, corner + (vec2(x, y) + 0.5) * step);
                }
            }
            fragOutput = sum / 64.0;
            break;
        }
        case 3: { // bloom brightness: copy to an intensity texture w/ the smoothing filter
            // (such copies turn color into the brightness of the tv signal, 16 to 235)
            float y = dot(copyFilter(uv).rgb, vec3(0.257, 0.504, 0.098)) + 16.0 / 255.0;
            fragOutput = vec4(vec3(y), 1.0);
            break;
        }
        case 4: { // bloom threshold: tex0 the frame buffer, tex1 its brightness, p0.rgb threshold
            // twice the brightness above the threshold, multiplied onto the frame buffer
            float y = texture(tex1, uv).r;
            vec3 above = clamp((vec3(y) - p0.rgb) * 2.0, 0.0, 1.0);
            fragOutput = vec4(texture(tex0, uv).rgb * above, 1.0);
            break;
        }
        case 5: { // bloom blur (EGG::PostEffectBlur): p0 = texel xy, radius, levels; p1 = level weights
            // level n has 8 * (n + 1) taps on the same ring. the first draw replaces, the others add
            vec3 sum = vec3(0.0);
            int levels = int(p0.w);
            for (int level = 0; level < levels; level++) {
                int taps = 8 * (level + 1);
                float weight = (level == 0) ? p1.x : p1.y;
                for (int first = 0; first < taps; first += 8) {
                    sum = min(sum + ringGroup(uv, p0.xy, p0.z, taps, first, weight), vec3(1.0));
                }
            }
            fragOutput = vec4(sum, 1.0);
            break;
        }
        case 6: { // bloom side by side blur: p0 = texel x, radius, strength
            // four times the average of the samples left & right, mixed into the image by strength
            vec3 left = texture(tex0, uv - vec2(p0.y * p0.x, 0.0)).rgb;
            vec3 right = texture(tex0, uv + vec2(p0.y * p0.x, 0.0)).rgb;
            vec3 blurred = clamp(mix(left, right, 0.5) * 4.0, 0.0, 1.0);
            fragOutput = vec4(mix(texture(tex0, uv).rgb, blurred, p0.z), 1.0);
            break;
        }
        case 7: { // bloom combine: tex0 & tex1 the blurs, p0 = their weights, whether there is a second
            vec3 sum = texture(tex0, uv).rgb * p0.x;
            if (p0.z > 0.5) {
                sum += texture(tex1, uv).rgb * p0.y;
            }
            fragOutput = vec4(min(sum, vec3(1.0)), 1.0);
            break;
        }
        case 8: { // bloom composite: tex0 the image, tex1 the bloom; p0 = color rgb, alpha; p1.x blend
            vec4 image = texture(tex0, uv);
            vec3 src = texture(tex1, uv).rgb * p0.rgb;
            int blend = int(p1.x);
            vec3 result = image.rgb;
            switch (blend) {
                case 0: result += src; break; // add
                case 1: result += src * (1.0 - image.rgb); break; // screen
                case 2: result += src * p0.a; break; // add w/ alpha
                case 3: result = mix(image.rgb, src, p0.a); break; // alpha blend
                case 4: result += src * image.rgb; break; // multiply & add
            }
            fragOutput = vec4(clamp(result, 0.0, 1.0), image.a);
            break;
        }
        case 9: { // depth of field blur: p0 = texel xy, radius, taps
            vec3 sum = vec3(0.0);
            int taps = int(p0.w);
            for (int i = 0; i < taps; i++) {
                float angle = TAU * float(i) / float(taps);
                sum += texture(tex0, uv + vec2(cos(angle), sin(angle)) * p0.z * p0.xy).rgb;
            }
            fragOutput = vec4(sum / float(taps), 1.0);
            break;
        }
        case 10: { // depth of field composite: tex0 image, tex1 blur, tex2 depth
            // p0 = focus distance, focus range, near, far; p1 = is ortho, unit scale, falloff power, is split
            // p2 = near strength, far strength
            vec4 image = texture(tex0, uv);
            float depth = eyeDepth(texelFetch(tex2, ivec2(gl_FragCoord.xy), 0).r, p0.z, p0.w, p1.x > 0.5) * p1.y;
            float amount;
            if (p1.w > 0.5) {
                amount = (depth < p0.x) ? p2.x : p2.y;
            }
            else {
                float off = (p0.y > 0.0) ? clamp(abs(depth - p0.x) / (p0.y * 0.5), 0.0, 1.0) : 1.0;
                amount = pow(off, p1.z) * p2.y;
            }
            fragOutput = vec4(mix(image.rgb, texture(tex1, uv).rgb, amount), image.a);
            break;
        }
        case 11: { // output: the same as the standard post-processing shader; p0.x = do alpha
            fragOutput = texture(tex0, uv);
            if (p0.x > 0.5) { fragOutput.rgb /= fragOutput.a; }
            else { fragOutput.a = 1.0; }
            fragOutput = pow(fragOutput, vec4(2.2, 2.2, 2.2, 1.0));
            if (p0.x > 0.5) { fragOutput.rgb *= fragOutput.a; }
            break;
        }
    }
}
