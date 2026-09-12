"""Execute this source through Blender MCP in an open Blender instance.

Creates a separate scene without modifying existing scenes or objects.
The mesh, rock colors, and small stones are original procedural assets.
"""

import math
import random
from pathlib import Path

import bpy
from mathutils import Vector, noise

# Set PROJECT_ROOT in the Blender MCP execution before evaluating this file.
ROOT = Path(PROJECT_ROOT)
OUT = ROOT / 'src/dmx_volcano/web/static/models'
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = ROOT / 'assets/models'
SOURCE.mkdir(parents=True, exist_ok=True)

rng = random.Random(7319)
previous_scene = bpy.context.window.scene
scene = bpy.data.scenes.new('Volcano Pro Asset')
scene.unit_settings.system = 'METRIC'
bpy.context.window.scene = scene

material = bpy.data.materials.new('Basalt - mineral variation')
material.use_nodes = True
shader = material.node_tree.nodes.get('Principled BSDF')
shader.inputs['Roughness'].default_value = 0.94
shader.inputs['Metallic'].default_value = 0.0
color_node = material.node_tree.nodes.new('ShaderNodeVertexColor')
color_node.layer_name = 'RockColor'
material.node_tree.links.new(color_node.outputs['Color'], shader.inputs['Base Color'])

N = 160
RINGS = 68
vertices, faces = [], []

def radial_vertex(t, angle):
    # Broad eroded skirts taper into a jagged rim, with channels down the flanks.
    rib = (0.5 + 0.5 * math.sin(13 * angle + 0.7 * math.sin(3 * angle) + t)) ** 3
    radius = 0.62 + 1.25 * (1 - t) ** 1.28
    radius += 0.19 * rib * math.sin(math.pi * t) + 0.045 * math.sin(5 * angle + 2 * t)
    z = 0.035 + 2.91 * t
    z += 0.045 * math.sin(3 * angle + 1.6) + 0.026 * math.sin(8 * angle + 0.3)
    p = Vector((radius * math.cos(angle), radius * math.sin(angle), z))
    detail = noise.fractal(p * 6.5, 1.0, 2.0, 3)
    p.x += math.cos(angle) * detail * 0.046 * math.sin(math.pi * t)
    p.y += math.sin(angle) * detail * 0.046 * math.sin(math.pi * t)
    p.z += detail * 0.025 * math.sin(math.pi * t)
    return tuple(p)

for j in range(RINGS):
    for i in range(N):
        vertices.append(radial_vertex(j / (RINGS - 1), i * math.tau / N))

# Continue the same connected mesh down into a deep, irregular crater bowl.
INNER = 20
for j in range(1, INNER + 1):
    t = j / INNER
    for i in range(N):
        angle = i * math.tau / N
        rim = Vector(radial_vertex(1, angle))
        radius = math.hypot(rim.x, rim.y) * (1 - 0.76 * t)
        z = rim.z - 0.93 * t ** 0.65
        radius += 0.018 * math.sin(23 * angle + j) * math.sin(math.pi * t)
        vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))

for j in range(RINGS + INNER - 1):
    for i in range(N):
        a, b = j * N + i, j * N + (i + 1) % N
        c, d = b + N, a + N
        if rng.random() < 0.5:
            faces.extend([(a, b, c), (a, c, d)])
        else:
            faces.extend([(a, b, d), (b, c, d)])
faces.append(tuple(range(len(vertices) - N, len(vertices))))
faces.append(tuple(reversed(range(N))))

mesh = bpy.data.meshes.new('Eroded basalt and crater mesh')
mesh.from_pydata(vertices, [], faces)
mesh.update()
volcano = bpy.data.objects.new('Volcano_Basalt', mesh)
scene.collection.objects.link(volcano)
mesh.materials.append(material)
colors = mesh.color_attributes.new(name='RockColor', type='FLOAT_COLOR', domain='POINT')
for i, vertex in enumerate(mesh.vertices):
    p = vertex.co
    grain = noise.fractal(p * 11, 1.0, 2.0, 3)
    mineral = noise.noise(p * 3.7)
    shade = max(0.07, min(0.4, 0.22 + grain * 0.10 + mineral * 0.06))
    if i >= RINGS * N:
        shade *= 0.60
    colors.data[i].color = (shade * 1.06, shade, shade * 0.94, 1.0)
for polygon in mesh.polygons:
    polygon.use_smooth = True

# Angular scree around the base gives the silhouette a natural transition.
stone_material = bpy.data.materials.new('Loose basalt')
stone_material.diffuse_color = (0.19, 0.18, 0.17, 1)
stone_material.use_nodes = True
stone_shader = stone_material.node_tree.nodes.get('Principled BSDF')
stone_shader.inputs['Base Color'].default_value = (0.19, 0.18, 0.17, 1)
stone_shader.inputs['Roughness'].default_value = 0.96
stones = []
for i in range(42):
    angle = rng.uniform(0, math.tau)
    radius = rng.uniform(1.77, 2.05)
    size = rng.uniform(0.045, 0.14)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1,
        location=(radius * math.cos(angle), radius * math.sin(angle), size * 0.25))
    stone = bpy.context.object
    stone.name = 'Basalt_scree'
    stone.scale = (size * rng.uniform(0.8, 1.8), size, size * rng.uniform(0.45, 0.9))
    stone.rotation_euler = (rng.random(), rng.random(), angle)
    stone.data.materials.append(stone_material)
    stones.append(stone)
bpy.ops.object.select_all(action='DESELECT')
for stone in stones:
    stone.select_set(True)
bpy.context.view_layer.objects.active = stones[0]
bpy.ops.object.join()
stones[0].name = 'Volcano_Scree'

volcano['height_m'] = 3.05
volcano['description'] = 'Illustrative 10-foot prop; crater and mineral-colored basalt'
scene['generator'] = 'tools/build_volcano_asset.py via Blender MCP'

bpy.ops.object.select_all(action='SELECT')
bpy.context.view_layer.objects.active = volcano
bpy.context.view_layer.update()
bpy.ops.export_scene.gltf(filepath=str(OUT / 'volcano.glb'), export_format='GLB',
    use_selection=True, use_active_scene=True, export_animations=False, export_extras=True,
    export_yup=True, export_vertex_color='MATERIAL')
bpy.data.libraries.write(str(SOURCE / 'volcano.blend'), {scene}, fake_user=True)
bpy.context.window.scene = previous_scene

result = {
    'scene': scene.name,
    'model': str(OUT / 'volcano.glb'),
    'source': str(SOURCE / 'volcano.blend'),
    'vertices': len(mesh.vertices),
    'triangles': len(faces) - 2,
    'bytes': (OUT / 'volcano.glb').stat().st_size,
}
