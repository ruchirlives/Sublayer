import pathlib
import struct
import tempfile
import unittest
from vdb_metadata import density_bounds
from usd_graph import _read_vdb_grid_names


def blob(value):
    return struct.pack('<I', len(value)) + value


def fixture(map_type=b'UniformScaleTranslateMap', values=(10.,20.,30.,2.,2.,2.)):
    meta = struct.pack('<I',3)
    for key, typ, value in [(b'class',b'string',b'fog volume'),
                            (b'file_bbox_min',b'vec3i',struct.pack('<iii',0,1,2)),
                            (b'file_bbox_max',b'vec3i',struct.pack('<iii',3,4,5))]:
        meta += blob(key)+blob(typ)+blob(value)
    data = struct.pack('<I',6)+meta+blob(map_type)+struct.pack('<'+'d'*len(values),*values)
    header = b' BDV\0\0\0\0'+struct.pack('<III?',223,12,1,True)+b'0'*36+struct.pack('<II',0,1)
    descriptor = blob(b'density')+blob(b'Tree_float_5_4_3')+blob(b'')
    start = len(header)+len(descriptor)+24
    return header+descriptor+struct.pack('<qqq',start,start+len(data),start+len(data))+data


class VdbMetadataTests(unittest.TestCase):
    def test_supported_linear_maps_and_real_header_grid_names(self):
        with tempfile.TemporaryDirectory() as folder:
            p=pathlib.Path(folder)/'test.vdb'
            p.write_bytes(fixture())
            center,size=density_bounds(p)
            self.assertEqual(center,[13,25,37])
            self.assertEqual(size,[8,8,8])
            self.assertEqual(_read_vdb_grid_names(p),['density'])
            p.write_bytes(fixture(values=(10.,20.,30.,-2.,2.,2.)))
            center,size=density_bounds(p)
            self.assertEqual(center,[7,25,37])
            self.assertEqual(size,[8,8,8])
            matrix=(1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,10.,20.,30.,1.)
            p.write_bytes(fixture(b'AffineMap',matrix))
            center,size=density_bounds(p)
            self.assertEqual(center,[11.5,22.5,33.5])
            self.assertEqual(size,[4,4,4])

    def test_truncated_unsupported_and_unbounded_metadata_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            p=pathlib.Path(folder)/'test.vdb'
            for data in (b'',fixture()[:90], fixture(b'NonlinearFrustumMap'),
                         fixture(values=(float('nan'),20.,30.,2.,2.,2.)),
                         fixture()[:57]+struct.pack('<I',1000000)):
                p.write_bytes(data)
                with self.assertRaises(ValueError): density_bounds(p)


if __name__ == '__main__': unittest.main()
