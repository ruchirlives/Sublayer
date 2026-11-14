"""Procedural MaterialX node-graph compiler for OpenUSD and .mtlx export."""
from collections import deque
import hashlib
import json
import re
import xml.etree.ElementTree as ET


STANDARD_SURFACE_PORTS = {
    'base_color': ('color3', 'Color3f'),
    'base': ('float', 'Float'),
    'metalness': ('float', 'Float'),
    'specular': ('float', 'Float'),
    'specular_color': ('color3', 'Color3f'),
    'specular_roughness': ('float', 'Float'),
    'specular_IOR': ('float', 'Float'),
    'coat': ('float', 'Float'),
    'coat_roughness': ('float', 'Float'),
    'coat_color': ('color3', 'Color3f'),
    'sheen': ('float', 'Float'),
    'sheen_roughness': ('float', 'Float'),
    'sheen_color': ('color3', 'Color3f'),
    'transmission': ('float', 'Float'),
    'transmission_color': ('color3', 'Color3f'),
    'emission': ('float', 'Float'),
    'emission_color': ('color3', 'Color3f'),
    'subsurface': ('float', 'Float'),
    'subsurface_color': ('color3', 'Color3f'),
    'subsurface_scale': ('float', 'Float'),
    'subsurface_radius': ('color3', 'Color3f'),
    'opacity': ('color3', 'Color3f'),
    'normal': ('vector3', 'Float3'),
}

# Maps procedural node kind -> spec.
# Dynamic-type math/color nodes ('mix', 'multiply', 'add', 'remap', 'clamp', 'contrast')
# resolve their suffix ('_float' or '_color3') from connected inputs or params['valueType'].
PROCEDURAL_NODE_SPECS = {
    'uv': {
        'mtlx': 'geompropvalue',
        'out_type': 'vector2',
        'inputs': {'geomprop': ('string', 'st')},
    },
    'place2d': {
        'mtlx': 'place2d',
        'out_type': 'vector2',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'pivot': ('vector2', [0.0, 0.0]),
            'scale': ('vector2', [1.0, 1.0]),
            'rotate': ('float', 0.0),
            'offset': ('vector2', [0.0, 0.0]),
        },
    },
    'position': {
        'mtlx': 'position',
        'out_type': 'vector3',
        'inputs': {'space': ('string', 'object')},
    },
    'place3d': {
        'mtlx': 'place3d',
        'out_type': 'vector3',
        'auto_coord': 'object',
        'inputs': {
            'position': ('vector3', None),
            'scale': ('vector3', [1.0, 1.0, 1.0]),
            'rotate': ('float', 0.0),
            'offset': ('vector3', [0.0, 0.0, 0.0]),
        },
    },
    'noise2d': {
        'mtlx': 'noise2d',
        'out_type': 'float',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'amplitude': ('float', 1.0),
            'pivot': ('float', 0.0),
        },
    },
    'noise3d': {
        'mtlx': 'noise3d',
        'out_type': 'float',
        'auto_coord': 'object',
        'inputs': {
            'position': ('vector3', None),
            'amplitude': ('float', 1.0),
            'pivot': ('float', 0.0),
        },
    },
    'fractal3d': {
        'mtlx': 'fractal3d',
        'out_type': 'float',
        'auto_coord': 'object',
        'inputs': {
            'position': ('vector3', None),
            'amplitude': ('float', 1.0),
            'octaves': ('integer', 3),
            'lacunarity': ('float', 2.0),
            'diminish': ('float', 0.5),
        },
    },
    'worley2d': {
        'mtlx': 'worleynoise2d',
        'out_type': 'float',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'jitter': ('float', 1.0),
        },
    },
    'worley3d': {
        'mtlx': 'worleynoise3d',
        'out_type': 'float',
        'auto_coord': 'object',
        'inputs': {
            'position': ('vector3', None),
            'jitter': ('float', 1.0),
        },
    },
    'cell2d': {
        'mtlx': 'cellnoise2d',
        'out_type': 'float',
        'auto_coord': 'uv',
        'inputs': {'texcoord': ('vector2', None)},
    },
    'cell3d': {
        'mtlx': 'cellnoise3d',
        'out_type': 'float',
        'auto_coord': 'object',
        'inputs': {'position': ('vector3', None)},
    },
    'unified2d': {
        'mtlx': 'unifiednoise2d',
        'out_type': 'float',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'type': ('integer', 0),
            'freq': ('vector2', [1.0, 1.0]),
            'offset': ('vector2', [0.0, 0.0]),
            'jitter': ('float', 1.0),
            'octaves': ('integer', 3),
            'lacunarity': ('float', 2.0),
            'diminish': ('float', 0.5),
            'outmin': ('float', 0.0),
            'outmax': ('float', 1.0),
            'clampoutput': ('boolean', True),
        },
    },
    'unified3d': {
        'mtlx': 'unifiednoise3d',
        'out_type': 'float',
        'auto_coord': 'object',
        'inputs': {
            'position': ('vector3', None),
            'type': ('integer', 0),
            'freq': ('vector3', [1.0, 1.0, 1.0]),
            'offset': ('vector3', [0.0, 0.0, 0.0]),
            'jitter': ('float', 1.0),
            'octaves': ('integer', 3),
            'lacunarity': ('float', 2.0),
            'diminish': ('float', 0.5),
            'outmin': ('float', 0.0),
            'outmax': ('float', 1.0),
            'clampoutput': ('boolean', True),
        },
    },
    'checkerboard': {
        'mtlx': 'checkerboard',
        'out_type': 'color3',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'color1': ('color3', [0.05, 0.05, 0.05]),
            'color2': ('color3', [0.95, 0.95, 0.95]),
            'uvtiling': ('vector2', [4.0, 4.0]),
            'uvoffset': ('vector2', [0.0, 0.0]),
        },
    },
    'grid': {
        'mtlx': 'grid',
        'out_type': 'color3',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'uvtiling': ('vector2', [4.0, 4.0]),
            'uvoffset': ('vector2', [0.0, 0.0]),
            'thickness': ('float', 0.05),
            'staggered': ('boolean', False),
        },
    },
    'tiledcircles': {
        'mtlx': 'tiledcircles',
        'out_type': 'color3',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'uvtiling': ('vector2', [4.0, 4.0]),
            'uvoffset': ('vector2', [0.0, 0.0]),
            'size': ('float', 0.5),
            'staggered': ('boolean', False),
        },
    },
    'tiledhexagons': {
        'mtlx': 'tiledhexagons',
        'out_type': 'color3',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'uvtiling': ('vector2', [4.0, 4.0]),
            'uvoffset': ('vector2', [0.0, 0.0]),
            'size': ('float', 0.5),
            'staggered': ('boolean', False),
        },
    },
    'ramplr': {
        'mtlx': 'ramplr',
        'out_type': 'color3',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'valuel': ('color3', [0.0, 0.0, 0.0]),
            'valuer': ('color3', [1.0, 1.0, 1.0]),
        },
    },
    'ramptb': {
        'mtlx': 'ramptb',
        'out_type': 'color3',
        'auto_coord': 'uv',
        'inputs': {
            'texcoord': ('vector2', None),
            'valuet': ('color3', [1.0, 1.0, 1.0]),
            'valueb': ('color3', [0.0, 0.0, 0.0]),
        },
    },
    'mix': {
        'mtlx': 'mix',
        'out_type': 'dynamic',
        'inputs': {
            'fg': ('dynamic', [1.0, 1.0, 1.0]),
            'bg': ('dynamic', [0.0, 0.0, 0.0]),
            'mix': ('float', 0.5),
        },
    },
    'multiply': {
        'mtlx': 'multiply',
        'out_type': 'dynamic',
        'inputs': {
            'in1': ('dynamic', 1.0),
            'in2': ('dynamic', 1.0),
        },
    },
    'add': {
        'mtlx': 'add',
        'out_type': 'dynamic',
        'inputs': {
            'in1': ('dynamic', 0.0),
            'in2': ('dynamic', 0.0),
        },
    },
    'remap': {
        'mtlx': 'remap',
        'out_type': 'dynamic',
        'inputs': {
            'in': ('dynamic', 0.0),
            'inlow': ('dynamic', 0.0),
            'inhigh': ('dynamic', 1.0),
            'outlow': ('dynamic', 0.0),
            'outhigh': ('dynamic', 1.0),
        },
    },
    'clamp': {
        'mtlx': 'clamp',
        'out_type': 'dynamic',
        'inputs': {
            'in': ('dynamic', 0.0),
            'low': ('dynamic', 0.0),
            'high': ('dynamic', 1.0),
        },
    },
    'contrast': {
        'mtlx': 'contrast',
        'out_type': 'dynamic',
        'inputs': {
            'in': ('dynamic', 0.0),
            'amount': ('dynamic', 1.0),
            'pivot': ('dynamic', 0.5),
        },
    },
    'saturate': {
        'mtlx': 'saturate',
        'out_type': 'color3',
        'inputs': {
            'in': ('color3', [0.5, 0.5, 0.5]),
            'amount': ('float', 1.0),
        },
    },
    'hsvadjust': {
        'mtlx': 'hsvadjust',
        'out_type': 'color3',
        'inputs': {
            'in': ('color3', [0.5, 0.5, 0.5]),
            'amount': ('vector3', [0.0, 1.0, 1.0]),
        },
    },
    'bump': {
        'mtlx': 'bump',
        'out_type': 'vector3',
        'inputs': {
            'height': ('float', 0.0),
            'scale': ('float', 0.1),
        },
    },
    'colorramp': {
        'mtlx': 'colorramp',
        'out_type': 'color3',
        'inputs': {
            'in': ('float', 0.5),
        },
    },
}


