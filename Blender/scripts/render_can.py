"""Dev script: build a can and render preview views.

Usage: blender -b --factory-startup --python scripts/render_can.py -- [out_dir] [size]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import bpy  # noqa: E402

from blender_toolkit.generators.can import create_can  # noqa: E402
from blender_toolkit.preview import render_views  # noqa: E402

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
out_dir = args[0] if args else '/tmp/can_preview'
size = args[1] if len(args) > 1 else '350ml'
views = args[2].split(',') if len(args) > 2 else ['front', 'three_quarter', 'top_closeup', 'bottom']

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
can = create_can('Can', size)
print('CAN', can.name, 'verts', len(can.data.vertices), 'faces', len(can.data.polygons),
      'dims_mm', [round(d * 1000, 1) for d in can.dimensions])
for p in render_views([can], out_dir, views=views, samples=int(os.environ.get('SAMPLES', '64'))):
    print('PREVIEW', p)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, f'can_{size}.blend'))
