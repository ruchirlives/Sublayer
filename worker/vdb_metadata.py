"""Bounded metadata-only OpenVDB reader; voxel decoding stays in the RTX runtime.

Layout follows upstream OpenVDB io/Archive.cc, GridDescriptor.cc and math/Maps.h.
Only seekable format 222/223/224 and supported linear maps are interpreted.
"""
import itertools
import math
import os
import struct


def density_bounds(path):
    with open(path, 'rb') as stream:
        file_size = os.fstat(stream.fileno()).st_size
        budget = 16 * 1024 * 1024
        def read(n):
            nonlocal budget
            if not 0 <= n <= budget:
                raise ValueError('VDB metadata exceeds supported limits')
            budget -= n
            data = stream.read(n)
            if len(data) != n:
                raise ValueError('Truncated VDB metadata')
            return data
        def unpack(fmt):
            return struct.unpack(fmt, read(struct.calcsize(fmt)))
        def blob(limit=1024 * 1024):
            n, = unpack('<I')
            if n > limit:
                raise ValueError('VDB metadata field exceeds supported limits')
            return read(n)
        def metadata():
            count, = unpack('<I')
            if count > 1024:
                raise ValueError('Too many VDB metadata fields')
            result = {}
            for _ in range(count):
                name, kind = blob(1024), blob(1024)
                result[name] = (kind, blob())
            return result
        if read(8) != b' BDV\0\0\0\0':
            raise ValueError('Unsupported OpenVDB header')
        version, major, minor, offsets = unpack('<III?')
        if version not in (222, 223, 224) or not offsets:
            raise ValueError('Unsupported OpenVDB metadata version or non-seekable file')
        read(36)  # UUID string
        metadata()
        count, = unpack('<I')
        if count > 64:
            raise ValueError('Too many VDB grids')
        for _ in range(count):
            name, kind, parent = blob(1024), blob(1024), blob(1024)
            grid_pos, block_pos, end_pos = unpack('<qqq')
            if not stream.tell() <= grid_pos < block_pos <= end_pos <= file_size:
                raise ValueError('Invalid VDB grid offsets')
            if name != b'density':
                stream.seek(end_pos)
                continue
            if parent or not kind.startswith(b'Tree_float_'):
                raise ValueError('Fog requires a non-instanced float density grid')
            stream.seek(grid_pos)
            read(4)  # per-grid compression flags
            meta = metadata()
            def bound(key):
                typ, value = meta.get(key, (None, None))
                if typ != b'vec3i' or len(value) != 12:
                    raise ValueError('VDB has no recorded active voxel bounds; set manual Fog bounds')
                return struct.unpack('<iii', value)
            lower, upper = bound(b'file_bbox_min'), bound(b'file_bbox_max')
            if any(upper[i] < lower[i] for i in range(3)):
                raise ValueError('Invalid VDB active voxel bounds')
            if meta.get(b'class') != (b'string', b'fog volume'):
                raise ValueError('VDB density grid must be a fog volume, not a level set')
            map_type = blob(1024)
            scale, translate = (1., 1., 1.), (0., 0., 0.)
            matrix = None
            if map_type in (b'UniformScaleTranslateMap', b'ScaleTranslateMap'):
                translate, scale = unpack('<3d'), unpack('<3d')
            elif map_type in (b'UniformScaleMap', b'ScaleMap'):
                scale = unpack('<3d')
            elif map_type == b'TranslationMap':
                translate = unpack('<3d')
            elif map_type in (b'AffineMap', b'UnitaryMap'):
                matrix = unpack('<16d')
                if matrix[3] or matrix[7] or matrix[11] or matrix[15] != 1:
                    raise ValueError('Unsupported VDB projective map')
            else:
                raise ValueError('Unsupported VDB map; set manual Fog bounds')
            corners = []
            for p in itertools.product(*[(lower[i] - .5, upper[i] + .5) for i in range(3)]):
                corners.append(tuple(sum(p[j] * matrix[j*4+i] for j in range(3)) + matrix[12+i] if matrix else p[i] * scale[i] + translate[i] for i in range(3)))
            lo, hi = ([min(p[i] for p in corners) for i in range(3)], [max(p[i] for p in corners) for i in range(3)])
            if any(not math.isfinite(v) or abs(v) > 1e12 for v in lo + hi) or any(hi[i] <= lo[i] for i in range(3)):
                raise ValueError('Invalid VDB bounds')
            return [(lo[i]+hi[i])*.5 for i in range(3)], [hi[i]-lo[i] for i in range(3)]
    raise ValueError('VDB has no density grid')