def _safe_ident(raw):
    clean = re.sub(r'[^A-Za-z0-9_]', '_', str(raw or 'node'))
    if not clean or clean[0].isdigit():
        clean = 'N_' + clean
    return clean[:64]


def _sdf_type(mtlx_type):
    from pxr import Sdf
    return {
        'float': Sdf.ValueTypeNames.Float,
        'vector2': Sdf.ValueTypeNames.Float2,
        'vector3': Sdf.ValueTypeNames.Float3,
        'color3': Sdf.ValueTypeNames.Color3f,
        'integer': Sdf.ValueTypeNames.Int,
        'boolean': Sdf.ValueTypeNames.Bool,
        'string': Sdf.ValueTypeNames.String,
    }[mtlx_type]


def _sdf_value(mtlx_type, val):
    from pxr import Gf
    if mtlx_type == 'float':
        if isinstance(val, (list, tuple)):
            return float(val[0]) if val else 0.0
        return float(val)
    if mtlx_type == 'integer':
        return int(val)
    if mtlx_type == 'boolean':
        return bool(val)
    if mtlx_type == 'string':
        return str(val)
    if mtlx_type == 'vector2':
        if isinstance(val, (int, float)):
            return Gf.Vec2f(float(val), float(val))
        return Gf.Vec2f(float(val[0]), float(val[1]))
    if mtlx_type in ('vector3', 'color3'):
        if isinstance(val, (int, float)):
            return Gf.Vec3f(float(val), float(val), float(val))
        return Gf.Vec3f(float(val[0]), float(val[1]), float(val[2]))
    return val


def _xml_value(mtlx_type, val):
    if mtlx_type == 'boolean':
        return 'true' if bool(val) else 'false'
    if mtlx_type == 'integer':
        return str(int(val))
    if mtlx_type == 'float':
        if isinstance(val, (list, tuple)):
            return str(float(val[0]) if val else 0.0)
        return str(float(val))
    if mtlx_type == 'string':
        return str(val)
    if mtlx_type == 'vector2':
        if isinstance(val, (int, float)):
            return f'{float(val)}, {float(val)}'
        return f'{float(val[0])}, {float(val[1])}'
    if mtlx_type in ('vector3', 'color3'):
        if isinstance(val, (int, float)):
            return f'{float(val)}, {float(val)}, {float(val)}'
        return f'{float(val[0])}, {float(val[1])}, {float(val[2])}'
    return str(val)


