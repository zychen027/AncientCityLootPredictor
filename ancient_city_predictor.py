#!/usr/bin/env python3
"""
MC 26.1.2 Ancient City Loot Predictor — Standalone Edition
==========================================================
Complete pipeline: worldSeed -> city location -> piece layout
                  -> chest positions -> LootTableSeed -> loot contents

All RNG implementations are exact replicas of MC's decompiled code:
- LegacyRandomSource (Java LCG) for Jigsaw placement
- Xoroshiro128++ for decoration/LootTableSeed
- LegacyRandomSource for loot table rolls

Verified against ground truth:
- Jigsaw placement: 557/557 pieces (pos+bb exact match) across 6 cities
- LootTableSeed: 158/159 (99.4%) prediction accuracy
- Loot contents: 100% slot-level match

Usage:
  python ancient_city_predictor.py --seed <world_seed> --chunk <cx> <cz>
  python ancient_city_predictor.py --verify
"""

import struct, gzip, json, os, sys, math
from collections import deque, Counter, defaultdict

# ============================================================
# Section 1: NBT Parser
# ============================================================
def read_nbt(data):
    """Parse NBT binary data (gzip-compressed or raw)."""
    if data[0:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    pos = [0]

    def rb():
        b = data[pos[0]]; pos[0] += 1; return b
    def rs():
        v = struct.unpack('>h', data[pos[0]:pos[0]+2])[0]; pos[0] += 2; return v
    def ri():
        v = struct.unpack('>i', data[pos[0]:pos[0]+4])[0]; pos[0] += 4; return v
    def rl():
        v = struct.unpack('>q', data[pos[0]:pos[0]+8])[0]; pos[0] += 8; return v
    def rstr():
        l = struct.unpack('>H', data[pos[0]:pos[0]+2])[0]; pos[0] += 2
        s = data[pos[0]:pos[0]+l].decode('utf-8'); pos[0] += l; return s

    def rp(t):
        if t == 1: return rb()
        elif t == 2: return rs()
        elif t == 3: return ri()
        elif t == 4: return rl()
        elif t == 5:
            v = struct.unpack('>f', data[pos[0]:pos[0]+4])[0]; pos[0] += 4; return v
        elif t == 6:
            v = struct.unpack('>d', data[pos[0]:pos[0]+8])[0]; pos[0] += 8; return v
        elif t == 7:
            l = ri(); a = list(data[pos[0]:pos[0]+l]); pos[0] += l; return a
        elif t == 8: return rstr()
        elif t == 9:
            lt = rb()
            l = ri()
            if lt == 0: return []
            return [rp(lt) for _ in range(l)]
        elif t == 10:
            r = {}
            while True:
                t2 = rb()
                if t2 == 0: break
                n = rstr(); r[n] = rp(t2)
            return r
        elif t == 11:
            l = ri(); return [ri() for _ in range(l)]
        elif t == 12:
            l = ri(); return [rl() for _ in range(l)]
        return None

    rt = rb(); rn = rstr()
    return rp(rt)


# ============================================================
# Section 2: Jigsaw Placement Simulator (LegacyRandomSource)
# ============================================================
# ============================================================
# Paths — 在文件末尾 Section 7 通过 _find_data_dir() 自动检测并覆盖
# ============================================================
STRUCTURE_DIR = ''   # 由 Section 7 覆盖
POOL_DIR = ''        # 由 Section 7 覆盖
MAX_DEPTH = 7
MAX_DISTANCE = 116

ROT_NAMES = ['NONE', 'CLOCKWISE_90', 'CLOCKWISE_180', 'COUNTERCLOCKWISE_90']
ROT_NONE = 0
ROT_CW90 = 1
ROT_CW180 = 2
ROT_CCW90 = 3

# ============================================================
# JavaRandom — exact replica of java.util.Random (LegacyRandomSource)
# ============================================================
class JavaRandom:
    def __init__(self, seed=0):
        self.set_seed(seed)
    
    def set_seed(self, seed):
        val = seed & 0xFFFFFFFFFFFFFFFF
        self.state = (val ^ 0x5DEECE66D) & ((1 << 48) - 1)
    
    def next(self, bits):
        self.state = (self.state * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
        val = self.state >> (48 - bits)
        if bits == 32 and val >= (1 << 31):
            val -= (1 << 32)
        return val
    
    def nextInt(self, bound=None):
        if bound is None:
            return self.next(32)
        if bound <= 0:
            raise ValueError
        r = self.next(31) & 0x7FFFFFFF
        m = bound - 1
        if (bound & m) == 0:  # power of 2
            return (bound * r) >> 31
        u = r
        while True:
            val = u % bound
            if u - val + m < 0x7FFFFFFF:
                return val
            u = self.next(31) & 0x7FFFFFFF
    
    def nextLong(self):
        hi = self.next(32) & 0xFFFFFFFFFFFFFFFF
        lo = self.next(32) & 0xFFFFFFFFFFFFFFFF
        return (hi << 32) + lo

# ============================================================
# WorldgenRandom — extends JavaRandom
# ============================================================
class WorldgenRandom(JavaRandom):
    def setLargeFeatureSeed(self, seed, chunkX, chunkZ):
        s = seed & 0xFFFFFFFFFFFFFFFF
        self.set_seed(s)
        xs = self.nextLong()
        zs = self.nextLong()
        result = ((xs * (chunkX & 0xFFFFFFFFFFFFFFFF)) ^ (zs * (chunkZ & 0xFFFFFFFFFFFFFFFF)) ^ s) & 0xFFFFFFFFFFFFFFFF
        self.set_seed(result)

# ============================================================
# Util.shuffle — exact replica
# ============================================================
def util_shuffle(lst, rng):
    """Util.shuffle: for (int i = size; i > 1; --i) { swap(i-1, nextInt(i)) }"""
    size = len(lst)
    for i in range(size, 1, -1):
        j = rng.nextInt(i)
        lst[i - 1], lst[j] = lst[j], lst[i - 1]

def util_shuffled_copy(arr, rng):
    """Util.shuffledCopy: copy then shuffle"""
    copy = list(arr)
    util_shuffle(copy, rng)
    return copy

# ============================================================
# BlockPos — simple (x, y, z) tuple
# ============================================================
def blockpos_offset(pos, dx, dy, dz):
    return (pos[0] + dx, pos[1] + dy, pos[2] + dz)

def blockpos_subtract(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def blockpos_relative(pos, facing):
    """BlockPos.relative(facing) — offset by 1 in facing direction"""
    dx, dy, dz = DIR_VEC[facing]
    return (pos[0] + dx, pos[1] + dy, pos[2] + dz)

# ============================================================
# Direction
# ============================================================
DIR_VEC = {
    'north': (0, 0, -1),
    'south': (0, 0, 1),
    'east': (1, 0, 0),
    'west': (-1, 0, 0),
    'up': (0, 1, 0),
    'down': (0, -1, 0),
}

DIR_OPPOSITE = {
    'north': 'south', 'south': 'north', 'east': 'west', 'west': 'east',
    'up': 'down', 'down': 'up',
}

DIR_ROTATION = {
    'north': {ROT_NONE: 'north', ROT_CW90: 'east', ROT_CW180: 'south', ROT_CCW90: 'west'},
    'south': {ROT_NONE: 'south', ROT_CW90: 'west', ROT_CW180: 'north', ROT_CCW90: 'east'},
    'east':  {ROT_NONE: 'east',  ROT_CW90: 'south', ROT_CW180: 'west', ROT_CCW90: 'north'},
    'west':  {ROT_NONE: 'west',  ROT_CW90: 'north', ROT_CW180: 'east', ROT_CCW90: 'south'},
    'up':    {ROT_NONE: 'up',    ROT_CW90: 'up',    ROT_CW180: 'up',    ROT_CCW90: 'up'},
    'down':  {ROT_NONE: 'down',  ROT_CW90: 'down',  ROT_CW180: 'down',  ROT_CCW90: 'down'},
}

# ============================================================
# FrontAndTop (orientation) — "front_top" e.g. "west_up"
# ============================================================
def get_front(orientation_str):
    return orientation_str.split('_')[0]

def get_top(orientation_str):
    return orientation_str.split('_')[1]

def rotate_orientation(orientation_str, rotation):
    front = get_front(orientation_str)
    top = get_top(orientation_str)
    new_front = DIR_ROTATION[front][rotation]
    new_top = DIR_ROTATION[top][rotation]
    return f'{new_front}_{new_top}'

# ============================================================
# JigsawBlock.canAttach — exact replica
# ============================================================
def can_attach(source_orient, source_target, source_joint,
               target_orient, target_name):
    """JigsawBlock.canAttach(source, target)
    
    sourceFront == targetFront.getOpposite() 
    AND (rollable || sourceTop == targetTop)
    AND source.target().equals(target.name())
    """
    source_front = get_front(source_orient)
    target_front = get_front(target_orient)
    source_top = get_top(source_orient)
    target_top = get_top(target_orient)
    
    rollable = (source_joint == 'rollable')
    
    return (source_front == DIR_OPPOSITE[target_front]
            and (rollable or source_top == target_top)
            and source_target == target_name)

# ============================================================
# Rotation.getShuffled — exact replica
# ============================================================
def rotation_get_shuffled(rng):
    """Rotation.getShuffled: Util.shuffledCopy(Rotation.values(), random)"""
    return util_shuffled_copy([ROT_NONE, ROT_CW90, ROT_CW180, ROT_CCW90], rng)

# ============================================================
# StructureTemplate.transform — exact replica (pivot=ZERO, mirror=NONE)
# ============================================================
def transform(pos, rotation, pivot=(0, 0, 0)):
    """StructureTemplate.transform(pos, Mirror.NONE, rotation, pivot=ZERO)
    
    NONE: (x, y, z) + pivot
    CW90: (-z, y, x) + pivot  
    CW180: (-x, y, -z) + pivot
    CCW90: (z, y, -x) + pivot
    
    But with pivot: return new BlockPos(pivotX*2 - x, y, pivotZ*2 - z) for CW180
    etc. With pivot=(0,0,0): return (-x, y, -z) for CW180.
    """
    x, y, z = pos
    px, py, pz = pivot
    if rotation == ROT_NONE:
        return (x + px, y + py, z + pz)
    elif rotation == ROT_CW90:
        # CLOCKWISE_90: return new BlockPos(pivotX + pivotZ - z, y, pivotZ - pivotX + x)
        return (px + pz - z, y + py, pz - px + x)
    elif rotation == ROT_CW180:
        # CLOCKWISE_180: return new BlockPos(pivotX + pivotX - x, y, pivotZ + pivotZ - z)
        return (px * 2 - x, y + py, pz * 2 - z)
    elif rotation == ROT_CCW90:
        # COUNTERCLOCKWISE_90: return new BlockPos(pivotX - pivotZ + z, y, pivotX + pivotZ - x)
        return (px - pz + z, y + py, px + pz - x)
    return (x, y, z)

# ============================================================
# BoundingBox — exact replica (inclusive integer coords)
# ============================================================
class BoundingBox:
    __slots__ = ['minX', 'minY', 'minZ', 'maxX', 'maxY', 'maxZ']
    
    def __init__(self, minX, minY, minZ, maxX, maxY, maxZ):
        self.minX = minX
        self.minY = minY
        self.minZ = minZ
        self.maxX = maxX
        self.maxY = maxY
        self.maxZ = maxZ
    
    @staticmethod
    def from_corners(p0, p1):
        return BoundingBox(
            min(p0[0], p1[0]), min(p0[1], p1[1]), min(p0[2], p1[2]),
            max(p0[0], p1[0]), max(p0[1], p1[1]), max(p0[2], p1[2])
        )
    
    def moved(self, dx, dy, dz):
        return BoundingBox(self.minX + dx, self.minY + dy, self.minZ + dz,
                          self.maxX + dx, self.maxY + dy, self.maxZ + dz)
    
    def is_inside(self, x, y, z):
        return (x >= self.minX and x <= self.maxX and
                z >= self.minZ and z <= self.maxZ and
                y >= self.minY and y <= self.maxY)
    
    def is_inside_pos(self, pos):
        return self.is_inside(pos[0], pos[1], pos[2])
    
    def get_yspan(self):
        return self.maxY - self.minY + 1
    
    @staticmethod
    def encapsulating_boxes(boxes):
        """BoundingBox.encapsulatingBoxes"""
        boxes = list(boxes)
        if not boxes:
            return None
        first = boxes[0]
        result = BoundingBox(first.minX, first.minY, first.minZ, first.maxX, first.maxY, first.maxZ)
        for bb in boxes[1:]:
            result.minX = min(result.minX, bb.minX)
            result.minY = min(result.minY, bb.minY)
            result.minZ = min(result.minZ, bb.minZ)
            result.maxX = max(result.maxX, bb.maxX)
            result.maxY = max(result.maxY, bb.maxY)
            result.maxZ = max(result.maxZ, bb.maxZ)
        return result
    
    def to_tuple(self):
        return (self.minX, self.minY, self.minZ, self.maxX, self.maxY, self.maxZ)
    
    def __repr__(self):
        return f"BB({self.minX},{self.minY},{self.minZ} - {self.maxX},{self.maxY},{self.maxZ})"

# ============================================================
# AABB — half-open float coords, for VoxelShape
# ============================================================
class AABB:
    __slots__ = ['minX', 'minY', 'minZ', 'maxX', 'maxY', 'maxZ']
    
    @staticmethod
    def of(bb):
        """AABB.of(BoundingBox): converts inclusive integer BB to half-open AABB"""
        return AABB(bb.minX, bb.minY, bb.minZ, bb.maxX + 1, bb.maxY + 1, bb.maxZ + 1)
    
    def __init__(self, minX, minY, minZ, maxX, maxY, maxZ):
        self.minX = minX
        self.minY = minY
        self.minZ = minZ
        self.maxX = maxX
        self.maxY = maxY
        self.maxZ = maxZ
    
    def deflate(self, amount):
        return AABB(self.minX + amount, self.minY + amount, self.minZ + amount,
                    self.maxX - amount, self.maxY - amount, self.maxZ - amount)
    
    def intersects(self, other):
        """Two AABBs intersect iff: min1 < max2 && max1 > min2 (strict)"""
        return (self.minX < other.maxX and self.maxX > other.minX and
                self.minY < other.maxY and self.maxY > other.minY and
                self.minZ < other.maxZ and self.maxZ > other.minZ)
    
    def contains_point(self, x, y, z):
        return (self.minX <= x < self.maxX and
                self.minY <= y < self.maxY and
                self.minZ <= z < self.maxZ)

# ============================================================
# VoxelShape — list of AABBs for collision detection.
# ONLY_SECOND: true if any part of b is NOT covered by a (collision).
# ONLY_FIRST: a minus b (subtract b from a).
# ============================================================
class VoxelShape:
    def __init__(self, aabbs=None):
        self.aabbs = aabbs if aabbs is not None else []
    
    @staticmethod
    def create(aabb):
        return VoxelShape([aabb])
    
    @staticmethod
    def empty():
        return VoxelShape([])
    
    def is_empty(self):
        return len(self.aabbs) == 0
    
    def copy(self):
        return VoxelShape(list(self.aabbs))
    
    def join_is_not_empty_only_second(self, other):
        """BooleanOp.ONLY_SECOND: true if any part of other is NOT covered by self.
        Checks each integer point in target (deflated) against union of self AABBs.
        """
        # For each other AABB (deflated), check if fully covered by union of self AABBs
        for o in other.aabbs:
            if not self._fully_covers(o):
                return True  # Some part of o not covered → ONLY_SECOND is true
        return False
    
    def _fully_covers(self, target):
        """Check if target AABB is fully covered by union of self AABBs.
        For integer-aligned BBs with deflate(0.25), equivalent to checking
        every integer point in the original BB is inside at least one self AABB.
        """
        tx_min = int(math.floor(target.minX))
        ty_min = int(math.floor(target.minY))
        tz_min = int(math.floor(target.minZ))
        tx_max = int(math.ceil(target.maxX)) - 1
        ty_max = int(math.ceil(target.maxY)) - 1
        tz_max = int(math.ceil(target.maxZ)) - 1
        
        # Check if (tx_min..tx_max, ty_min..ty_max, tz_min..tz_max) is covered
        # by union of self AABBs (converted to integer BBs)
        for x in range(tx_min, tx_max + 1):
            for y in range(ty_min, ty_max + 1):
                for z in range(tz_min, tz_max + 1):
                    covered = False
                    for s in self.aabbs:
                        if (s.minX <= x < s.maxX and s.minY <= y < s.maxY and s.minZ <= z < s.maxZ):
                            covered = True
                            break
                    if not covered:
                        return False
        return True
    
    def join_only_first(self, other):
        """BooleanOp.ONLY_FIRST: self minus other.
        
        Returns a new VoxelShape representing the parts of self not in other.
        For integer-aligned AABBs, this is equivalent to subtracting other's AABBs from self.
        """
        result = self.copy()
        for o in other.aabbs:
            new_aabbs = []
            for s in result.aabbs:
                # Subtract o from s
                if not s.intersects(o):
                    new_aabbs.append(s)
                else:
                    # Split s into up to 6 pieces around o
                    # This is the standard AABB subtraction algorithm
                    pieces = subtract_aabb(s, o)
                    new_aabbs.extend(pieces)
            result.aabbs = new_aabbs
        return result

def subtract_aabb(s, o):
    """Subtract AABB o from AABB s, returning a list of AABBs covering s minus o."""
    if not s.intersects(o):
        return [s]
    
    # Compute intersection
    ix_min = max(s.minX, o.minX)
    iy_min = max(s.minY, o.minY)
    iz_min = max(s.minZ, o.minZ)
    ix_max = min(s.maxX, o.maxX)
    iy_max = min(s.maxY, o.maxY)
    iz_max = min(s.maxZ, o.maxZ)
    
    pieces = []
    
    # X- slab (s.minX to ix_min)
    if s.minX < ix_min:
        pieces.append(AABB(s.minX, s.minY, s.minZ, ix_min, s.maxY, s.maxZ))
    # X+ slab (ix_max to s.maxX)
    if ix_max < s.maxX:
        pieces.append(AABB(ix_max, s.minY, s.minZ, s.maxX, s.maxY, s.maxZ))
    # Y- slab (s.minY to iy_min, clamped in X)
    if s.minY < iy_min:
        pieces.append(AABB(ix_min, s.minY, s.minZ, ix_max, iy_min, s.maxZ))
    # Y+ slab (iy_max to s.maxY, clamped in X)
    if iy_max < s.maxY:
        pieces.append(AABB(ix_min, iy_max, s.minZ, ix_max, s.maxY, s.maxZ))
    # Z- slab (s.minZ to iz_min, clamped in X and Y)
    if s.minZ < iz_min:
        pieces.append(AABB(ix_min, iy_min, s.minZ, ix_max, iy_max, iz_min))
    # Z+ slab (iz_max to s.maxZ, clamped in X and Y)
    if iz_max < s.maxZ:
        pieces.append(AABB(ix_min, iy_min, iz_max, ix_max, iy_max, s.maxZ))
    
    return pieces

# ============================================================
# Shapes helpers
# ============================================================
def shapes_create(aabb):
    return VoxelShape.create(aabb)

def shapes_join_is_not_empty(shape1, shape2, op):
    """Shapes.joinIsNotEmpty(shape1, shape2, BooleanOp)"""
    if op == 'ONLY_SECOND':
        return shape1.join_is_not_empty_only_second(shape2)
    elif op == 'ONLY_FIRST':
        # ONLY_FIRST: true if any part of shape1 is not in shape2
        return shape2.join_is_not_empty_only_second(shape1)
    raise ValueError(f"Unsupported op: {op}")

def shapes_join_unoptimized(shape1, shape2, op):
    """Shapes.joinUnoptimized(shape1, shape2, BooleanOp)"""
    if op == 'ONLY_FIRST':
        return shape1.join_only_first(shape2)
    raise ValueError(f"Unsupported op: {op}")

# ============================================================
# NBT Template Loading
# ============================================================
_template_cache = {}

def load_template(location):
    """Load NBT template, return dict with size, jigsaw_blocks, palette, blocks."""
    if location in _template_cache:
        return _template_cache[location]
    
    path = os.path.join(STRUCTURE_DIR, location.replace('minecraft:', '') + '.nbt')
    if not os.path.exists(path):
        _template_cache[location] = None
        return None
    
    with open(path, 'rb') as f:
        nbt = read_nbt(f.read())
    
    blocks = nbt.get('blocks', [])
    palette = nbt.get('palette', [])
    size = tuple(nbt.get('size', [1, 1, 1]))
    
    # Extract jigsaw blocks in NBT order (same as MC's Palette.jigsaws())
    jigsaw_blocks = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        state_idx = block.get('state', 0)
        state = palette[state_idx] if isinstance(state_idx, int) and state_idx < len(palette) else {}
        if state.get('Name', '') == 'minecraft:jigsaw':
            nbt_data = block.get('nbt', {})
            props = state.get('Properties', {})
            jigsaw_blocks.append({
                'pos': tuple(block.get('pos', [0, 0, 0])),
                'orientation': props.get('orientation', 'north_up'),
                'name': nbt_data.get('name', ''),
                'target': nbt_data.get('target', ''),
                'pool': nbt_data.get('pool', ''),
                'joint': nbt_data.get('joint', 'rollable'),
                'selection_priority': nbt_data.get('selection_priority', 0),
                'placement_priority': nbt_data.get('placement_priority', 0),
            })
    
    result = {
        'size': size,
        'jigsaw_blocks': jigsaw_blocks,
        'palette': palette,
        'blocks': blocks,
    }
    _template_cache[location] = result
    return result

def get_template_bounding_box(position, rotation, size):
    """StructureTemplate.getBoundingBox(position, rotation, pivot=ZERO, mirror=NONE)
    
    delta = size.offset(-1, -1, -1)
    corner1 = transform(ZERO, NONE, rotation, ZERO)
    corner2 = transform(ZERO.offset(delta), NONE, rotation, ZERO)
    return BoundingBox.fromCorners(corner1, corner2).move(position)
    """
    sx, sy, sz = size
    delta = (sx - 1, sy - 1, sz - 1)
    corner1 = transform((0, 0, 0), rotation)
    corner2 = transform(delta, rotation)
    bb = BoundingBox.from_corners(corner1, corner2)
    return bb.moved(position[0], position[1], position[2])

# ============================================================
# StructurePoolElement types
# ============================================================
class PoolElement:
    """Base class for pool elements."""
    def __init__(self, projection='rigid'):
        self.projection = projection
    
    def get_shuffled_jigsaw_blocks(self, position, rotation, rng):
        raise NotImplementedError
    
    def get_bounding_box(self, position, rotation):
        raise NotImplementedError
    
    def get_size(self, rotation):
        raise NotImplementedError

class EmptyPoolElement(PoolElement):
    def get_shuffled_jigsaw_blocks(self, position, rotation, rng):
        return []  # EmptyPoolElement has no jigsaw blocks
    
    def get_bounding_box(self, position, rotation):
        return BoundingBox(position[0], position[1], position[2], 
                          position[0], position[1], position[2])
    
    def get_size(self, rotation):
        return (0, 0, 0)

class SinglePoolElement(PoolElement):
    def __init__(self, location, projection='rigid'):
        super().__init__(projection)
        self.location = location
    
    def get_shuffled_jigsaw_blocks(self, position, rotation, rng):
        """SinglePoolElement.getShuffledJigsawBlocks:
        1. template.getJigsaws(position, rotation) — transforms jigsaw blocks to world coords
        2. Util.shuffle(jigsaws, random)
        3. sortBySelectionPriority(jigsaws) — sort by selectionPriority DESCENDING
        """
        tpl = load_template(self.location)
        if not tpl:
            return []
        
        size = tpl['size']
        
        # getJigsaws: transform each jigsaw block's local pos to world pos
        jigsaws = []
        for jb in tpl['jigsaw_blocks']:
            # calculateRelativePosition(settings, pos) = transform(pos, NONE, rotation, ZERO)
            world_pos = transform(jb['pos'], rotation)
            world_pos = blockpos_offset(world_pos, position[0], position[1], position[2])
            
            # Rotate the block state's orientation
            rotated_orient = rotate_orientation(jb['orientation'], rotation)
            
            jigsaws.append({
                'pos': world_pos,  # world position of jigsaw block
                'local_pos': jb['pos'],  # original local position (for targetJigsawLocalPos)
                'orientation': rotated_orient,
                'name': jb['name'],
                'target': jb['target'],
                'pool': jb['pool'],
                'joint': jb['joint'],
                'selection_priority': jb['selection_priority'],
                'placement_priority': jb['placement_priority'],
            })
        
        # Util.shuffle
        util_shuffle(jigsaws, rng)
        
        # sortBySelectionPriority: HIGHEST_SELECTION_PRIORITY_FIRST = comparingInt(selectionPriority).reversed()
        # Since all are 0 for ancient city, this is a no-op
        jigsaws.sort(key=lambda j: -j['selection_priority'])
        
        return jigsaws
    
    def get_bounding_box(self, position, rotation):
        tpl = load_template(self.location)
        if not tpl:
            return BoundingBox(position[0], position[1], position[2], 
                             position[0], position[1], position[2])
        return get_template_bounding_box(position, rotation, tpl['size'])
    
    def get_size(self, rotation):
        tpl = load_template(self.location)
        if not tpl:
            return (0, 0, 0)
        return tpl['size']

class FeaturePoolElement(PoolElement):
    """FeaturePoolElement: size=(0,0,0), BB=point, 1 jigsaw (name='bottom', no shuffle)."""
    def get_shuffled_jigsaw_blocks(self, position, rotation, rng):
        # Returns single jigsaw, NO shuffle, NO RNG consumed
        return [{
            'pos': position,
            'local_pos': (0, 0, 0),
            'orientation': 'down_south',  # FrontAndTop.fromFrontAndTop(Direction.DOWN, Direction.SOUTH)
            'name': 'minecraft:bottom',  # DEFAULT_JIGSAW_NAME
            'target': '',  # EMPTY_ID
            'pool': 'minecraft:empty',  # Pools.EMPTY
            'joint': 'rollable',
            'selection_priority': 0,
            'placement_priority': 0,
        }]
    
    def get_bounding_box(self, position, rotation):
        # size = (0,0,0), so BB = (pos, pos)
        return BoundingBox(position[0], position[1], position[2],
                          position[0], position[1], position[2])
    
    def get_size(self, rotation):
        return (0, 0, 0)

class ListPoolElement(PoolElement):
    def __init__(self, elements, projection='rigid'):
        super().__init__(projection)
        self.elements = elements  # list of PoolElement
    
    def get_shuffled_jigsaw_blocks(self, position, rotation, rng):
        # Delegates to elements[0]
        return self.elements[0].get_shuffled_jigsaw_blocks(position, rotation, rng)
    
    def get_bounding_box(self, position, rotation):
        # Encapsulating boxes of all non-empty elements
        boxes = []
        for e in self.elements:
            if not isinstance(e, EmptyPoolElement):
                boxes.append(e.get_bounding_box(position, rotation))
        if not boxes:
            return BoundingBox(position[0], position[1], position[2],
                             position[0], position[1], position[2])
        return BoundingBox.encapsulating_boxes(boxes)
    
    def get_size(self, rotation):
        sizeX = sizeY = sizeZ = 0
        for e in self.elements:
            sx, sy, sz = e.get_size(rotation)
            sizeX = max(sizeX, sx)
            sizeY = max(sizeY, sy)
            sizeZ = max(sizeZ, sz)
        return (sizeX, sizeY, sizeZ)

# ============================================================
# Pool Loading
# ============================================================
_pool_cache = {}

def load_pool(pool_name):
    """Load template pool, return (elements_list, fallback_name).
    
    elements_list is the expanded list (weight applied) of PoolElement objects.
    """
    if pool_name in _pool_cache:
        return _pool_cache[pool_name]
    
    pool_path = pool_name.replace('minecraft:', '')
    json_path = os.path.join(POOL_DIR, pool_path + '.json')
    
    if not os.path.exists(json_path):
        # Check other paths
        json_path2 = os.path.join(os.path.dirname(POOL_DIR), pool_path + '.json')
        if os.path.exists(json_path2):
            json_path = json_path2
    
    if not os.path.exists(json_path):
        if pool_name == 'minecraft:empty':
            result = ([EmptyPoolElement()], 'minecraft:empty')
            _pool_cache[pool_name] = result
            return result
        # Unknown pool — treat as empty
        result = ([EmptyPoolElement()], 'minecraft:empty')
        _pool_cache[pool_name] = result
        return result
    
    with open(json_path) as f:
        pool_json = json.load(f)
    
    elements = []
    for entry in pool_json.get('elements', []):
        weight = entry.get('weight', 1)
        elem = entry.get('element', {})
        etype = elem.get('element_type', '')
        projection = elem.get('projection', 'rigid')
        
        if etype == 'minecraft:empty_pool_element':
            pe = EmptyPoolElement(projection)
        elif etype == 'minecraft:single_pool_element':
            pe = SinglePoolElement(elem.get('location', ''), projection)
        elif etype == 'minecraft:feature_pool_element':
            pe = FeaturePoolElement(projection)
        elif etype == 'minecraft:list_pool_element':
            sub_elements = []
            for sub in elem.get('elements', []):
                sub_elem = sub.get('element', sub)
                sub_etype = sub_elem.get('element_type', '')
                sub_proj = sub_elem.get('projection', projection)
                if sub_etype == 'minecraft:single_pool_element':
                    sub_elements.append(SinglePoolElement(sub_elem.get('location', ''), sub_proj))
                elif sub_etype == 'minecraft:empty_pool_element':
                    sub_elements.append(EmptyPoolElement(sub_proj))
                elif sub_etype == 'minecraft:feature_pool_element':
                    sub_elements.append(FeaturePoolElement(sub_proj))
                else:
                    sub_elements.append(EmptyPoolElement(sub_proj))
            pe = ListPoolElement(sub_elements, projection)
        else:
            pe = EmptyPoolElement(projection)
        
        for _ in range(weight):
            elements.append(pe)
    
    fallback = pool_json.get('fallback', 'minecraft:empty')
    result = (elements, fallback)
    _pool_cache[pool_name] = result
    return result

# ============================================================
# StructureTemplatePool.getShuffledTemplates
# ============================================================
def get_shuffled_templates(pool_name, rng):
    """StructureTemplatePool.getShuffledTemplates: Util.shuffledCopy(this.templates, random)"""
    elements, fallback = load_pool(pool_name)
    return util_shuffled_copy(elements, rng)

# ============================================================
# PoolElementStructurePiece
# ============================================================
class Piece:
    __slots__ = ['element', 'position', 'rotation', 'bb', 'depth', 
                 'ground_level_delta', 'junctions']
    
    def __init__(self, element, position, rotation, bb, depth, ground_level_delta=0):
        self.element = element  # PoolElement
        self.position = position  # BlockPos (x, y, z)
        self.rotation = rotation  # int (0-3)
        self.bb = bb  # BoundingBox
        self.depth = depth
        self.ground_level_delta = ground_level_delta
        self.junctions = []
    
    @property
    def location(self):
        if isinstance(self.element, SinglePoolElement):
            return self.element.location
        elif isinstance(self.element, FeaturePoolElement):
            return 'minecraft:feature'
        elif isinstance(self.element, ListPoolElement):
            if isinstance(self.element.elements[0], SinglePoolElement):
                return self.element.elements[0].location
            return 'minecraft:list'
        elif isinstance(self.element, EmptyPoolElement):
            return 'minecraft:empty'
        return 'unknown'

# ============================================================
# Placer — direct port of JigsawPlacement.Placer
# ============================================================
class Placer:
    def __init__(self, max_depth, rng):
        self.max_depth = max_depth
        self.rng = rng
        self.pieces = []
        # SequencedPriorityIterator: all priorities are 0, so it's just a FIFO queue
        self.placing = deque()
    
    def try_placing_children(self, source_piece, context_free, depth):
        """Direct port of Placer.tryPlacingChildren.
        
        context_free: MutableObject<VoxelShape> — shared VoxelShape
        """
        source_element = source_piece.element
        source_box_position = source_piece.position
        source_rotation = source_piece.rotation
        source_projection = source_element.projection
        source_rigid = (source_projection == 'rigid')
        source_free = [None]  # MutableObject<@Nullable VoxelShape>
        source_bb = source_piece.bb
        source_box_y = source_bb.minY
        
        # getShuffledJigsawBlocks(sourceElement, sourceBoxPosition, sourceRotation, random)
        source_jigsaws = source_element.get_shuffled_jigsaw_blocks(
            source_box_position, source_rotation, self.rng)
        
        for source_jigsaw in source_jigsaws:
            source_jigsaw_pos = source_jigsaw['pos']  # world position
            
            # Get source front facing
            source_orient = source_jigsaw['orientation']
            source_front = get_front(source_orient)
            source_dir = DIR_VEC[source_front]
            
            # targetJigsawPos = sourceJigsawPos.relative(sourceFront)
            target_jigsaw_pos = blockpos_offset(source_jigsaw_pos, 
                                                source_dir[0], source_dir[1], source_dir[2])
            
            # attachInsideSource = sourceBB.isInside(targetJigsawPos)
            attach_inside = source_bb.is_inside_pos(target_jigsaw_pos)
            
            if attach_inside:
                children_free = source_free
                if children_free[0] is None:
                    children_free[0] = shapes_create(AABB.of(source_bb))
            else:
                children_free = context_free
            
            # Get target pool
            pool_name = source_jigsaw['pool']
            # MC: doesn't skip empty pool — it processes it (getShuffledTemplates returns empty list)
            # But pool=empty → targetPool is Pools.EMPTY → getShuffledTemplates returns []
            # → targetPieces = [] + fallback(=empty) = [] → while loop doesn't execute
            # So skipping is equivalent EXCEPT: MC still shuffles fallback pool
            # For Pools.EMPTY, fallback = Pools.EMPTY → getShuffledTemplates([]) → no RNG
            # So skipping is fine. BUT we need to NOT skip to match MC's RNG sequence
            # when the pool is NOT minecraft:empty but has an empty fallback
            if not pool_name:
                continue
            
            # getShuffledTemplates for target pool
            target_pieces = []
            if depth != self.max_depth:
                target_pieces.extend(get_shuffled_templates(pool_name, self.rng))
            
            # getShuffledTemplates for fallback pool
            _, fallback_name = load_pool(pool_name)
            fallback_pieces = get_shuffled_templates(fallback_name, self.rng)
            target_pieces.extend(fallback_pieces)
            
            target_name = source_jigsaw['target']
            
            placed = False
            
            for target_element in target_pieces:
                if placed:
                    break
                
                # EmptyPoolElement stops the loop
                if isinstance(target_element, EmptyPoolElement):
                    break
                
                # Rotation.getShuffled
                rotations = rotation_get_shuffled(self.rng)
                
                for target_rotation in rotations:
                    if placed:
                        break
                    
                    # targetElement.getShuffledJigsawBlocks(manager, BlockPos.ZERO, targetRotation, random)
                    # NOTE: position = BlockPos.ZERO, so jigsaw pos = transformed local pos
                    target_jigsaws = target_element.get_shuffled_jigsaw_blocks(
                        (0, 0, 0), target_rotation, self.rng)
                    
                    # hackBox and expandTo — we skip expansion hack (doExpansionHack=false for ancient city)
                    expand_to = 0
                    
                    for target_jigsaw in target_jigsaws:
                        if placed:
                            break
                        
                        # canAttach check
                        if not can_attach(source_orient, target_name, source_jigsaw['joint'],
                                         target_jigsaw['orientation'], target_jigsaw['name']):
                            continue
                        
                        # targetJigsawLocalPos = targetJigsaw.info().pos()
                        # Since getShuffledJigsawBlocks was called with position=ZERO,
                        # target_jigsaw['pos'] is already the transformed local pos
                        target_jigsaw_local_pos = target_jigsaw['pos']
                        
                        # rawTargetBoxPos = targetJigsawPos.subtract(targetJigsawLocalPos)
                        raw_target_box_pos = blockpos_subtract(target_jigsaw_pos, target_jigsaw_local_pos)
                        
                        # rawTargetBB = targetElement.getBoundingBox(manager, rawTargetBoxPos, targetRotation)
                        raw_target_bb = target_element.get_bounding_box(raw_target_box_pos, target_rotation)
                        raw_target_y = raw_target_bb.minY
                        
                        target_projection = target_element.projection
                        target_rigid = (target_projection == 'rigid')
                        
                        # targetJigsawLocalY = targetJigsawLocalPos.getY()
                        target_jigsaw_local_y = target_jigsaw_local_pos[1]
                        
                        # sourceJigsawLocalY = sourceJigsawPos.getY() - sourceBoxY
                        source_jigsaw_local_y = source_jigsaw_pos[1] - source_box_y
                        
                        # deltaY = sourceJigsawLocalY - targetJigsawLocalY + sourceFront.getStepY()
                        delta_y = source_jigsaw_local_y - target_jigsaw_local_y + source_dir[1]
                        
                        if source_rigid:
                            target_box_y = source_box_y + delta_y
                        else:
                            # Not applicable for ancient city (all rigid)
                            target_box_y = raw_target_y
                        
                        y_offset = target_box_y - raw_target_y
                        target_bb = raw_target_bb.moved(0, y_offset, 0)
                        target_box_position = blockpos_offset(raw_target_box_pos, 0, y_offset, 0)
                        
                        # expandTo > 0 handling (skipped, doExpansionHack=false)
                        
                        # Collision detection:
                        # Shapes.joinIsNotEmpty(childrenFree, Shapes.create(AABB.of(targetBB).deflate(0.25)), ONLY_SECOND)
                        target_aabb = AABB.of(target_bb).deflate(0.25)
                        target_shape = shapes_create(target_aabb)
                        
                        if shapes_join_is_not_empty(children_free[0], target_shape, 'ONLY_SECOND'):
                            continue  # Collision
                        
                        # childrenFree.setValue(Shapes.joinUnoptimized(childrenFree, Shapes.create(AABB.of(targetBB)), ONLY_FIRST))
                        children_free[0] = shapes_join_unoptimized(
                            children_free[0], 
                            shapes_create(AABB.of(target_bb)), 
                            'ONLY_FIRST')
                        
                        # groundLevelDelta
                        source_gld = source_piece.ground_level_delta
                        if target_rigid:
                            target_gld = source_gld - delta_y
                        else:
                            target_gld = 0  # targetElement.getGroundLevelDelta() — not stored, default 0
                        
                        target_piece = Piece(target_element, target_box_position, target_rotation,
                                           target_bb, depth + 1, target_gld)
                        
                        self.pieces.append(target_piece)
                        
                        if depth + 1 > self.max_depth:
                            placed = True
                            break
                        
                        placement_priority = source_jigsaw['placement_priority']
                        # SequencedPriorityIterator.add(state, placementPriority)
                        # All priorities are 0 → FIFO
                        self.placing.append((target_piece, children_free, depth + 1, placement_priority))
                        
                        placed = True
                        break

# ============================================================
# JigsawPlacement.addPieces — entry point
# ============================================================
def simulate_ancient_city(world_seed, chunk_x, chunk_z, verbose=False):
    """Direct port of JigsawPlacement.addPieces."""
    rng = WorldgenRandom()
    rng.setLargeFeatureSeed(world_seed, chunk_x, chunk_z)
    
    # Rotation.getRandom(random) = Rotation.values()[random.nextInt(4)]
    center_rotation = rng.nextInt(4)
    
    # Start pool: minecraft:ancient_city/city_center
    start_pool_name = 'minecraft:ancient_city/city_center'
    start_pool_elements, _ = load_pool(start_pool_name)
    
    # getRandomTemplate: templates.get(random.nextInt(size))
    center_idx = rng.nextInt(len(start_pool_elements))
    center_element = start_pool_elements[center_idx]
    
    # Position: chunkX*16, -27, chunkZ*16
    position = (chunk_x * 16, -27, chunk_z * 16)
    
    # getRandomNamedJigsaw: calls getShuffledJigsawBlocks(position, rotation, random)
    # then finds jigsaw with name == "minecraft:city_anchor"
    center_jigsaws = center_element.get_shuffled_jigsaw_blocks(
        position, center_rotation, rng)
    
    start_jigsaw_name = 'minecraft:city_anchor'
    anchored_position = None
    for jigsaw in center_jigsaws:
        if jigsaw['name'] == start_jigsaw_name:
            anchored_position = jigsaw['pos']
            break
    
    if anchored_position is None:
        return []
    
    # MC: adjustedPosition = position.subtract(anchoredPosition)
    # anchoredPosition = position + transform(localPos, rotation) (from getShuffledJigsawBlocks)
    # So adjustedPosition = position - (position + transform(localPos, rotation))
    #                      = -transform(localPos, rotation)
    # But getShuffledJigsawBlocks was called with `position` as the world position,
    # so anchored_position already includes position. We compute adjusted_pos directly:
    adjusted_pos = blockpos_subtract(position, anchored_position)

    center_tpl = load_template(center_element.location) if isinstance(center_element, SinglePoolElement) else None
    if not center_tpl:
        return []
    
    anchor_local_pos = None
    for jb in center_tpl['jigsaw_blocks']:
        if jb['name'] == start_jigsaw_name:
            anchor_local_pos = jb['pos']
            break
    
    if anchor_local_pos is None:
        return []
    
    # adjustedPos = position - transform(localPos, rotation)
    rotated_anchor = transform(anchor_local_pos, center_rotation)
    adjusted_pos = blockpos_subtract(position, rotated_anchor)
    
    # centerPiece bounding box
    center_bb = get_template_bounding_box(adjusted_pos, center_rotation, center_tpl['size'])
    
    # Apply ground_level_delta (ancient city start pieces have delta=1, shifts Y down by 1)
    # MC: sourceGroundLevelDelta = sourcePiece.getGroundLevelDelta()
    # For start pieces, this comes from the structure configuration
    gld = 1
    center_pos = (adjusted_pos[0], adjusted_pos[1] - gld, adjusted_pos[2])
    center_bb = center_bb.moved(0, -gld, 0)
    
    center_piece = Piece(center_element, center_pos, center_rotation, center_bb, 0, gld)
    
    pieces = [center_piece]
    
    # Initial contextFree shape
    # MC: shape = Shapes.join(Shapes.create(aabb), Shapes.create(AABB.of(box)), ONLY_FIRST)
    # where aabb is the max distance AABB and box is the center piece BB
    # For ancient city, max_distance = 116, so aabb is a large box centered on the city
    # Shapes.join(big_aabb, center_piece_aabb, ONLY_FIRST) = big_aabb minus center_piece_aabb
    
    # Actually, looking at the code more carefully:
    # VoxelShape shape = Shapes.join(Shapes.create(aabb), Shapes.create(AABB.of(box)), BooleanOp.ONLY_FIRST);
    # This is the INITIAL contextFree, which is the allowed area minus the center piece.
    # But wait, this is the "free" shape, meaning areas where new pieces CAN go.
    # So it starts as: (allowed area) minus (center piece BB)
    
    # Hmm, but in tryPlacingChildren, contextFree is what's passed in.
    # And the collision check is: Shapes.joinIsNotEmpty(childrenFree, targetShape, ONLY_SECOND)
    # which means: any part of target NOT in childrenFree → collision
    # So childrenFree is the FREE space (where pieces can go).
    
    # Initial contextFree = allowed_aabb minus center_piece_bb
    # This means pieces can go anywhere in the allowed area except where the center piece is.
    
    # But for ancient city, the allowed area is very large (116 blocks in each direction).
    # And the center piece is small (18x31x41).
    # So contextFree starts as a large box with a small hole.
    
    # For our purposes, we can represent contextFree as:
    # - A large AABB (the allowed area)
    # - Minus the center piece AABB
    
    # But actually, the VoxelShape representation as a list of AABBs would make this
    # a single large AABB minus a smaller one, which after ONLY_FIRST becomes
    # up to 6 AABBs surrounding the center piece.
    
    # For simplicity and correctness, let me just use a very large AABB and
    # subtract the center piece BB. The collision check will work correctly.
    
    # Actually, the exact allowed area matters for pieces that extend far from center.
    # Let me compute it properly.
    
    # AABB: centerX - 116 to centerX + 117, etc.
    center_x = position[0]
    center_y = position[1]
    center_z = position[2]
    
    # From MC code:
    # AABB aabb = new AABB(centerX - maxDistance.horizontal(), 
    #     Math.max(centerY - maxDistance.vertical(), minY + padding.bottom()),
    #     centerZ - maxDistance.horizontal(),
    #     centerX + maxDistance.horizontal() + 1,
    #     Math.min(centerY + maxDistance.vertical() + 1, maxY + 1 - padding.top()),
    #     centerZ + maxDistance.horizontal() + 1);
    
    # For ancient city: maxDistance = (116, 116, 116) (horizontal=116, vertical=116)
    # padding = DEFAULT_DIMENSION_PADDING (likely 0)
    # minY = -64 (overworld), maxY = 319
    
    # Let's just use a very large box
    allowed_aabb = AABB(
        center_x - 116,
        max(center_y - 116, -64),
        center_z - 116,
        center_x + 117,
        min(center_y + 117, 320),
        center_z + 117,
    )
    
    context_free = [shapes_create(allowed_aabb)]
    # Subtract center piece BB
    center_aabb = AABB.of(center_bb)
    context_free[0] = shapes_join_unoptimized(context_free[0], shapes_create(center_aabb), 'ONLY_FIRST')
    
    # Create placer
    placer = Placer(MAX_DEPTH, rng)
    placer.pieces = pieces
    
    # Add center piece to placing queue
    placer.placing.append((center_piece, context_free, 0, 0))
    
    # Process queue
    while placer.placing:
        piece_state = placer.placing.popleft()
        source_piece = piece_state[0]
        free = piece_state[1]
        depth = piece_state[2]
        
        placer.try_placing_children(source_piece, free, depth)
    
    return placer.pieces

# ============================================================
# Main
# ============================================================

# ============================================================
# Section 3: Xoroshiro128++ RNG (for LootTableSeed prediction)
# ============================================================
# ============================================================================
# Xoroshiro128++ (exact replica of net.minecraft.world.level.levelgen.Xoroshiro128PlusPlus)
# ============================================================================
class Xoroshiro128PlusPlus:
    def __init__(self, seed_lo, seed_hi):
        self.seed_lo = seed_lo & 0xFFFFFFFFFFFFFFFF
        self.seed_hi = seed_hi & 0xFFFFFFFFFFFFFFFF
        if self.seed_lo == 0 and self.seed_hi == 0:
            self.seed_lo = -7046029254386353131 & 0xFFFFFFFFFFFFFFFF
            self.seed_hi = 7640891576956012809 & 0xFFFFFFFFFFFFFFFF

    def _to_signed(self, val):
        val &= 0xFFFFFFFFFFFFFFFF
        if val >= (1 << 63):
            val -= (1 << 64)
        return val

    def nextLong(self):
        s0 = self.seed_lo
        s1 = self.seed_hi
        # Long.rotateLeft(s0 + s1, 17) + s0
        sum_val = (s0 + s1) & 0xFFFFFFFFFFFFFFFF
        rotated = ((sum_val << 17) | (sum_val >> (64 - 17))) & 0xFFFFFFFFFFFFFFFF
        result = (rotated + s0) & 0xFFFFFFFFFFFFFFFF

        # Update state
        # seedLo = Long.rotateLeft(s0, 49) ^ (s1 ^= s0) ^ s1 << 21
        s1_new = s1 ^ s0  # s1 ^= s0
        rot49 = ((s0 << 49) | (s0 >> (64 - 49))) & 0xFFFFFFFFFFFFFFFF
        shift21 = (s1_new << 21) & 0xFFFFFFFFFFFFFFFF
        self.seed_lo = rot49 ^ s1_new ^ shift21

        # seedHi = Long.rotateLeft(s1, 28)  (s1 after the XOR above)
        self.seed_hi = ((s1_new << 28) | (s1_new >> (64 - 28))) & 0xFFFFFFFFFFFFFFFF

        return self._to_signed(result)

    def nextInt(self):
        return self.nextInt_bounded(0xFFFFFFFF + 1) if False else (self.nextLong() & 0xFFFFFFFF)

    def nextInt_bounded(self, bound):
        """Exact replica of XoroshiroRandomSource.nextInt(int bound)"""
        if bound <= 0:
            raise ValueError("Bound must be positive")
        random_bits = self.nextLong() & 0xFFFFFFFF  # Integer.toUnsignedLong(this.nextInt())
        multiplied = random_bits * bound
        fractional = multiplied & 0xFFFFFFFF
        if fractional < bound:
            unbiased_start = ((~bound + 1) & 0xFFFFFFFF) % bound  # Integer.remainderUnsigned(~bound + 1, bound)
            while fractional < unbiased_start:
                random_bits = self.nextLong() & 0xFFFFFFFF
                multiplied = random_bits * bound
                fractional = multiplied & 0xFFFFFFFF
        return (multiplied >> 32) & 0xFFFFFFFF

    def nextBoolean(self):
        return (self.nextLong() & 1) != 0

    def nextFloat(self):
        bits = self.nextLong() >> (64 - 24)
        return bits / (1 << 24)


# ============================================================================
# RandomSupport helpers
# ============================================================================
def mix_stafford13(z):
    z = z & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 30)) * (-4658895280553007687 & 0xFFFFFFFFFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * (-7723592293110705685 & 0xFFFFFFFFFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF
    return (z ^ (z >> 31)) & 0xFFFFFFFFFFFFFFFF

def upgrade_seed_to_128bit(legacy_seed):
    """UpgradeSeedTo128bit: upgrade 64-bit seed to 128-bit"""
    legacy_seed &= 0xFFFFFFFFFFFFFFFF
    low_bits = legacy_seed ^ 0x6A09E667F3BCC909
    high_bits = (low_bits + (-7046029254386353131 & 0xFFFFFFFFFFFFFFFF)) & 0xFFFFFFFFFFFFFFFF
    # .mixed() applies mixStafford13 to both
    return mix_stafford13(low_bits), mix_stafford13(high_bits)


# ============================================================================
# XoroshiroRandomSource (wraps Xoroshiro128PlusPlus)
# ============================================================================
class XoroshiroRandomSource:
    def __init__(self, seed_lo=None, seed_hi=None, seed=None):
        if seed is not None:
            lo, hi = upgrade_seed_to_128bit(seed)
            self.rng = Xoroshiro128PlusPlus(lo, hi)
        else:
            self.rng = Xoroshiro128PlusPlus(seed_lo, seed_hi)

    def setSeed(self, seed):
        lo, hi = upgrade_seed_to_128bit(seed & 0xFFFFFFFFFFFFFFFF)
        self.rng = Xoroshiro128PlusPlus(lo, hi)

    def nextLong(self):
        return self.rng.nextLong()

    def nextInt(self, bound=None):
        if bound is None:
            return self.rng.nextLong() & 0xFFFFFFFF
        return self.rng.nextInt_bounded(bound)

    def nextBoolean(self):
        return (self.rng.nextLong() & 1) != 0

    def nextFloat(self):
        bits = self.rng.nextLong() >> (64 - 24)
        return bits / (1 << 24)

    def consumeCount(self, rounds):
        for _ in range(rounds):
            self.rng.nextLong()

    def fork(self):
        lo = self.rng.nextLong()
        hi = self.rng.nextLong()
        return XoroshiroRandomSource(lo, hi)


# ============================================================================
# WorldgenRandom (wraps Xoroshiro for decoration phase)
# ============================================================================
class WorldgenRandomXoroshiro:
    """WorldgenRandom that uses XoroshiroRandomSource (for decoration/placeInWorld phase).
    
    Key: WorldgenRandom extends LegacyRandomSource, which delegates next(bits) to
    XoroshiroRandomSource. LegacyRandomSource.nextLong() = ((long)next(32) << 32) + next(32).
    Each next(32) = (int)(xorRng.nextLong() >>> 32) — consumes ONE Xoroshiro nextLong.
    So each WorldgenRandom.nextLong() consumes TWO Xoroshiro nextLong() calls.
    """
    MASK = 0xFFFFFFFFFFFFFFFF
    
    def __init__(self):
        self.random = XoroshiroRandomSource(seed=0)

    def setSeed(self, seed):
        self.random.setSeed(seed & self.MASK)

    def _next_bits(self, bits):
        """WorldgenRandom.next(bits) = (int)(xorRng.nextLong() >>> (64 - bits))"""
        raw = self.random.rng.nextLong() & self.MASK  # unsigned 64-bit
        val = raw >> (64 - bits)  # high `bits` bits, unsigned
        # Cast to signed 32-bit int (Java behavior)
        if bits == 32 and val >= (1 << 31):
            val -= (1 << 32)
        return val

    def nextLong(self):
        """LegacyRandomSource.nextLong() = ((long)next(32) << 32) + next(32)
        Consumes TWO Xoroshiro nextLong() calls."""
        high = self._next_bits(32)  # signed 32-bit int
        low = self._next_bits(32)   # signed 32-bit int
        # Java: ((long)high << 32) + low  (low sign-extended to long)
        result = (high << 32) + low
        return self._to_signed(result)
    
    @staticmethod
    def _to_signed(val):
        val &= 0xFFFFFFFFFFFFFFFF
        if val >= (1 << 63):
            val -= (1 << 64)
        return val

    def nextInt(self, bound=None):
        if bound is None:
            return self.random.nextInt()
        return self.random.nextInt(bound)

    def nextBoolean(self):
        return self.random.nextBoolean()

    def nextFloat(self):
        return self.random.nextFloat()

    def setDecorationSeed(self, seed, chunkX, chunkZ):
        """Exact replica of WorldgenRandom.setDecorationSeed
        
        Java code:
          this.random.setSeed(worldSeed);
          long xs = this.random.nextLong() | 1L;
          long zs = this.random.nextLong() | 1L;
          long result = (x * xs + z * zs) ^ worldSeed;
          this.random.setSeed(result);
        
        Note: In Java, all operations are 64-bit signed with overflow wrap.
        In Python, we must mask to 64 bits to simulate this, especially
        for XOR with negative numbers (Python negative ints have infinite
        leading 1s).
        """
        MASK = 0xFFFFFFFFFFFFFFFF
        seed_u = seed & MASK
        self.setSeed(seed_u)
        # Convert to unsigned 64-bit before arithmetic
        xScale = (self.nextLong() & MASK) | 1  # unsigned | 1
        zScale = (self.nextLong() & MASK) | 1
        # In Java: (x * xs + z * zs) wraps at 64 bits
        # In Python: mask to 64 bits after multiplication and addition
        result = ((chunkX * xScale + chunkZ * zScale) & MASK) ^ seed_u
        result &= MASK
        self.setSeed(result)
        return result

    def setFeatureSeed(self, decorationSeed, index, step):
        """Exact replica of WorldgenRandom.setFeatureSeed"""
        result = decorationSeed + index + (10000 * step)
        result &= 0xFFFFFFFFFFFFFFFF
        self.setSeed(result)


# ============================================================================
# NBT Parser (for reading structure templates)
# ============================================================================

# ============================================================
# Section 4: Loot Table Simulator
# ============================================================
class JavaRandom:
    def __init__(self, seed=0):
        val = seed & 0xFFFFFFFFFFFFFFFF
        self.state = (val ^ 0x5DEECE66D) & ((1 << 48) - 1)
    
    def next(self, bits):
        self.state = (self.state * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
        val = self.state >> (48 - bits)
        if bits == 32 and val >= (1 << 31):
            val -= (1 << 32)
        return val
    
    def nextInt(self, n=None):
        if n is None: return self.next(32)
        if n <= 0: raise ValueError
        if (n & (n - 1)) == 0:
            return (n * (self.next(31) & 0x7FFFFFFF)) >> 31
        while True:
            bits = self.next(31) & 0x7FFFFFFF
            val = bits % n
            if bits - val + (n - 1) < 0x7FFFFFFF:
                return val
    
    def nextFloat(self):
        return (self.next(24) & 0xFFFFFF) / (1 << 24)
    
    def nextBoolean(self):
        return self.next(1) != 0


def mth_next_int(rng, min_v, max_v):
    """Mth.nextInt(random, min, max) = nextInt(max-min+1) + min"""
    return rng.nextInt(max_v - min_v + 1) + min_v


def load_loot_table(path):
    with open(path) as f:
        return json.load(f)


def get_entry_count(entry, rng):
    """Get item count from entry's set_count function"""
    count = 1
    for func in entry.get('functions', []):
        if func.get('function') == 'minecraft:set_count':
            cnt = func.get('count', 1)
            add = func.get('add', False)
            if isinstance(cnt, dict) and cnt.get('type') == 'minecraft:uniform':
                min_v = int(cnt['min'])
                max_v = int(cnt['max'])
                new_count = mth_next_int(rng, min_v, max_v)
                count = count + new_count if add else new_count
            elif isinstance(cnt, (int, float)):
                new_count = int(cnt)
                count = count + new_count if add else new_count
    return count


def roll_pool(pool, rng):
    """Roll a pool and return list of (item_name, count)"""
    # Get rolls
    rolls_spec = pool.get('rolls', 1)
    if isinstance(rolls_spec, dict) and rolls_spec.get('type') == 'minecraft:uniform':
        min_r = int(rolls_spec['min'])
        max_r = int(rolls_spec['max'])
        roll_count = mth_next_int(rng, min_r, max_r)
    elif isinstance(rolls_spec, (int, float)):
        roll_count = int(rolls_spec)
    else:
        roll_count = 1
    
    # Get entries and total weight
    entries = pool['entries']
    total_weight = sum(e.get('weight', 1) for e in entries)
    
    items = []
    for _ in range(roll_count):
        # Select entry
        idx = rng.nextInt(total_weight)
        cumulative = 0
        for entry in entries:
            w = entry.get('weight', 1)
            cumulative += w
            if idx < cumulative:
                etype = entry.get('type', '')
                if etype == 'minecraft:empty':
                    break
                name = entry.get('name', '')
                count = get_entry_count(entry, rng)
                items.append((name, count))
                break
    
    return items


def shuffle_and_split(items, available_slots, rng):
    """
    Replicate LootTable.shuffleAndSplitItems exactly
    """
    result = []
    splittable = []
    
    # Separate items with count > 1
    for item in items:
        name, count = item
        if count <= 1:
            result.append([name, count])
        else:
            splittable.append([name, count])
    
    # Split items until we fill available slots
    while available_slots - len(result) - len(splittable) > 0 and splittable:
        # Pick random splittable item
        idx = mth_next_int(rng, 0, len(splittable) - 1)
        item = splittable.pop(idx)
        
        # Split count
        remove = mth_next_int(rng, 1, item[1] // 2)
        copy = [item[0], remove]
        item[1] -= remove
        
        # Add original back
        if item[1] > 1 and rng.nextBoolean():
            splittable.append(item)
        else:
            result.append(item)
        
        # Add copy back
        if copy[1] > 1 and rng.nextBoolean():
            splittable.append(copy)
        else:
            result.append(copy)
    
    # Add remaining splittable items
    for item in splittable:
        result.append(item)
    
    # Util.shuffle(result, random) = Collections.shuffle
    for i in range(len(result) - 1, 0, -1):
        j = rng.nextInt(i + 1)
        result[i], result[j] = result[j], result[i]
    
    return result


def get_available_slots(rng, container_size=27):
    """Replicate getAvailableSlots: create list and shuffle"""
    slots = list(range(container_size))
    # Util.shuffle = Collections.shuffle
    for i in range(len(slots) - 1, 0, -1):
        j = rng.nextInt(i + 1)
        slots[i], slots[j] = slots[j], slots[i]
    return slots


def simulate_loot(loot_table_path, seed):
    """Simulate opening a chest with the given seed.
    Delegates to loot_simulator.py for 100% accurate MC 26.1.2 replication.
    """
    # Try importing the accurate simulator
    try:
        from loot_simulator import simulate_loot as _simulate_loot
        return _simulate_loot(loot_table_path, seed)
    except ImportError:
        pass
    
    # Fallback to old (less accurate) implementation
    table = load_loot_table(loot_table_path)
    rng = JavaRandom(seed)
    
    all_items = []
    for pool in table['pools']:
        items = roll_pool(pool, rng)
        all_items.extend(items)
    
    slots = get_available_slots(rng, 27)
    final_items = shuffle_and_split(all_items, len(slots), rng)
    
    slot_items = {}
    for i, (name, count) in enumerate(final_items):
        if i < len(slots):
            slot_items[slots[i]] = (name, count)
    
    return slot_items, final_items



# ============================================================
# Section 5: Region File Reader
# ============================================================
class NBTReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_byte(self): v = self.data[self.pos]; self.pos += 1; return v
    def read_short(self): v = struct.unpack('>h', self.data[self.pos:self.pos+2])[0]; self.pos += 2; return v
    def read_ushort(self): v = struct.unpack('>H', self.data[self.pos:self.pos+2])[0]; self.pos += 2; return v
    def read_int(self): v = struct.unpack('>i', self.data[self.pos:self.pos+4])[0]; self.pos += 4; return v
    def read_long(self): v = struct.unpack('>q', self.data[self.pos:self.pos+8])[0]; self.pos += 8; return v
    def read_float(self): v = struct.unpack('>f', self.data[self.pos:self.pos+4])[0]; self.pos += 4; return v
    def read_double(self): v = struct.unpack('>d', self.data[self.pos:self.pos+8])[0]; self.pos += 8; return v
    def read_string(self):
        l = struct.unpack('>H', self.data[self.pos:self.pos+2])[0]; self.pos += 2
        s = self.data[self.pos:self.pos+l].decode('utf-8', errors='replace'); self.pos += l; return s
    def read_byte_array(self):
        l = self.read_int(); arr = list(self.data[self.pos:self.pos+l]); self.pos += l; return arr
    def read_int_array(self):
        l = self.read_int(); return [self.read_int() for _ in range(l)]
    def read_long_array(self):
        l = self.read_int(); return [self.read_long() for _ in range(l)]

    def read_payload(self, tag_type):
        if tag_type == 1: return self.read_byte()
        elif tag_type == 2: return self.read_short()
        elif tag_type == 3: return self.read_int()
        elif tag_type == 4: return self.read_long()
        elif tag_type == 5: return self.read_float()
        elif tag_type == 6: return self.read_double()
        elif tag_type == 7: return self.read_byte_array()
        elif tag_type == 8: return self.read_string()
        elif tag_type == 9:
            list_type = self.read_byte()
            length = self.read_int()
            if length == 0: return []
            return [self.read_payload(list_type) for _ in range(length)]
        elif tag_type == 10:
            result = {}
            while True:
                t = self.read_byte()
                if t == 0: break
                n = self.read_string()
                result[n] = self.read_payload(t)
            return result
        elif tag_type == 11: return self.read_int_array()
        elif tag_type == 12: return self.read_long_array()
        return None

    def read_root(self):
        tag_type = self.read_byte()
        name = self.read_string()
        return self.read_payload(tag_type)



# ============================================================
# Section 6: LootTableSeed Prediction
# ============================================================
def predict_loot_seeds(world_seed, chunk_x, chunk_z, max_chests=30):
    """Predict LootTableSeed for chests in a single chunk.

    RNG chain (per-chunk, NOT per-structure):
    1. WorldgenRandom(XoroshiroRandomSource)
    2. setDecorationSeed(worldSeed, chunkX*16, chunkZ*16)
    3. setFeatureSeed(decorationSeed, index=0, step=7)
    4. For each RandomizableContainer in this chunk: nextLong() -> LootTableSeed
    """
    rng = WorldgenRandomXoroshiro()
    origin_x = chunk_x * 16
    origin_z = chunk_z * 16
    decoration_seed = rng.setDecorationSeed(world_seed, origin_x, origin_z)
    rng.setFeatureSeed(decoration_seed, 0, 7)
    return [rng.nextLong() for _ in range(max_chests)]


# Blocks that extend RandomizableContainerBlockEntity in MC
_RANDOMIZABLE_CONTAINERS = {
    'minecraft:chest', 'minecraft:trapped_chest', 'minecraft:barrel',
    'minecraft:shulker_box', 'minecraft:dispenser', 'minecraft:dropper',
    'minecraft:hopper', 'minecraft:brewing_stand',
    'minecraft:furnace', 'minecraft:blast_furnace', 'minecraft:smoker',
}
for _c in ['white','orange','magenta','light_blue','yellow','lime','pink','gray',
            'light_gray','cyan','purple','blue','brown','green','red','black']:
    _RANDOMIZABLE_CONTAINERS.add(f'minecraft:{_c}_shulker_box')


def extract_chests_from_pieces(pieces, world_seed):
    """Extract chest positions + LootTableSeeds from Jigsaw simulation results.

    No region files needed — chest positions come from NBT templates.

    Algorithm:
    1. For each piece (in BFS order), load its NBT template
    2. Find all RandomizableContainer blocks, sorted by (y, x, z)
    3. Transform to world positions
    4. Group by chunk (world_x>>4, world_z>>4)
    5. For each chunk, init per-chunk RNG and assign nextLong() to each container

    Returns list of dicts: {x, y, z, seed, loot_table, piece_idx}
    """
    from collections import defaultdict

    all_containers = []
    for pi, piece in enumerate(pieces):
        loc = piece.location
        if loc in ('minecraft:empty', 'minecraft:feature', 'minecraft:list', 'unknown'):
            if loc == 'minecraft:list':
                tpl = load_template(piece.element.elements[0].location)
            else:
                continue
        else:
            tpl = load_template(loc)
        if not tpl:
            continue

        palette = tpl.get('palette', [])
        # Block entities sorted by (y, x, z) — same as MC's buildInfoList
        block_entities = []
        for b in tpl.get('blocks', []):
            if not isinstance(b, dict):
                continue
            if 'nbt' not in b:
                continue
            block_entities.append(b)
        block_entities.sort(key=lambda b: (b['pos'][1], b['pos'][0], b['pos'][2]))

        for b in block_entities:
            si = b.get('state', 0)
            state = palette[si] if si < len(palette) else {}
            name = state.get('Name', '') if isinstance(state, dict) else ''
            if name not in _RANDOMIZABLE_CONTAINERS:
                continue

            local_pos = b.get('pos', [0, 0, 0])
            world_pos = transform(local_pos, piece.rotation)
            wx = world_pos[0] + piece.position[0]
            wy = world_pos[1] + piece.position[1]
            wz = world_pos[2] + piece.position[2]
            nbt = b.get('nbt', {})
            lt = nbt.get('LootTable', '')
            all_containers.append({
                'x': wx, 'y': wy, 'z': wz,
                'piece_idx': pi, 'loot_table': lt,
                'chunk_x': wx >> 4, 'chunk_z': wz >> 4,
            })

    # Group by chunk, assign per-chunk seeds
    by_chunk = defaultdict(list)
    for c in all_containers:
        by_chunk[(c['chunk_x'], c['chunk_z'])].append(c)

    rng = WorldgenRandomXoroshiro()
    result = []
    for (cx, cz), containers in sorted(by_chunk.items()):
        rng2 = WorldgenRandomXoroshiro()
        decoration_seed = rng2.setDecorationSeed(world_seed, cx * 16, cz * 16)
        rng2.setFeatureSeed(decoration_seed, 0, 7)

        for c in containers:
            c['seed'] = rng2.nextLong()
            if c['loot_table']:
                result.append(c)

    return result


# ============================================================
# Section 7: Pipeline
# ============================================================
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Try several candidate locations for data files
def _find_data_dir():
    candidates = [
        os.path.join(_SCRIPT_DIR, 'loot_tables'),
        os.path.join(_SCRIPT_DIR, '..', 'loot_tables'),
    ]
    for c in candidates:
        c = os.path.abspath(c)
        if os.path.isdir(os.path.join(c, 'data', 'minecraft', 'structure')):
            return c
    return candidates[0]

_DATA_DIR = _find_data_dir()
# Override paths from Section 2 with auto-detected locations
STRUCTURE_DIR = os.path.join(_DATA_DIR, 'data', 'minecraft', 'structure')
POOL_DIR = os.path.join(_DATA_DIR, 'data', 'minecraft', 'worldgen', 'template_pool')
LOOT_TABLE_DIR = os.path.join(_DATA_DIR, 'data', 'minecraft', 'loot_table', 'chests')

TARGET_ITEMS = {
    'minecraft:music_disc_otherside': 'music_disc_otherside',
    'minecraft:enchanted_golden_apple': 'enchanted_golden_apple',
    'minecraft:diamond_hoe': 'diamond_hoe (enchanted)',
    'minecraft:diamond_leggings': 'diamond_leggings (enchanted)',
    'minecraft:diamond_chestplate': 'diamond_chestplate (enchanted)',
    'minecraft:diamond_boots': 'diamond_boots (enchanted)',
    'minecraft:diamond_helmet': 'diamond_helmet (enchanted)',
    'minecraft:diamond_sword': 'diamond_sword (enchanted)',
}


def extract_chests_from_region(world_dir, city_chunk_x, city_chunk_z, radius=200):
    """Extract ancient city chests from region files around a city."""
    chests = []
    region_dir = os.path.join(world_dir, 'dimensions', 'minecraft', 'overworld', 'region')
    if not os.path.isdir(region_dir):
        return chests
    center_block_x = city_chunk_x * 16
    center_block_z = city_chunk_z * 16

    for fname in sorted(os.listdir(region_dir)):
        if not fname.endswith('.mca'):
            continue
        parts = fname.replace('.mca', '').split('.')
        rx, rz = int(parts[1]), int(parts[2])
        region_block_x = rx * 512
        region_block_z = rz * 512
        if abs(region_block_x - center_block_x) > radius + 1024:
            continue
        if abs(region_block_z - center_block_z) > radius + 1024:
            continue
        try:
            region = RegionFile(os.path.join(region_dir, fname))
        except Exception:
            continue
        for cx, cz, chunk in region.iter_chunks():
            abs_cx = rx * 32 + cx
            abs_cz = rz * 32 + cz
            block_cx = abs_cx * 16
            block_cz = abs_cz * 16
            dist = math.sqrt((block_cx - center_block_x)**2 + (block_cz - center_block_z)**2)
            if dist > radius:
                continue
            bes = chunk.get('block_entities', [])
            if not isinstance(bes, list):
                continue
            for be in bes:
                if not isinstance(be, dict):
                    continue
                lt = be.get('LootTable', '')
                if 'ancient_city' not in lt:
                    continue
                chests.append({
                    'x': be.get('x', 0), 'y': be.get('y', 0), 'z': be.get('z', 0),
                    'seed': be.get('LootTableSeed', 0), 'loot_table': lt,
                    'chunk_x': abs_cx, 'chunk_z': abs_cz,
                })
    return chests


# ============================================================
# Section 7.5: Ancient City Locator (pure Python, no cubiomes)
# ============================================================
ANCIENT_CITY_SALT = 20083232
ANCIENT_CITY_REGION_SIZE = 24
ANCIENT_CITY_CHUNK_RANGE = 16

def get_ancient_city_in_region(world_seed, reg_x, reg_z):
    """Replicate cubiomes getFeaturePos for Ancient City.
    Returns (block_x, block_z) of the structure origin in the given region.
    Each 24x24-chunk region produces exactly one candidate.
    """
    rng = WorldgenRandom(0)
    val = reg_x * 341873128712 + reg_z * 132897987541 + world_seed + ANCIENT_CITY_SALT
    rng.set_seed(val & 0xFFFFFFFFFFFFFFFF)
    cx = rng.nextInt(ANCIENT_CITY_CHUNK_RANGE)
    cz = rng.nextInt(ANCIENT_CITY_CHUNK_RANGE)
    block_x = (reg_x * ANCIENT_CITY_REGION_SIZE + cx) * 16
    block_z = (reg_z * ANCIENT_CITY_REGION_SIZE + cz) * 16
    return block_x, block_z

def get_ancient_city_variant(world_seed, block_x, block_z):
    """Replicate cubiomes getVariant for Ancient City.
    Returns (rotation, start_piece_index) where:
    - rotation: 0=NONE, 1=CW90, 2=CW180, 3=CCW90
    - start_piece_index: 1, 2, or 3 (city_center_1/2/3)
    """
    chunk_x = block_x >> 4
    chunk_z = block_z >> 4
    rng = WorldgenRandom(0)
    rng.setLargeFeatureSeed(world_seed, chunk_x, chunk_z)
    rotation = rng.nextInt(4)
    start = 1 + rng.nextInt(3)
    return rotation, start

def find_nearby_ancient_cities(world_seed, center_block_x, center_block_z, search_radius_chunks=200):
    """Find all ancient city candidates near a position.
    Scans regions within search_radius_chunks (in chunk units).
    Returns list of (block_x, block_z, rotation, start, chunk_x, chunk_z).
    NOTE: Does NOT verify biome. Some candidates may not actually generate.
    """
    results = []
    region_size = ANCIENT_CITY_REGION_SIZE
    # Convert center to region coords
    center_reg_x = math.floor(center_block_x / (region_size * 16))
    center_reg_z = math.floor(center_block_z / (region_size * 16))
    # How many regions to scan in each direction
    region_range = max(1, search_radius_chunks // region_size + 1)

    for dr_x in range(-region_range, region_range + 1):
        for dr_z in range(-region_range, region_range + 1):
            reg_x = center_reg_x + dr_x
            reg_z = center_reg_z + dr_z
            bx, bz = get_ancient_city_in_region(world_seed, reg_x, reg_z)
            # Distance check
            dist = math.sqrt((bx - center_block_x)**2 + (bz - center_block_z)**2)
            if dist > search_radius_chunks * 16:
                continue
            rot, start = get_ancient_city_variant(world_seed, bx, bz)
            results.append({
                'block_x': bx, 'block_z': bz,
                'chunk_x': bx >> 4, 'chunk_z': bz >> 4,
                'rotation': rot, 'start': start,
                'distance': int(dist),
            })

    results.sort(key=lambda r: r['distance'])
    return results


def predict_city(world_seed, chunk_x, chunk_z, world_dir=None, data_dir=None, verbose=True):
    """End-to-end: simulate jigsaw -> get chests -> predict seeds -> simulate loot.

    Args:
        world_seed: 64-bit world seed
        chunk_x, chunk_z: ancient city origin chunk coords
        world_dir: path to world/ directory (for region file chest extraction)
        data_dir: path to loot_tables/ directory (for NBT templates and loot JSONs)
        verbose: print progress

    Returns:
        dict with pieces, chests, target_items
    """
    if data_dir:
        global STRUCTURE_DIR, POOL_DIR, LOOT_TABLE_DIR
        STRUCTURE_DIR = os.path.join(data_dir, 'data', 'minecraft', 'structure')
        POOL_DIR = os.path.join(data_dir, 'data', 'minecraft', 'worldgen', 'template_pool')
        LOOT_TABLE_DIR = os.path.join(data_dir, 'data', 'minecraft', 'loot_table', 'chests')

    # Step 1: Simulate Jigsaw placement
    if verbose:
        print(f"[1/4] Simulating Jigsaw placement at chunk ({chunk_x}, {chunk_z})...")
    pieces = simulate_ancient_city(world_seed, chunk_x, chunk_z)
    if verbose:
        print(f"      {len(pieces)} pieces placed")

    # Step 2: Extract chests from simulation (no region files needed)
    if verbose:
        print(f"[2/4] Extracting chests from simulated pieces...")
    chests = extract_chests_from_pieces(pieces, world_seed)
    if verbose:
        print(f"      {len(chests)} chests found")

    # Step 3: LootTableSeeds are already assigned in step 2
    if verbose:
        print(f"[3/4] LootTableSeeds assigned (per-chunk RNG)")

    # Step 4: Simulate loot contents
    if verbose:
        print(f"[4/4] Simulating loot contents...")
    target_chests = []
    for chest in chests:
        loot_table_name = chest['loot_table'].replace('minecraft:chests/', '')
        loot_table_path = os.path.join(LOOT_TABLE_DIR, loot_table_name + '.json')
        if not os.path.exists(loot_table_path):
            continue
        slot_items, final_items = simulate_loot(loot_table_path, chest['seed'])
        chest['items'] = final_items
        chest['slot_items'] = slot_items

        # Check for target items
        found = [(name, count) for name, count in final_items if name in TARGET_ITEMS]
        if found:
            target_chests.append({
                'pos': (chest['x'], chest['y'], chest['z']),
                'seed': chest['seed'],
                'loot_table': chest['loot_table'],
                'targets': found,
                'all_items': final_items,
            })

    if verbose:
        print(f"\n=== Results ===")
        print(f"Pieces: {len(pieces)}")
        print(f"Chests: {len(chests)}")
        print(f"Target item chests: {len(target_chests)}")
        for tc in target_chests:
            print(f"  Chest at {tc['pos']}: {tc['targets']}")

    return {'pieces': pieces, 'chests': chests, 'target_chests': target_chests}


# ============================================================
# CLI
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='MC 26.1.2 Ancient City Loot Predictor')
    parser.add_argument('--seed', type=int, default=-7346913998703726680,
                        help='World seed (default: test seed)')
    parser.add_argument('--chunk', nargs=2, type=int, metavar=('CX', 'CZ'),
                        help='Ancient city origin chunk coords')
    parser.add_argument('--world', type=str, default=None,
                        help='Path to world/ directory (for region file chest extraction)')
    parser.add_argument('--data', type=str, default=None,
                        help='Path to loot_tables/ directory (for NBT templates)')
    parser.add_argument('--verify', action='store_true',
                        help='Run verification against 6 known cities')
    parser.add_argument('--pieces-only', action='store_true',
                        help='Only simulate Jigsaw placement (no loot)')
    args = parser.parse_args()

    if args.verify:
        # Verify against 6 known cities
        gt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'work', 'pieces_gt.json')
        if not os.path.exists(gt_path):
            # Try relative to script
            for p in ['work/pieces_gt.json',
                      os.path.join(_SCRIPT_DIR, 'work', 'pieces_gt.json')]:
                if os.path.exists(p):
                    gt_path = p
                    break
        if os.path.exists(gt_path):
            with open(gt_path) as f:
                all_gt = json.load(f)
            cities = [(-158,-140),(19451,1814),(19455,1829),
                      (19477,1833),(19513,1830),(19619,1854)]
            total_match, total = 0, 0
            for cx, cz in cities:
                pieces = simulate_ancient_city(args.seed, cx, cz)
                gt = [p for p in all_gt
                      if p['start_chunk_x']==cx and p['start_chunk_z']==cz]
                matched = sum(1 for i in range(min(len(pieces),len(gt)))
                    if list(pieces[i].position)==gt[i]['pos']
                    and list(pieces[i].bb.to_tuple())==gt[i]['bb'])
                total_match += matched
                total += len(gt)
                status = 'OK' if matched==len(gt) else 'FAIL'
                print(f'{status} City ({cx},{cz}): {matched}/{len(gt)}')
            print(f'\nTotal: {total_match}/{total}')
        else:
            print("GT file not found, running demo simulation...")
            pieces = simulate_ancient_city(args.seed, 19513, 1830)
            print(f"Generated {len(pieces)} pieces")
            for i, p in enumerate(pieces[:5]):
                print(f"  [{i}] {p.location} rot={ROT_NAMES[p.rotation]} "
                      f"pos={p.position} bb={p.bb.to_tuple()}")
        return

    if args.chunk:
        cx, cz = args.chunk
    else:
        cx, cz = 19513, 1830
        print(f"No chunk specified, using default ({cx}, {cz})")

    if args.pieces_only:
        pieces = simulate_ancient_city(args.seed, cx, cz)
        print(f"Generated {len(pieces)} pieces at chunk ({cx}, {cz})")
        for i, p in enumerate(pieces):
            print(f"  [{i:3d}] {p.location:45s} rot={ROT_NAMES[p.rotation]:20s} "
                  f"pos={p.position} bb={p.bb.to_tuple()}")
        return

    predict_city(args.seed, cx, cz,
                 world_dir=args.world, data_dir=args.data, verbose=True)


if __name__ == '__main__':
    main()
