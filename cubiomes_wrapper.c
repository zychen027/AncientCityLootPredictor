/* cubiomes_wrapper.c — 简化 API 供 Python ctypes 调用
 *
 * 编译 (Windows, mingw-w64):
 *   x86_64-w64-mingw32-gcc -shared -o cubiomes_wrapper.dll -fPIC \
 *     cubiomes_wrapper.c biomenoise.c biomes.c finders.c generator.c \
 *     layers.c noise.c quadbase.c util.c -lm -I. -O2
 *
 * 编译 (Linux):
 *   gcc -shared -o libcubiomes_wrapper.so -fPIC \
 *     cubiomes_wrapper.c biomenoise.c biomes.c finders.c generator.c \
 *     layers.c noise.c quadbase.c util.c -lm -I. -O2
 */

#include "finders.h"
#include "generator.h"
#include <stdint.h>
#include <string.h>

/* 全局 Generator — Python 端不需要管它的内存布局 */
static Generator g_gen;
static int g_initialized = 0;

/* 初始化生成器 (相当于 setupGenerator + applySeed)
 * mc: MC 版本号 (如 1200 = MC 1.21)
 * seed: 世界种子
 * 返回: 0=成功, -1=失败
 */
int cubiomes_init(int mc, uint64_t seed) {
    setupGenerator(&g_gen, mc, 0);
    applySeed(&g_gen, DIM_OVERWORLD, seed);
    g_initialized = 1;
    return 0;
}

/* 查找某个 region 中的古城候选位置
 * worldSeed: 世界种子
 * regX, regZ: region 坐标
 * out_x, out_z: 输出方块坐标
 * 返回: 1=有候选, 0=无
 */
int cubiomes_get_ancient_city_pos(uint64_t worldSeed, int regX, int regZ,
                                   int *out_x, int *out_z) {
    Pos pos;
    int ok = getStructurePos(Ancient_City, g_gen.mc, worldSeed, regX, regZ, &pos);
    if (ok) {
        *out_x = pos.x;
        *out_z = pos.z;
    }
    return ok;
}

/* 检查古城是否真的在此位置生成 (biome 验证)
 * blockX, blockZ: 方块坐标
 * 返回: 1=会生成, 0=不会生成 (biome 不符)
 */
int cubiomes_is_ancient_city_viable(int blockX, int blockZ) {
    if (!g_initialized) return -1;
    return isViableStructurePos(Ancient_City, &g_gen, blockX, blockZ, 0);
}

/* 获取古城 variant (旋转 + 起始件)
 * worldSeed: 世界种子
 * blockX, blockZ: 方块坐标
 * out_rot: 输出旋转 (0-3)
 * out_start: 输出起始件 (1-3)
 * 返回: 1=成功, 0=失败
 */
int cubiomes_get_ancient_city_variant(uint64_t worldSeed, int blockX, int blockZ,
                                       int *out_rot, int *out_start) {
    StructureVariant sv;
    int ok = getVariant(&sv, Ancient_City, g_gen.mc, worldSeed, blockX, blockZ, deep_dark);
    if (ok) {
        *out_rot = sv.rotation;
        *out_start = sv.start;
    }
    return ok;
}

/* 搜索附近所有真正会生成的古城
 * worldSeed: 世界种子
 * centerBlockX, centerBlockZ: 中心方块坐标
 * radiusChunks: 搜索半径 (区块)
 * out_results: 输出数组 (每个古城 5 个 int: blockX, blockZ, chunkX, chunkZ, rotation, start)
 * maxResults: 数组最大容量
 * 返回: 找到的古城数量
 */
int cubiomes_find_ancient_cities(uint64_t worldSeed,
                                  int centerBlockX, int centerBlockZ,
                                  int radiusChunks,
                                  int *out_results, int maxResults) {
    if (!g_initialized) return -1;

    int regionSize = 24;  // Ancient City region size
    int count = 0;

    int centerRegX = floordiv(centerBlockX, regionSize << 4);
    int centerRegZ = floordiv(centerBlockZ, regionSize << 4);
    int regionRange = radiusChunks / regionSize + 1;

    for (int drx = -regionRange; drx <= regionRange; drx++) {
        for (int drz = -regionRange; drz <= regionRange; drz++) {
            int regX = centerRegX + drx;
            int regZ = centerRegZ + drz;

            Pos pos;
            if (!getStructurePos(Ancient_City, g_gen.mc, worldSeed, regX, regZ, &pos))
                continue;

            // 距离过滤
            int dx = pos.x - centerBlockX;
            int dz = pos.z - centerBlockZ;
            if (dx*dx + dz*dz > (radiusChunks*16)*(radiusChunks*16))
                continue;

            // Biome 验证
            if (!isViableStructurePos(Ancient_City, &g_gen, pos.x, pos.z, 0))
                continue;

            // 获取 variant
            StructureVariant sv;
            getVariant(&sv, Ancient_City, g_gen.mc, worldSeed, pos.x, pos.z, deep_dark);

            if (count < maxResults) {
                int *p = out_results + count * 6;
                p[0] = pos.x;
                p[1] = pos.z;
                p[2] = pos.x >> 4;
                p[3] = pos.z >> 4;
                p[4] = sv.rotation;
                p[5] = sv.start;
                count++;
            }
        }
    }

    return count;
}
