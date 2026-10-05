"""Dev script: build an onigiri and render preview views.

Usage: blender -b --factory-startup --python scripts/render_onigiri.py -- [out_dir] [views]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import bpy  # noqa: E402

from blender_toolkit.generators.onigiri import create_onigiri  # noqa: E402
from blender_toolkit.preview import render_views  # noqa: E402

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
out_dir = args[0] if args else '/tmp/onigiri_preview'
views = args[1].split(',') if len(args) > 1 else ['front', 'three_quarter', 'top_closeup']

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
from blender_toolkit.label import StickerDesign  # noqa: E402
font = os.environ.get('FONT')
sticker = StickerDesign(title='ツナマヨネーズ', subtitle='手巻おにぎり', price='¥150', font_path=font) if font else None
rice = create_onigiri('Onigiri', packaged=os.environ.get('PACKAGED', '1') == '1', sticker=sticker)
objs = [rice] + list(rice.children)
print('ONIGIRI', 'faces', len(rice.data.polygons), 'dims_mm', [round(d * 1000, 1) for d in rice.dimensions])
for p in render_views(objs, out_dir, views=views, samples=int(os.environ.get('SAMPLES', '64'))):
    print('PREVIEW', p)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, 'onigiri.blend'))