def validate_procedural_graph(procedural_graph):
    """Validate proceduralGraph DAG and return (nodes_by_id, incoming_edges_by_target)."""
    if not procedural_graph:
        return {}, {}
    nodes = procedural_graph.get('nodes') or []
    edges = procedural_graph.get('edges') or []
    nodes_by_id = {}
    indegree = {}
    outgoing = {}
    for n in nodes:
        nid = n.get('id')
        if not nid or nid in nodes_by_id:
            raise ValueError('Duplicate or empty procedural node ID')
        kind = (n.get('data') or {}).get('kind')
        if kind != 'output' and kind not in PROCEDURAL_NODE_SPECS:
            raise ValueError(f'Unknown procedural node kind: {kind}')
        nodes_by_id[nid] = n
        indegree[nid] = 0
        outgoing[nid] = []

    incoming = {}
    for e in edges:
        src, dst = e.get('source'), e.get('target')
        if not src or not dst or src == dst or src not in nodes_by_id or dst not in nodes_by_id:
            raise ValueError('Invalid or dangling procedural edge')
        indegree[dst] += 1
        outgoing[src].append(dst)
        handle = e.get('targetHandle') or 'in'
        incoming.setdefault(dst, {})[handle] = (src, e.get('sourceHandle') or 'out')

    queue = deque([nid for nid, deg in indegree.items() if deg == 0])
    visited = 0
    while queue:
        cur = queue.popleft()
        visited += 1
        for nxt in outgoing[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if visited != len(nodes_by_id):
        raise ValueError('Procedural graph contains a cycle')
    return nodes_by_id, incoming


def collect_rule_subgraph(procedural_graph, mx_node_id, rule_id):
    """Collect enabled Output nodes for (mx_node_id, rule_id) and their reachable upstream DAG."""
    if not procedural_graph:
        return None
    nodes_by_id, incoming = validate_procedural_graph(procedural_graph)
    outputs = []
    for n in procedural_graph.get('nodes') or []:
        d = n.get('data') or {}
        if d.get('kind') != 'output' or not d.get('enabled', True):
            continue
        if d.get('materialXNodeId') != mx_node_id:
            continue
        if rule_id is not None and d.get('ruleId') not in (rule_id, '*', None):
            continue
        if d.get('parameter') not in STANDARD_SURFACE_PORTS:
            continue
        if n['id'] in incoming:
            outputs.append(n)
    if not outputs:
        return None

    needed = set()
    stack = [o['id'] for o in outputs]
    while stack:
        cur = stack.pop()
        if cur in needed:
            continue
        needed.add(cur)
        for src, _ in incoming.get(cur, {}).values():
            stack.append(src)

    sub_nodes = [nodes_by_id[nid] for nid in sorted(needed)]
    sub_edges = [
        e for e in (procedural_graph.get('edges') or [])
        if e.get('source') in needed and e.get('target') in needed
    ]
    return {'nodes': sub_nodes, 'edges': sub_edges, 'outputs': outputs}


def _normalize_ramp_stops(params):
    raw = (params or {}).get('stops')
    if not isinstance(raw, list) or not raw:
        return [
            (0.0, [0.0, 0.0, 0.0]),
            (1.0, [1.0, 1.0, 1.0]),
        ]
    stops = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pos = max(0.0, min(1.0, float(item.get('pos', 0.0))))
        col = item.get('color', [0.0, 0.0, 0.0])
        if not isinstance(col, (list, tuple)) or len(col) < 3:
            col = [0.0, 0.0, 0.0]
        stops.append((pos, [float(col[0]), float(col[1]), float(col[2])]))
    if not stops:
        return [(0.0, [0.0, 0.0, 0.0]), (1.0, [1.0, 1.0, 1.0])]
    stops.sort(key=lambda s: s[0])
    return stops


def _node_topo_entry(n):
    d = n.get('data') or {}
    kind = d.get('kind')
    params = d.get('params') or {}
    entry = {
        'id': n.get('id'),
        'kind': kind,
        'valueType': params.get('valueType'),
    }
    if kind == 'colorramp':
        entry['stopCount'] = len(_normalize_ramp_stops(params))
        entry['interpolation'] = params.get('interpolation') or 'linear'
    return entry


def _subgraph_topology(subgraph):
    if not subgraph:
        return None
    nodes = [_node_topo_entry(n) for n in (subgraph.get('nodes') or [])]
    nodes.sort(key=lambda item: str(item.get('id') or ''))
    edges = [
        {
            'source': e.get('source'),
            'sourceHandle': e.get('sourceHandle') or 'out',
            'target': e.get('target'),
            'targetHandle': e.get('targetHandle') or 'in',
        }
        for e in (subgraph.get('edges') or [])
    ]
    edges.sort(key=lambda item: (str(item['source']), str(item['sourceHandle']), str(item['target']), str(item['targetHandle'])))
    outputs = [
        {
            'id': o.get('id'),
            'materialXNodeId': (o.get('data') or {}).get('materialXNodeId'),
            'ruleId': (o.get('data') or {}).get('ruleId'),
            'parameter': (o.get('data') or {}).get('parameter'),
            'mappingType': (o.get('data') or {}).get('mappingType') or 'replace',
            'enabled': (o.get('data') or {}).get('enabled', True) is not False,
        }
        for o in (subgraph.get('outputs') or [])
    ]
    outputs.sort(key=lambda item: str(item.get('id') or ''))
    return {'nodes': nodes, 'edges': edges, 'outputs': outputs}


def graph_topology_signature(procedural_graph):
    """Return a canonical topology-only signature for an entire proceduralGraph."""
    if not procedural_graph:
        return None
    nodes = []
    for n in (procedural_graph.get('nodes') or []):
        d = n.get('data') or {}
        kind = d.get('kind')
        if kind == 'output':
            nodes.append({
                'id': n.get('id'),
                'kind': kind,
                'materialXNodeId': d.get('materialXNodeId'),
                'ruleId': d.get('ruleId'),
                'parameter': d.get('parameter'),
                'mappingType': d.get('mappingType') or 'replace',
                'enabled': d.get('enabled', True) is not False,
            })
        else:
            nodes.append(_node_topo_entry(n))
    nodes.sort(key=lambda item: str(item.get('id') or ''))
    edges = [
        {
            'source': e.get('source'),
            'sourceHandle': e.get('sourceHandle') or 'out',
            'target': e.get('target'),
            'targetHandle': e.get('targetHandle') or 'in',
        }
        for e in (procedural_graph.get('edges') or [])
    ]
    edges.sort(key=lambda item: (str(item['source']), str(item['sourceHandle']), str(item['target']), str(item['targetHandle'])))
    return {'nodes': nodes, 'edges': edges}


def subgraph_hash(subgraph):
    topo = _subgraph_topology(subgraph)
    if not topo:
        return None
    return hashlib.sha256(
        json.dumps(topo, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()[:16]


def _resolve_node_types(ordered_nodes, nodes_by_id, incoming):
    """Infer concrete output types ('float' | 'color3' | 'vector2' | 'vector3') for dynamic nodes."""
    out_types = {}
    for nid in ordered_nodes:
        node = nodes_by_id[nid]
        d = node.get('data') or {}
        kind = d.get('kind')
        if kind == 'output':
            continue
        spec = PROCEDURAL_NODE_SPECS[kind]
        if spec['out_type'] != 'dynamic':
            out_types[nid] = spec['out_type']
            continue
        params = d.get('params') or {}
        explicit = params.get('valueType')
        if explicit in ('float', 'color3', 'vector3'):
            out_types[nid] = explicit
            continue
        # Infer vector3 for multiply/add when fed a vector3 (e.g. Position 3D / Place 3D);
        # otherwise infer color3 if any connected upstream input is color3/vector3 or any color param is a list.
        inferred = 'float'
        if kind in ('multiply', 'add') and any(out_types.get(src) == 'vector3' for src, _ in incoming.get(nid, {}).values()):
            inferred = 'vector3'
        else:
            for handle, (src, _) in incoming.get(nid, {}).items():
                if out_types.get(src) in ('color3', 'vector3') and not (kind == 'mix' and handle == 'mix'):
                    inferred = 'color3'
                    break
        if inferred == 'float':
            for k in ('fg', 'bg', 'in1', 'in2', 'in', 'outlow', 'outhigh'):
                if isinstance(params.get(k), (list, tuple)):
                    inferred = 'color3'
                    break
        out_types[nid] = inferred
    return out_types


def _topo_order(nodes, edges):
    indegree = {n['id']: 0 for n in nodes}
    outgoing = {n['id']: [] for n in nodes}
    for e in edges:
        indegree[e['target']] += 1
        outgoing[e['source']].append(e['target'])
    queue = deque([nid for nid, deg in indegree.items() if deg == 0])
    order = []
    while queue:
        cur = queue.popleft()
        order.append(cur)
        for nxt in outgoing[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return order


def _convert_usd(stage, mat_path, suffix, src_api, src_port, src_type, target_type):
    """Insert a USD MaterialX type-conversion shader when src_type != target_type."""
    from pxr import UsdShade, Sdf
    if src_type == target_type:
        return src_api, src_port
    if src_type == 'float' and target_type == 'color3':
        conv = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcConv_{suffix}'))
        conv.CreateIdAttr('ND_convert_float_color3')
        conv.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(src_api, src_port)
        conv.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
        return conv.ConnectableAPI(), 'out'
    if src_type == 'float' and target_type == 'vector3':
        conv = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcConv_{suffix}'))
        conv.CreateIdAttr('ND_convert_float_vector3')
        conv.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(src_api, src_port)
        conv.CreateOutput('out', Sdf.ValueTypeNames.Float3)
        return conv.ConnectableAPI(), 'out'
    if src_type == 'color3' and target_type == 'float':
        lum = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcLum_{suffix}'))
        lum.CreateIdAttr('ND_luminance_color3')
        lum.CreateInput('in', Sdf.ValueTypeNames.Color3f).ConnectToSource(src_api, src_port)
        lum.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
        ext = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcExt_{suffix}'))
        ext.CreateIdAttr('ND_extract_color3')
        ext.CreateInput('in', Sdf.ValueTypeNames.Color3f).ConnectToSource(lum.ConnectableAPI(), 'out')
        ext.CreateInput('index', Sdf.ValueTypeNames.Int).Set(0)
        ext.CreateOutput('out', Sdf.ValueTypeNames.Float)
        return ext.ConnectableAPI(), 'out'
    return src_api, src_port


def author_procedural_graph_usd(stage, mat_path, surface, subgraph, get_uv_coord, base_values):
    """Author a proceduralGraph subgraph into USD MaterialX shaders and bind to StandardSurface."""
    from pxr import UsdShade, Sdf
    if not subgraph:
        return
    nodes_by_id = {n['id']: n for n in subgraph['nodes']}
    incoming = {}
    for e in subgraph['edges']:
        incoming.setdefault(e['target'], {})[e.get('targetHandle') or 'in'] = (
            e['source'],
            e.get('sourceHandle') or 'out',
        )

    order = _topo_order(subgraph['nodes'], subgraph['edges'])
    out_types = _resolve_node_types(order, nodes_by_id, incoming)
    built = {}  # nid -> (ConnectableAPI, out_port, out_type)

    for nid in order:
        node = nodes_by_id[nid]
        d = node.get('data') or {}
        kind = d.get('kind')
        if kind == 'output':
            continue
        spec = PROCEDURAL_NODE_SPECS[kind]
        out_type = out_types[nid]
        params = d.get('params') or {}
        node_in = incoming.get(nid, {})

        if kind == 'place3d':
            ident = _safe_ident(nid)
            # Position source: connected input or default object-space Position 3D
            if 'position' in node_in:
                src_nid, _ = node_in['position']
                pos_api, pos_port, src_t = built[src_nid]
                pos_api, pos_port = _convert_usd(stage, mat_path, f'{ident}_pos', pos_api, pos_port, src_t, 'vector3')
            else:
                coord_shader, _ = get_uv_coord('object')
                pos_api, pos_port = (coord_shader.ConnectableAPI(), 'out') if coord_shader is not None else (None, None)

            mul_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{ident}_mul'))
            mul_sh.CreateIdAttr('ND_multiply_vector3')
            in1 = mul_sh.CreateInput('in1', Sdf.ValueTypeNames.Float3)
            if pos_api is not None:
                in1.ConnectToSource(pos_api, pos_port)
            else:
                in1.Set(_sdf_value('vector3', [0.0, 0.0, 0.0]))
            in2 = mul_sh.CreateInput('in2', Sdf.ValueTypeNames.Float3)
            if 'scale' in node_in:
                s_nid, _ = node_in['scale']
                s_api, s_port, s_t = built[s_nid]
                s_api, s_port = _convert_usd(stage, mat_path, f'{ident}_scale', s_api, s_port, s_t, 'vector3')
                in2.ConnectToSource(s_api, s_port)
            else:
                in2.Set(_sdf_value('vector3', params.get('scale', [1.0, 1.0, 1.0])))
            mul_sh.CreateOutput('out', Sdf.ValueTypeNames.Float3)

            rot_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{ident}_rot'))
            rot_sh.CreateIdAttr('ND_rotate3d_vector3')
            rot_sh.CreateInput('in', Sdf.ValueTypeNames.Float3).ConnectToSource(mul_sh.ConnectableAPI(), 'out')
            rot_amt = rot_sh.CreateInput('amount', Sdf.ValueTypeNames.Float)
            if 'rotate' in node_in:
                r_nid, _ = node_in['rotate']
                r_api, r_port, r_t = built[r_nid]
                r_api, r_port = _convert_usd(stage, mat_path, f'{ident}_rot', r_api, r_port, r_t, 'float')
                rot_amt.ConnectToSource(r_api, r_port)
            else:
                rot_amt.Set(_sdf_value('float', params.get('rotate', 0.0)))
            rot_sh.CreateInput('axis', Sdf.ValueTypeNames.Float3).Set(_sdf_value('vector3', [0.0, 1.0, 0.0]))
            rot_sh.CreateOutput('out', Sdf.ValueTypeNames.Float3)

            add_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{ident}'))
            add_sh.CreateIdAttr('ND_add_vector3')
            add_sh.CreateInput('in1', Sdf.ValueTypeNames.Float3).ConnectToSource(rot_sh.ConnectableAPI(), 'out')
            off_in = add_sh.CreateInput('in2', Sdf.ValueTypeNames.Float3)
            if 'offset' in node_in:
                o_nid, _ = node_in['offset']
                o_api, o_port, o_t = built[o_nid]
                o_api, o_port = _convert_usd(stage, mat_path, f'{ident}_off', o_api, o_port, o_t, 'vector3')
                off_in.ConnectToSource(o_api, o_port)
            else:
                off_in.Set(_sdf_value('vector3', params.get('offset', [0.0, 0.0, 0.0])))
            add_sh.CreateOutput('out', Sdf.ValueTypeNames.Float3)
            built[nid] = (add_sh.ConnectableAPI(), 'out', 'vector3')
            continue

        if kind == 'colorramp':
            ident = _safe_ident(nid)
            stops = _normalize_ramp_stops(params)
            interp = params.get('interpolation') or 'linear'
            if 'in' in node_in:
                src_nid, _ = node_in['in']
                in_api, in_port, in_t = built[src_nid]
                in_api, in_port = _convert_usd(stage, mat_path, f'{ident}_in', in_api, in_port, in_t, 'float')
                in_const = None
            else:
                in_api, in_port = None, None
                in_const = float(params.get('in', 0.5))

            if len(stops) == 1:
                const_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{ident}'))
                const_sh.CreateIdAttr('ND_constant_color3')
                const_sh.CreateInput('value', Sdf.ValueTypeNames.Color3f).Set(_sdf_value('color3', stops[0][1]))
                const_sh.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
                built[nid] = (const_sh.ConnectableAPI(), 'out', 'color3')
                continue

            cur_api, cur_port, cur_color = None, None, stops[0][1]
            for seg_idx in range(len(stops) - 1):
                p0, _ = stops[seg_idx]
                p1, c1 = stops[seg_idx + 1]
                is_last = seg_idx == len(stops) - 2
                if interp == 'constant':
                    inlow = max(0.0, p1 - 1e-5)
                    inhigh = p1
                else:
                    inlow = p0
                    inhigh = p0 + max(p1 - p0, 1e-5)

                remap_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{ident}_r{seg_idx}'))
                remap_sh.CreateIdAttr('ND_remap_float')
                r_in = remap_sh.CreateInput('in', Sdf.ValueTypeNames.Float)
                if in_api is not None:
                    r_in.ConnectToSource(in_api, in_port)
                else:
                    r_in.Set(in_const)
                remap_sh.CreateInput('inlow', Sdf.ValueTypeNames.Float).Set(float(inlow))
                remap_sh.CreateInput('inhigh', Sdf.ValueTypeNames.Float).Set(float(inhigh))
                remap_sh.CreateInput('outlow', Sdf.ValueTypeNames.Float).Set(0.0)
                remap_sh.CreateInput('outhigh', Sdf.ValueTypeNames.Float).Set(1.0)
                remap_sh.CreateOutput('out', Sdf.ValueTypeNames.Float)

                clamp_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{ident}_c{seg_idx}'))
                clamp_sh.CreateIdAttr('ND_clamp_float')
                clamp_sh.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(remap_sh.ConnectableAPI(), 'out')
                clamp_sh.CreateInput('low', Sdf.ValueTypeNames.Float).Set(0.0)
                clamp_sh.CreateInput('high', Sdf.ValueTypeNames.Float).Set(1.0)
                clamp_sh.CreateOutput('out', Sdf.ValueTypeNames.Float)

                mix_name = f'Proc_{ident}' if is_last else f'Proc_{ident}_m{seg_idx}'
                mix_sh = UsdShade.Shader.Define(stage, mat_path.AppendChild(mix_name))
                mix_sh.CreateIdAttr('ND_mix_color3')
                bg_in = mix_sh.CreateInput('bg', Sdf.ValueTypeNames.Color3f)
                if cur_api is not None:
                    bg_in.ConnectToSource(cur_api, cur_port)
                else:
                    bg_in.Set(_sdf_value('color3', cur_color))
                mix_sh.CreateInput('fg', Sdf.ValueTypeNames.Color3f).Set(_sdf_value('color3', c1))
                mix_sh.CreateInput('mix', Sdf.ValueTypeNames.Float).ConnectToSource(clamp_sh.ConnectableAPI(), 'out')
                mix_sh.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
                cur_api, cur_port = mix_sh.ConnectableAPI(), 'out'

            built[nid] = (cur_api, cur_port, 'color3')
            continue

        sdr_id = f"ND_{spec['mtlx']}_{out_type}"
        shader = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'Proc_{_safe_ident(nid)}'))
        shader.CreateIdAttr(sdr_id)

        # Auto-wire coordinate input if unconnected
        auto_coord = spec.get('auto_coord')
        coord_port = 'texcoord' if auto_coord == 'uv' else 'position'
        if auto_coord and coord_port not in node_in:
            coord_shader, _ = get_uv_coord('object' if auto_coord == 'object' else 'uv')
            if coord_shader is not None:
                c_sdf = Sdf.ValueTypeNames.Float2 if auto_coord == 'uv' else Sdf.ValueTypeNames.Float3
                shader.CreateInput(coord_port, c_sdf).ConnectToSource(coord_shader.ConnectableAPI(), 'out')

        for port, (ptype, default_val) in spec['inputs'].items():
            resolved_ptype = out_type if ptype == 'dynamic' else ptype
            inp = shader.CreateInput(port, _sdf_type(resolved_ptype))
            if port in node_in:
                src_nid, src_port = node_in[port]
                src_api, src_out_port, src_t = built[src_nid]
                conv_api, conv_port = _convert_usd(
                    stage, mat_path, f'{_safe_ident(nid)}_{port}',
                    src_api, src_out_port, src_t, resolved_ptype,
                )
                inp.ConnectToSource(conv_api, conv_port)
            elif default_val is not None:
                val = params.get(port, default_val)
                if val is not None:
                    inp.Set(_sdf_value(resolved_ptype, val))

        shader.CreateOutput('out', _sdf_type(out_type))
        built[nid] = (shader.ConnectableAPI(), 'out', out_type)

    # Wire Output nodes to StandardSurface
    for idx, out_node in enumerate(subgraph['outputs']):
        oid = out_node['id']
        d = out_node.get('data') or {}
        param = d.get('parameter')
        if param not in STANDARD_SURFACE_PORTS:
            continue
        target_type, target_sdf_name = STANDARD_SURFACE_PORTS[param]
        target_sdf = getattr(Sdf.ValueTypeNames, target_sdf_name)
        mapping = d.get('mappingType') or 'replace'
        strength = float(d.get('strength') if d.get('strength') is not None else 1.0)

        in_conn = incoming.get(oid, {})
        if not in_conn:
            continue
        src_nid, _ = next(iter(in_conn.values()))
        src_api, src_port, src_type = built[src_nid]

        surf_input = surface.GetInput(param)
        if not surf_input:
            surf_input = surface.CreateInput(param, target_sdf)

        if mapping == 'bump_normal' or param == 'normal':
            h_api, h_port = _convert_usd(
                stage, mat_path, f'OutBump_{idx}', src_api, src_port, src_type, 'float'
            )
            bump = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcOutBump_{_safe_ident(oid)}'))
            bump.CreateIdAttr('ND_bump_vector3')
            bump.CreateInput('height', Sdf.ValueTypeNames.Float).ConnectToSource(h_api, h_port)
            bump.CreateInput('scale', Sdf.ValueTypeNames.Float).Set(strength)
            bump.CreateOutput('out', Sdf.ValueTypeNames.Float3)
            norm_input = surface.GetInput('normal') or surface.CreateInput('normal', Sdf.ValueTypeNames.Float3)
            norm_input.ConnectToSource(bump.ConnectableAPI(), 'out')
            continue

        proc_api, proc_port = _convert_usd(
            stage, mat_path, f'Out_{idx}', src_api, src_port, src_type, target_type
        )
        if mapping == 'replace':
            surf_input.ConnectToSource(proc_api, proc_port)
            continue

        # Compose with existing connection or base scalar/color value
        existing_sources = surf_input.GetConnectedSources()
        base_val = base_values.get(param)

        def _set_base_or_connect(inp_attr):
            if existing_sources and existing_sources[0]:
                src = existing_sources[0][0]
                inp_attr.ConnectToSource(src.source, src.sourceName)
            elif base_val is not None:
                inp_attr.Set(_sdf_value(target_type, base_val))

        if mapping == 'multiply':
            comp = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcMul_{_safe_ident(oid)}'))
            comp.CreateIdAttr(f'ND_multiply_{target_type}')
            _set_base_or_connect(comp.CreateInput('in1', target_sdf))
            comp.CreateInput('in2', target_sdf).ConnectToSource(proc_api, proc_port)
            comp.CreateOutput('out', target_sdf)
            surf_input.ConnectToSource(comp.ConnectableAPI(), 'out')
        elif mapping == 'add':
            scaled_api, scaled_port = proc_api, proc_port
            if strength != 1.0:
                scale_node = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcAddScale_{_safe_ident(oid)}'))
                scale_node.CreateIdAttr(f'ND_multiply_{target_type}')
                scale_node.CreateInput('in1', target_sdf).ConnectToSource(proc_api, proc_port)
                scale_node.CreateInput('in2', target_sdf).Set(_sdf_value(target_type, strength))
                scale_node.CreateOutput('out', target_sdf)
                scaled_api, scaled_port = scale_node.ConnectableAPI(), 'out'
            comp = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcAdd_{_safe_ident(oid)}'))
            comp.CreateIdAttr(f'ND_add_{target_type}')
            _set_base_or_connect(comp.CreateInput('in1', target_sdf))
            comp.CreateInput('in2', target_sdf).ConnectToSource(scaled_api, scaled_port)
            comp.CreateOutput('out', target_sdf)
            surf_input.ConnectToSource(comp.ConnectableAPI(), 'out')
        elif mapping == 'mix':
            comp = UsdShade.Shader.Define(stage, mat_path.AppendChild(f'ProcMix_{_safe_ident(oid)}'))
            comp.CreateIdAttr(f'ND_mix_{target_type}')
            _set_base_or_connect(comp.CreateInput('bg', target_sdf))
            comp.CreateInput('fg', target_sdf).ConnectToSource(proc_api, proc_port)
            comp.CreateInput('mix', Sdf.ValueTypeNames.Float).Set(max(0.0, min(1.0, strength)))
            comp.CreateOutput('out', target_sdf)
            surf_input.ConnectToSource(comp.ConnectableAPI(), 'out')


def _convert_xml(root, suffix, src_name, src_type, target_type):
    if src_type == target_type:
        return src_name
    if src_type == 'float' and target_type == 'color3':
        cname = f'ProcConv_{suffix}'
        el = ET.SubElement(root, 'convert', name=cname, type='color3')
        ET.SubElement(el, 'input', name='in', type='float', nodename=src_name)
        return cname
    if src_type == 'float' and target_type == 'vector3':
        cname = f'ProcConv_{suffix}'
        el = ET.SubElement(root, 'convert', name=cname, type='vector3')
        ET.SubElement(el, 'input', name='in', type='float', nodename=src_name)
        return cname
    if src_type == 'color3' and target_type == 'float':
        lname = f'ProcLum_{suffix}'
        lel = ET.SubElement(root, 'luminance', name=lname, type='color3')
        ET.SubElement(lel, 'input', name='in', type='color3', nodename=src_name)
        ename = f'ProcExt_{suffix}'
        eel = ET.SubElement(root, 'extract', name=ename, type='float')
        ET.SubElement(eel, 'input', name='in', type='color3', nodename=lname)
        ET.SubElement(eel, 'input', name='index', type='integer', value='0')
        return ename
    return src_name


def append_procedural_graph_xml(root, surface_el, subgraph, get_uv_doc, get_pos_doc, base_values):
    """Append proceduralGraph nodes to a MaterialX 1.38 XML document."""
    if not subgraph:
        return
    nodes_by_id = {n['id']: n for n in subgraph['nodes']}
    incoming = {}
    for e in subgraph['edges']:
        incoming.setdefault(e['target'], {})[e.get('targetHandle') or 'in'] = (
            e['source'],
            e.get('sourceHandle') or 'out',
        )
    order = _topo_order(subgraph['nodes'], subgraph['edges'])
    out_types = _resolve_node_types(order, nodes_by_id, incoming)
    built = {}

    for nid in order:
        node = nodes_by_id[nid]
        d = node.get('data') or {}
        kind = d.get('kind')
        if kind == 'output':
            continue
        spec = PROCEDURAL_NODE_SPECS[kind]
        out_type = out_types[nid]
        el_name = f'Proc_{_safe_ident(nid)}'
        params = d.get('params') or {}
        node_in = incoming.get(nid, {})

        if kind == 'place3d':
            ident = _safe_ident(nid)
            if 'position' in node_in:
                src_nid, _ = node_in['position']
                src_name, src_t = built[src_nid]
                pos_name = _convert_xml(root, f'{ident}_pos', src_name, src_t, 'vector3')
            else:
                pos_name = get_pos_doc()

            mul_name = f'{el_name}_mul'
            mul_el = ET.SubElement(root, 'multiply', name=mul_name, type='vector3')
            ET.SubElement(mul_el, 'input', name='in1', type='vector3', nodename=pos_name)
            if 'scale' in node_in:
                s_nid, _ = node_in['scale']
                s_name, s_t = built[s_nid]
                s_conv = _convert_xml(root, f'{ident}_scale', s_name, s_t, 'vector3')
                ET.SubElement(mul_el, 'input', name='in2', type='vector3', nodename=s_conv)
            else:
                ET.SubElement(mul_el, 'input', name='in2', type='vector3', value=_xml_value('vector3', params.get('scale', [1.0, 1.0, 1.0])))

            rot_name = f'{el_name}_rot'
            rot_el = ET.SubElement(root, 'rotate3d', name=rot_name, type='vector3')
            ET.SubElement(rot_el, 'input', name='in', type='vector3', nodename=mul_name)
            if 'rotate' in node_in:
                r_nid, _ = node_in['rotate']
                r_name, r_t = built[r_nid]
                r_conv = _convert_xml(root, f'{ident}_rot', r_name, r_t, 'float')
                ET.SubElement(rot_el, 'input', name='amount', type='float', nodename=r_conv)
            else:
                ET.SubElement(rot_el, 'input', name='amount', type='float', value=_xml_value('float', params.get('rotate', 0.0)))
            ET.SubElement(rot_el, 'input', name='axis', type='vector3', value='0, 1, 0')

            add_el = ET.SubElement(root, 'add', name=el_name, type='vector3')
            ET.SubElement(add_el, 'input', name='in1', type='vector3', nodename=rot_name)
            if 'offset' in node_in:
                o_nid, _ = node_in['offset']
                o_name, o_t = built[o_nid]
                o_conv = _convert_xml(root, f'{ident}_off', o_name, o_t, 'vector3')
                ET.SubElement(add_el, 'input', name='in2', type='vector3', nodename=o_conv)
            else:
                ET.SubElement(add_el, 'input', name='in2', type='vector3', value=_xml_value('vector3', params.get('offset', [0.0, 0.0, 0.0])))
            built[nid] = (el_name, 'vector3')
            continue

        if kind == 'colorramp':
            ident = _safe_ident(nid)
            stops = _normalize_ramp_stops(params)
            interp = params.get('interpolation') or 'linear'
            if 'in' in node_in:
                src_nid, _ = node_in['in']
                src_name, src_t = built[src_nid]
                in_name = _convert_xml(root, f'{ident}_in', src_name, src_t, 'float')
                in_const = None
            else:
                in_name = None
                in_const = float(params.get('in', 0.5))

            if len(stops) == 1:
                c_el = ET.SubElement(root, 'constant', name=el_name, type='color3')
                ET.SubElement(c_el, 'input', name='value', type='color3', value=_xml_value('color3', stops[0][1]))
                built[nid] = (el_name, 'color3')
                continue

            cur_name, cur_color = None, stops[0][1]
            for seg_idx in range(len(stops) - 1):
                p0, _ = stops[seg_idx]
                p1, c1 = stops[seg_idx + 1]
                is_last = seg_idx == len(stops) - 2
                if interp == 'constant':
                    inlow, inhigh = max(0.0, p1 - 1e-5), p1
                else:
                    inlow, inhigh = p0, p0 + max(p1 - p0, 1e-5)

                r_name = f'{el_name}_r{seg_idx}'
                r_el = ET.SubElement(root, 'remap', name=r_name, type='float')
                if in_name is not None:
                    ET.SubElement(r_el, 'input', name='in', type='float', nodename=in_name)
                else:
                    ET.SubElement(r_el, 'input', name='in', type='float', value=str(in_const))
                ET.SubElement(r_el, 'input', name='inlow', type='float', value=str(inlow))
                ET.SubElement(r_el, 'input', name='inhigh', type='float', value=str(inhigh))
                ET.SubElement(r_el, 'input', name='outlow', type='float', value='0.0')
                ET.SubElement(r_el, 'input', name='outhigh', type='float', value='1.0')

                c_name = f'{el_name}_c{seg_idx}'
                c_el = ET.SubElement(root, 'clamp', name=c_name, type='float')
                ET.SubElement(c_el, 'input', name='in', type='float', nodename=r_name)
                ET.SubElement(c_el, 'input', name='low', type='float', value='0.0')
                ET.SubElement(c_el, 'input', name='high', type='float', value='1.0')

                m_name = el_name if is_last else f'{el_name}_m{seg_idx}'
                m_el = ET.SubElement(root, 'mix', name=m_name, type='color3')
                if cur_name is not None:
                    ET.SubElement(m_el, 'input', name='bg', type='color3', nodename=cur_name)
                else:
                    ET.SubElement(m_el, 'input', name='bg', type='color3', value=_xml_value('color3', cur_color))
                ET.SubElement(m_el, 'input', name='fg', type='color3', value=_xml_value('color3', c1))
                ET.SubElement(m_el, 'input', name='mix', type='float', nodename=c_name)
                cur_name = m_name

            built[nid] = (cur_name, 'color3')
            continue

        el = ET.SubElement(root, spec['mtlx'], name=el_name, type=out_type)

        auto_coord = spec.get('auto_coord')
        coord_port = 'texcoord' if auto_coord == 'uv' else 'position'
        if auto_coord and coord_port not in node_in:
            if auto_coord == 'uv':
                get_uv_doc()
                ET.SubElement(el, 'input', name='texcoord', type='vector2', nodename='UV_st')
            else:
                pos_name = get_pos_doc()
                ET.SubElement(el, 'input', name='position', type='vector3', nodename=pos_name)

        for port, (ptype, default_val) in spec['inputs'].items():
            resolved_ptype = out_type if ptype == 'dynamic' else ptype
            if port in node_in:
                src_nid, _ = node_in[port]
                src_name, src_t = built[src_nid]
                conv_name = _convert_xml(root, f'{_safe_ident(nid)}_{port}', src_name, src_t, resolved_ptype)
                ET.SubElement(el, 'input', name=port, type=resolved_ptype, nodename=conv_name)
            elif default_val is not None:
                val = params.get(port, default_val)
                if val is not None:
                    ET.SubElement(el, 'input', name=port, type=resolved_ptype, value=_xml_value(resolved_ptype, val))
        built[nid] = (el_name, out_type)

    for idx, out_node in enumerate(subgraph['outputs']):
        oid = out_node['id']
        d = out_node.get('data') or {}
        param = d.get('parameter')
        if param not in STANDARD_SURFACE_PORTS:
            continue
        target_type, _ = STANDARD_SURFACE_PORTS[param]
        mapping = d.get('mappingType') or 'replace'
        strength = float(d.get('strength') if d.get('strength') is not None else 1.0)
        in_conn = incoming.get(oid, {})
        if not in_conn:
            continue
        src_nid, _ = next(iter(in_conn.values()))
        src_name, src_t = built[src_nid]

        def _replace_surface_port(port_name, port_type, nodename):
            existing = surface_el.find(f"input[@name='{port_name}']")
            prev_nodename = existing.attrib.get('nodename') if existing is not None else None
            if existing is not None:
                surface_el.remove(existing)
            ET.SubElement(surface_el, 'input', name=port_name, type=port_type, nodename=nodename)
            return prev_nodename

        if mapping == 'bump_normal' or param == 'normal':
            h_name = _convert_xml(root, f'OutBump_{idx}', src_name, src_t, 'float')
            b_name = f'ProcOutBump_{_safe_ident(oid)}'
            bel = ET.SubElement(root, 'bump', name=b_name, type='vector3')
            ET.SubElement(bel, 'input', name='height', type='float', nodename=h_name)
            ET.SubElement(bel, 'input', name='scale', type='float', value=str(strength))
            _replace_surface_port('normal', 'vector3', b_name)
            continue

        proc_name = _convert_xml(root, f'Out_{idx}', src_name, src_t, target_type)
        if mapping == 'replace':
            _replace_surface_port(param, target_type, proc_name)
            continue

        existing = surface_el.find(f"input[@name='{param}']")
        prev_nodename = existing.attrib.get('nodename') if existing is not None else None
        base_val = base_values.get(param)

        def _add_base_or_conn(parent_el, inp_name):
            if prev_nodename:
                ET.SubElement(parent_el, 'input', name=inp_name, type=target_type, nodename=prev_nodename)
            elif base_val is not None:
                ET.SubElement(parent_el, 'input', name=inp_name, type=target_type, value=_xml_value(target_type, base_val))

        if mapping == 'multiply':
            cname = f'ProcMul_{_safe_ident(oid)}'
            cel = ET.SubElement(root, 'multiply', name=cname, type=target_type)
            _add_base_or_conn(cel, 'in1')
            ET.SubElement(cel, 'input', name='in2', type=target_type, nodename=proc_name)
            _replace_surface_port(param, target_type, cname)
        elif mapping == 'add':
            cname = f'ProcAdd_{_safe_ident(oid)}'
            cel = ET.SubElement(root, 'add', name=cname, type=target_type)
            _add_base_or_conn(cel, 'in1')
            ET.SubElement(cel, 'input', name='in2', type=target_type, nodename=proc_name)
            _replace_surface_port(param, target_type, cname)
        elif mapping == 'mix':
            cname = f'ProcMix_{_safe_ident(oid)}'
            cel = ET.SubElement(root, 'mix', name=cname, type=target_type)
            _add_base_or_conn(cel, 'bg')
            ET.SubElement(cel, 'input', name='fg', type=target_type, nodename=proc_name)
            ET.SubElement(cel, 'input', name='mix', type='float', value=str(max(0.0, min(1.0, strength))))
            _replace_surface_port(param, target_type, cname)


BLENDER_PROCEDURAL_EXTRACTOR = r"""
def _extract_blender_procedural_graph(socket_mappings, group_stack=()):
    import math
    nodes = []
    edges = []
    outputs = []
    node_cache = {}
    counter = [0]

    def _next_id(prefix):
        counter[0] += 1
        return f"bp_{prefix}_{counter[0]}"

    def _inp(node, *names):
        inps = getattr(node, 'inputs', None)
        if not inps:
            return None
        for nm in names:
            s = inps.get(nm) if hasattr(inps, 'get') else None
            if s is not None:
                return s
        return None

    def _sock_float(sock, default=0.0):
        if sock is None:
            return float(default)
        v = getattr(sock, 'default_value', default)
        try:
            if hasattr(v, '__len__'):
                return float(v[0])
            return float(v)
        except Exception:
            return float(default)

    def _sock_vec(sock, n=3, default=None):
        if default is None:
            default = [0.0] * n
        if sock is None:
            return list(default)
        v = getattr(sock, 'default_value', default)
        try:
            return [float(v[i]) for i in range(n)]
        except Exception:
            try:
                f = float(v)
                return [f] * n
            except Exception:
                return list(default)

    def _emit_node(kind, params):
        nid = _next_id(kind)
        nodes.append({
            'id': nid,
            'type': 'procedural',
            'position': {'x': 0, 'y': 0},
            'data': {'kind': kind, 'params': params},
        })
        return nid

    def _emit_edge(src_id, dst_id, dst_handle, src_handle='out'):
        edges.append({
            'id': _next_id('e'),
            'source': src_id,
            'sourceHandle': src_handle,
            'target': dst_id,
            'targetHandle': dst_handle,
        })

    def _resolve_octane_transform(sock, gs):
        if not sock or not getattr(sock, 'is_linked', False):
            return None
        link = sock.links[0]
        tnode = link.from_node
        tbid = getattr(tnode, 'bl_idname', '')
        if tbid in ('Octane3DTransformation', 'Octane2DTransformation'):
            sc = _sock_vec(_inp(tnode, 'Scale'), 3, [1.0, 1.0, 1.0])
            tr = _sock_vec(_inp(tnode, 'Translation'), 3, [0.0, 0.0, 0.0])
            rot = _sock_vec(_inp(tnode, 'Rotation'), 3, [0.0, 0.0, 0.0])
            return {'is2d': tbid == 'Octane2DTransformation', 'scale': sc, 'offset': tr, 'rotate': rot[2]}
        return None

    def _walk_sock(sock, gs):
        if sock is None or not getattr(sock, 'is_linked', False):
            return None
        link = sock.links[0]
        src_node = link.from_node
        if src_node is None:
            return None
        from_sock = getattr(link, 'from_socket', None)
        from_name = getattr(from_sock, 'name', '') or ''
        bid = getattr(src_node, 'bl_idname', '')

        if bid == 'NodeGroupInput' and gs:
            outer = gs[-1]
            outer_sock = _inp(outer, from_name)
            return _walk_sock(outer_sock, gs[:-1])

        if bid == 'ShaderNodeGroup' and getattr(src_node, 'node_tree', None):
            out_n = next((n for n in src_node.node_tree.nodes if getattr(n, 'bl_idname', '') == 'NodeGroupOutput'), None)
            if out_n:
                out_sock = _inp(out_n, from_name)
                if out_sock is None:
                    sock_id = getattr(from_sock, 'identifier', '')
                    out_sock = next((s for s in out_n.inputs if getattr(s, 'identifier', '') == sock_id), None)
                if out_sock is not None and out_sock.is_linked:
                    return _walk_sock(out_sock, gs + (src_node,))
            return None

        cache_key = (id(src_node), from_name, tuple(id(g) for g in gs))
        if cache_key in node_cache:
            return node_cache[cache_key]

        result_id = None

        # --- Coordinates ---
        if bid == 'ShaderNodeTexCoord':
            if from_name == 'UV':
                result_id = _emit_node('uv', {'geomprop': 'st'})
            elif from_name in ('Object', 'Generated'):
                result_id = _emit_node('position', {'space': 'object'})
        elif bid == 'ShaderNodeMapping':
            vec_sock = _inp(src_node, 'Vector')
            loc = _sock_vec(_inp(src_node, 'Location'), 3, [0.0, 0.0, 0.0])
            rot = _sock_vec(_inp(src_node, 'Rotation'), 3, [0.0, 0.0, 0.0])
            sc = _sock_vec(_inp(src_node, 'Scale'), 3, [1.0, 1.0, 1.0])
            coord_id = _walk_sock(vec_sock, gs) if (vec_sock and vec_sock.is_linked) else None
            # place2d operates on vector2; only attach coord_id when it is a 2D UV coordinate.
            is_3d_coord = False
            if vec_sock and vec_sock.is_linked:
                fn = vec_sock.links[0].from_node
                fn_sock = getattr(vec_sock.links[0], 'from_socket', None)
                if getattr(fn, 'bl_idname', '') == 'ShaderNodeTexCoord' and getattr(fn_sock, 'name', '') in ('Object', 'Generated'):
                    is_3d_coord = True
            if not is_3d_coord:
                result_id = _emit_node('place2d', {
                    'scale': [sc[0], sc[1]],
                    'rotate': math.degrees(rot[2]),
                    'offset': [loc[0], loc[1]],
                    'pivot': [0.0, 0.0],
                })
                if coord_id:
                    _emit_edge(coord_id, result_id, 'texcoord')
            else:
                result_id = coord_id
        elif bid == 'Octane2DTransformation':
            sc = _sock_vec(_inp(src_node, 'Scale'), 2, [1.0, 1.0])
            tr = _sock_vec(_inp(src_node, 'Translation'), 2, [0.0, 0.0])
            rot = _sock_vec(_inp(src_node, 'Rotation'), 3, [0.0, 0.0, 0.0])
            result_id = _emit_node('place2d', {
                'scale': [sc[0], sc[1]],
                'rotate': rot[2],
                'offset': [tr[0], tr[1]],
                'pivot': [0.0, 0.0],
            })

        # --- Cycles Generators ---
        elif bid == 'ShaderNodeTexNoise':
            dims = getattr(src_node, 'noise_dimensions', '3D')
            scale = _sock_float(_inp(src_node, 'Scale'), 5.0)
            detail = max(1, int(round(_sock_float(_inp(src_node, 'Detail'), 2.0))))
            rough = _sock_float(_inp(src_node, 'Roughness'), 0.5)
            lac = _sock_float(_inp(src_node, 'Lacunarity'), 2.0)
            vec_sock = _inp(src_node, 'Vector')
            coord_id = _walk_sock(vec_sock, gs) if (vec_sock and vec_sock.is_linked) else None
            if dims == '2D':
                result_id = _emit_node('unified2d', {
                    'type': 3 if detail > 1 else 0,
                    'freq': [scale, scale],
                    'octaves': min(detail, 8),
                    'diminish': rough,
                    'lacunarity': lac,
                    'outmin': 0.0,
                    'outmax': 1.0,
                    'clampoutput': True,
                })
                if coord_id:
                    _emit_edge(coord_id, result_id, 'texcoord')
            else:
                result_id = _emit_node('unified3d', {
                    'type': 3 if detail > 1 else 0,
                    'freq': [scale, scale, scale],
                    'octaves': min(detail, 8),
                    'diminish': rough,
                    'lacunarity': lac,
                    'outmin': 0.0,
                    'outmax': 1.0,
                    'clampoutput': True,
                })
                if coord_id:
                    _emit_edge(coord_id, result_id, 'position')
        elif bid == 'ShaderNodeTexVoronoi':
            dims = getattr(src_node, 'voronoi_dimensions', '3D')
            scale = _sock_float(_inp(src_node, 'Scale'), 5.0)
            rand = _sock_float(_inp(src_node, 'Randomness'), 1.0)
            vec_sock = _inp(src_node, 'Vector')
            coord_id = _walk_sock(vec_sock, gs) if (vec_sock and vec_sock.is_linked) else None
            ntype = 1 if from_name == 'Color' else 2
            if dims == '2D':
                result_id = _emit_node('unified2d', {
                    'type': ntype,
                    'freq': [scale, scale],
                    'jitter': rand,
                    'outmin': 0.0,
                    'outmax': 1.0,
                    'clampoutput': True,
                })
                if coord_id:
                    _emit_edge(coord_id, result_id, 'texcoord')
            else:
                result_id = _emit_node('unified3d', {
                    'type': ntype,
                    'freq': [scale, scale, scale],
                    'jitter': rand,
                    'outmin': 0.0,
                    'outmax': 1.0,
                    'clampoutput': True,
                })
                if coord_id:
                    _emit_edge(coord_id, result_id, 'position')
        elif bid == 'ShaderNodeTexChecker':
            c1 = _sock_vec(_inp(src_node, 'Color1'), 3, [0.8, 0.8, 0.8])
            c2 = _sock_vec(_inp(src_node, 'Color2'), 3, [0.2, 0.2, 0.2])
            sc = _sock_float(_inp(src_node, 'Scale'), 5.0)
            vec_sock = _inp(src_node, 'Vector')
            coord_id = _walk_sock(vec_sock, gs) if (vec_sock and vec_sock.is_linked) else None
            result_id = _emit_node('checkerboard', {
                'color1': c1,
                'color2': c2,
                'uvtiling': [sc, sc],
                'uvoffset': [0.0, 0.0],
            })
            if coord_id:
                _emit_edge(coord_id, result_id, 'texcoord')

        # --- Octane Generators ---
        elif bid in ('OctaneNoiseTexture', 'OctaneTurbulenceTexture'):
            octaves = max(1, min(8, int(round(_sock_float(_inp(src_node, 'Octaves'), 5 if bid == 'OctaneTurbulenceTexture' else 1)))))
            omega = _sock_float(_inp(src_node, 'Omega', 'Gamma'), 0.5)
            tr = _resolve_octane_transform(_inp(src_node, 'Transform'), gs)
            sc = tr['scale'] if tr else [1.0, 1.0, 1.0]
            freq = [1.0 / s if abs(s) > 1e-5 else 1.0 for s in sc]
            result_id = _emit_node('unified3d', {
                'type': 3 if (bid == 'OctaneTurbulenceTexture' or octaves > 1) else 0,
                'freq': freq,
                'octaves': octaves,
                'diminish': omega,
                'outmin': 0.0,
                'outmax': 1.0,
                'clampoutput': True,
            })
        elif bid == 'OctaneChecksTexture':
            tr = _resolve_octane_transform(_inp(src_node, 'Transform'), gs)
            sc = tr['scale'] if tr else [1.0, 1.0, 1.0]
            tiling = [1.0 / s if abs(s) > 1e-5 else 1.0 for s in sc[:2]]
            result_id = _emit_node('checkerboard', {
                'color1': [0.05, 0.05, 0.05],
                'color2': [0.95, 0.95, 0.95],
                'uvtiling': tiling,
                'uvoffset': [0.0, 0.0],
            })

        # --- ColorRamps ---
        elif bid in ('ShaderNodeValToRGB', 'OctaneGradient'):
            cr = getattr(src_node, 'color_ramp', None)
            stops = []
            interp = 'linear'
            if cr and getattr(cr, 'elements', None):
                interp = 'constant' if getattr(cr, 'interpolation', 'LINEAR') == 'CONSTANT' else 'linear'
                for el in cr.elements:
                    col = getattr(el, 'color', [0.0, 0.0, 0.0])
                    stops.append({'pos': float(getattr(el, 'position', 0.0)), 'color': [float(col[0]), float(col[1]), float(col[2])]})
            elif bid == 'OctaneGradient':
                c0 = _sock_vec(_inp(src_node, 'Start value'), 3, [0.0, 0.0, 0.0])
                c1 = _sock_vec(_inp(src_node, 'End value'), 3, [1.0, 1.0, 1.0])
                stops = [{'pos': 0.0, 'color': c0}, {'pos': 1.0, 'color': c1}]
            if len(stops) >= 2:
                fac_sock = _inp(src_node, 'Fac', 'Input', 'Texture')
                fac_id = _walk_sock(fac_sock, gs) if (fac_sock and fac_sock.is_linked) else None
                if fac_id is not None or not (fac_sock and fac_sock.is_linked):
                    result_id = _emit_node('colorramp', {'interpolation': interp, 'stops': stops, 'in': _sock_float(fac_sock, 0.5)})
                    if fac_id:
                        _emit_edge(fac_id, result_id, 'in')

        # --- Math & Color ---
        elif bid in ('ShaderNodeMix', 'ShaderNodeMixRGB', 'OctaneMixTexture', 'OctaneMultiplyTexture', 'OctaneAddTexture'):
            if bid in ('ShaderNodeMix', 'ShaderNodeMixRGB'):
                btype = getattr(src_node, 'blend_type', 'MIX')
                if btype == 'MIX':
                    kind, p1_n, p2_n = 'mix', ('A', 'Color1'), ('B', 'Color2')
                elif btype == 'MULTIPLY':
                    kind, p1_n, p2_n = 'multiply', ('A', 'Color1'), ('B', 'Color2')
                elif btype == 'ADD':
                    kind, p1_n, p2_n = 'add', ('A', 'Color1'), ('B', 'Color2')
                else:
                    return None
                s1 = _inp(src_node, *p1_n)
                s2 = _inp(src_node, *p2_n)
                smix = _inp(src_node, 'Factor', 'Fac')
            elif bid == 'OctaneMixTexture':
                kind = 'mix'
                s1 = _inp(src_node, 'Texture 1')
                s2 = _inp(src_node, 'Texture 2')
                smix = _inp(src_node, 'Amount')
            elif bid == 'OctaneMultiplyTexture':
                kind = 'multiply'
                s1 = _inp(src_node, 'Texture 1')
                s2 = _inp(src_node, 'Texture 2')
                smix = None
            else:
                kind = 'add'
                s1 = _inp(src_node, 'Texture 1')
                s2 = _inp(src_node, 'Texture 2')
                smix = None

            id1 = _walk_sock(s1, gs) if (s1 and s1.is_linked) else None
            id2 = _walk_sock(s2, gs) if (s2 and s2.is_linked) else None
            if (s1 and s1.is_linked and id1 is None) or (s2 and s2.is_linked and id2 is None):
                return None
            if kind == 'mix':
                id_m = _walk_sock(smix, gs) if (smix and smix.is_linked) else None
                if smix and smix.is_linked and id_m is None:
                    return None
                result_id = _emit_node('mix', {
                    'bg': _sock_vec(s1, 3, [0.0, 0.0, 0.0]),
                    'fg': _sock_vec(s2, 3, [1.0, 1.0, 1.0]),
                    'mix': _sock_float(smix, 0.5),
                    'valueType': 'color3',
                })
                if id1:
                    _emit_edge(id1, result_id, 'bg')
                if id2:
                    _emit_edge(id2, result_id, 'fg')
                if id_m:
                    _emit_edge(id_m, result_id, 'mix')
            else:
                result_id = _emit_node(kind, {
                    'in1': _sock_vec(s1, 3, [1.0, 1.0, 1.0]) if kind == 'multiply' else _sock_vec(s1, 3, [0.0, 0.0, 0.0]),
                    'in2': _sock_vec(s2, 3, [1.0, 1.0, 1.0]) if kind == 'multiply' else _sock_vec(s2, 3, [0.0, 0.0, 0.0]),
                    'valueType': 'color3',
                })
                if id1:
                    _emit_edge(id1, result_id, 'in1')
                if id2:
                    _emit_edge(id2, result_id, 'in2')

        elif bid == 'ShaderNodeMath':
            op = getattr(src_node, 'operation', '')
            if op in ('ADD', 'MULTIPLY'):
                kind = 'add' if op == 'ADD' else 'multiply'
                s1 = src_node.inputs[0] if len(src_node.inputs) > 0 else None
                s2 = src_node.inputs[1] if len(src_node.inputs) > 1 else None
                id1 = _walk_sock(s1, gs) if (s1 and s1.is_linked) else None
                id2 = _walk_sock(s2, gs) if (s2 and s2.is_linked) else None
                if (s1 and s1.is_linked and id1 is None) or (s2 and s2.is_linked and id2 is None):
                    return None
                result_id = _emit_node(kind, {
                    'in1': _sock_float(s1, 0.0 if kind == 'add' else 1.0),
                    'in2': _sock_float(s2, 0.0 if kind == 'add' else 1.0),
                    'valueType': 'float',
                })
                if id1:
                    _emit_edge(id1, result_id, 'in1')
                if id2:
                    _emit_edge(id2, result_id, 'in2')

        elif bid == 'ShaderNodeMapRange':
            sin = _inp(src_node, 'Value')
            id_in = _walk_sock(sin, gs) if (sin and sin.is_linked) else None
            if sin and sin.is_linked and id_in is None:
                return None
            result_id = _emit_node('remap', {
                'in': _sock_float(sin, 0.0),
                'inlow': _sock_float(_inp(src_node, 'From Min'), 0.0),
                'inhigh': _sock_float(_inp(src_node, 'From Max'), 1.0),
                'outlow': _sock_float(_inp(src_node, 'To Min'), 0.0),
                'outhigh': _sock_float(_inp(src_node, 'To Max'), 1.0),
                'valueType': 'float',
            })
            if id_in:
                _emit_edge(id_in, result_id, 'in')

        elif bid in ('ShaderNodeClamp', 'OctaneClampTexture'):
            sin = _inp(src_node, 'Value', 'Input')
            id_in = _walk_sock(sin, gs) if (sin and sin.is_linked) else None
            if sin and sin.is_linked and id_in is None:
                return None
            result_id = _emit_node('clamp', {
                'in': _sock_float(sin, 0.0),
                'low': _sock_float(_inp(src_node, 'Min'), 0.0),
                'high': _sock_float(_inp(src_node, 'Max'), 1.0),
                'valueType': 'float',
            })
            if id_in:
                _emit_edge(id_in, result_id, 'in')

        elif bid == 'ShaderNodeBrightContrast':
            scol = _inp(src_node, 'Color')
            id_in = _walk_sock(scol, gs) if (scol and scol.is_linked) else None
            if scol and scol.is_linked and id_in is None:
                return None
            result_id = _emit_node('contrast', {
                'in': _sock_vec(scol, 3, [0.5, 0.5, 0.5]),
                'amount': _sock_float(_inp(src_node, 'Contrast'), 0.0) + 1.0,
                'pivot': 0.5,
                'valueType': 'color3',
            })
            if id_in:
                _emit_edge(id_in, result_id, 'in')

        elif bid in ('ShaderNodeHueSaturation', 'OctaneColorCorrection'):
            scol = _inp(src_node, 'Color', 'Texture')
            id_in = _walk_sock(scol, gs) if (scol and scol.is_linked) else None
            if scol and scol.is_linked and id_in is None:
                return None
            if bid == 'ShaderNodeHueSaturation':
                h = _sock_float(_inp(src_node, 'Hue'), 0.5) - 0.5
                s = _sock_float(_inp(src_node, 'Saturation'), 1.0)
                v = _sock_float(_inp(src_node, 'Value'), 1.0)
            else:
                h = _sock_float(_inp(src_node, 'Hue'), 0.0) / 360.0
                s = _sock_float(_inp(src_node, 'Saturation'), 1.0)
                v = _sock_float(_inp(src_node, 'Brightness'), 1.0)
            result_id = _emit_node('hsvadjust', {'amount': [h, s, v]})
            if id_in:
                _emit_edge(id_in, result_id, 'in')

        elif bid == 'ShaderNodeBump':
            sh = _inp(src_node, 'Height')
            id_h = _walk_sock(sh, gs) if (sh and sh.is_linked) else None
            if not id_h:
                return None
            dist = _sock_float(_inp(src_node, 'Distance'), 0.1)
            strength = _sock_float(_inp(src_node, 'Strength'), 1.0)
            result_id = _emit_node('bump', {'scale': dist * strength})
            _emit_edge(id_h, result_id, 'height')

        if result_id is not None:
            node_cache[cache_key] = result_id
        return result_id

    for sock, param_name, mapping_type in socket_mappings:
        if sock is None or not getattr(sock, 'is_linked', False):
            continue
        root_id = _walk_sock(sock, group_stack)
        if root_id is not None:
            oid = _next_id('out')
            outputs.append({
                'id': oid,
                'type': 'procedural',
                'position': {'x': 0, 'y': 0},
                'data': {
                    'kind': 'output',
                    'parameter': param_name,
                    'mappingType': mapping_type,
                    'strength': 1.0,
                    'enabled': True,
                },
            })
            _emit_edge(root_id, oid, 'in')

    if not outputs:
        return None
    return {'nodes': nodes + outputs, 'edges': edges, 'outputs': outputs}
"""
