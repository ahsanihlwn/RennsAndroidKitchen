#include "zlib.h"

uLong ZEXPORT crc32_z(uLong crc, const Bytef *buf, z_size_t len) {
    if (buf == Z_NULL) return 0UL;
    crc ^= 0xffffffffUL;
    for (z_size_t i = 0; i < len; ++i) {
        crc ^= (uLong)buf[i];
        for (int b = 0; b < 8; ++b) {
            crc = (crc >> 1) ^ ((crc & 1UL) ? 0xedb88320UL : 0UL);
        }
    }
    return crc ^ 0xffffffffUL;
}

uLong ZEXPORT crc32(uLong crc, const Bytef *buf, uInt len) {
    return crc32_z(crc, buf, (z_size_t)len);
}
