"""Run through Blender MCP with PROJECT_ROOT set to the checkout path.

Original visual approximations of the MOKA H-E01A and MK-C09 housings.
Dimensions and references are documented in docs/PRO_VIEW.md.
"""
import math
from pathlib import Path

import bpy

root = Path(PROJECT_ROOT)
previous_scene = bpy.context.window.scene
scene = bpy.data.scenes.new('Volcano effect machines')
scene.unit_settings.system = 'METRIC'
bpy.context.window.scene = scene

def material(name, rgb, metallic=0, roughness=0.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = (*rgb, 1)
    shader.inputs['Metallic'].default_value = metallic
    shader.inputs['Roughness'].default_value = roughness
    return mat

black = material('Powder coated steel', (0.065, 0.075, 0.085), 0.45, 0.4)
steel = material('Brushed aluminum', (0.45, 0.50, 0.54), 0.8, 0.3)
rubber = material('Rubber and nozzle interior', (0.008, 0.012, 0.016), 0, 0.85)
brass = material('Brass fittings', (0.48, 0.29, 0.07), 0.7, 0.35)
amber = material('Amber machine markings', (0.95, 0.38, 0.025), 0, 0.6)
display = material('Blue control display', (0.05, 0.38, 0.65), 0, 0.25)

def empty(name, kind=None, location=(0, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    scene.collection.objects.link(obj)
    obj.location = location
    if kind:
        obj['fixture_kind'] = kind
    return obj

def finish(obj, name, parent, mat):
    obj.name = name
    obj.parent = parent
    obj.data.materials.append(mat)
    return obj

def box(name, parent, location, size, mat=black, bevel=0.003):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = finish(bpy.context.object, name, parent, mat)
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        modifier = obj.modifiers.new('Soft metal edges', 'BEVEL')
        modifier.width, modifier.segments = bevel, 2
    return obj

def cylinder(name, parent, location, radius, depth, mat=steel, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=radius, depth=depth,
        location=location, rotation=rotation)
    obj = finish(bpy.context.object, name, parent, mat)
    for face in obj.data.polygons:
        face.use_smooth = len(face.vertices) == 4
    return obj

def ring(name, parent, location, radius, thickness, mat=steel):
    bpy.ops.mesh.primitive_torus_add(major_segments=32, minor_segments=8,
        location=location, major_radius=radius, minor_radius=thickness)
    return finish(bpy.context.object, name, parent, mat)

def hose(name, parent, points):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions, curve.bevel_depth, curve.bevel_resolution = '3D', 0.009, 3
    spline = curve.splines.new('BEZIER')
    spline.bezier_points.add(len(points) - 1)
    for point, xyz in zip(spline.bezier_points, points):
        point.co = xyz
        point.handle_left_type = point.handle_right_type = 'AUTO'
    obj = bpy.data.objects.new(name, curve)
    scene.collection.objects.link(obj)
    finish(obj, name, parent, rubber)

flame = empty('Flamethrower', 'flame')
flame['model_reference'] = 'MOKA H-E01A; illustrative exterior'
box('Flame cabinet', flame, (0, 0, 0.135), (0.185, 0.185, 0.25))
box('Flame mounting plate', flame, (0, 0, 0.007), (0.23, 0.22, 0.014), steel)
box('Burner deck', flame, (0, 0, 0.264), (0.185, 0.185, 0.014), steel)
cylinder('Burner cup', flame, (0, 0, 0.278), 0.037, 0.029, black)
cylinder('Burner opening', flame, (0, 0, 0.293), 0.026, 0.002, rubber)
ring('Burner rim', flame, (0, 0, 0.293), 0.032, 0.004)
for x in [-0.047, 0.047]:
    cylinder('Ignition electrode', flame, (x, 0, 0.278), 0.004, 0.033, steel)
for x in [-0.073, 0.073]:
    for y in [-0.073, 0.073]:
        cylinder('Top fastener', flame, (x, y, 0.274), 0.006, 0.004)
box('Control screen', flame, (0, -0.094, 0.145), (0.055, 0.004, 0.024), display, 0.001)
box('Flame identification stripe', flame, (0, -0.094, 0.216), (0.14, 0.003, 0.019), amber, 0)
for i in range(5):
    box('Vent slot', flame, (0.094, 0, 0.08 + i * 0.018), (0.003, 0.12, 0.006), rubber, 0)
for x in [-0.057, 0, 0.057]:
    cylinder('Control button', flame, (x, -0.096, 0.10), 0.006, 0.004, steel, (math.pi / 2, 0, 0))
cylinder('LPG fitting', flame, (0, 0.103, 0.06), 0.015, 0.035, brass, (math.pi / 2, 0, 0))
hose('LPG hose', flame, [(0, 0.12, 0.06), (0.07, 0.23, 0.04), (0.13, 0.29, -0.16)])
outlet = empty('FlameOutlet', location=(0, 0, 0.295))
outlet.parent, outlet['outlet'] = flame, True

co2 = empty('CO2Cannon', 'co2')
co2['model_reference'] = 'MOKA MK-C09; illustrative exterior'
box('CO2 mounting plate', co2, (0, 0, 0.007), (0.275, 0.125, 0.014), steel)
box('CO2 valve enclosure', co2, (0.040, 0, 0.077), (0.17, 0.12, 0.13))
cylinder('CO2 vertical barrel', co2, (-0.09, 0, 0.18), 0.027, 0.285, black)
ring('CO2 barrel collar', co2, (-0.09, 0, 0.075), 0.028, 0.007, steel)
ring('CO2 nozzle rim', co2, (-0.09, 0, 0.324), 0.027, 0.005, steel)
cylinder('CO2 nozzle opening', co2, (-0.09, 0, 0.325), 0.021, 0.002, rubber)
box('CO2 control display', co2, (0.04, -0.061, 0.10), (0.06, 0.004, 0.026), display, 0.001)
box('CO2 identification stripe', co2, (0.04, -0.061, 0.129), (0.13, 0.003, 0.008), steel, 0)
for x in [0.005, 0.037, 0.069]:
    cylinder('DMX controls', co2, (x, -0.064, 0.062), 0.006, 0.004, steel, (math.pi / 2, 0, 0))
cylinder('CO2 brass inlet', co2, (0.13, 0, 0.057), 0.017, 0.04, brass, (0, math.pi / 2, 0))
ring('Barrel upper band', co2, (-0.09, 0, 0.27), 0.028, 0.004, steel)
hose('CO2 supply hose', co2, [(0.15, 0, 0.057), (0.25, 0.09, 0.03), (0.24, 0.22, -0.18)])
outlet = empty('CO2Outlet', location=(-0.09, 0, 0.33))
outlet.parent, outlet['outlet'] = co2, True

try:
    bpy.ops.object.select_all(action='SELECT')
    bpy.context.view_layer.update()
    output = root / 'src/dmx_volcano/web/static/models/pyro-fixtures.glb'
    bpy.ops.export_scene.gltf(filepath=str(output), export_format='GLB',
        use_active_scene=True, use_selection=True, export_animations=False,
        export_extras=True, export_apply=True, export_yup=True)
    source = root / 'assets/models/pyro-fixtures.blend'
    bpy.data.libraries.write(str(source), {scene}, fake_user=True)
    result = {'model': str(output), 'source': str(source), 'bytes': output.stat().st_size,
        'objects': len(scene.objects), 'scene': scene.name}
finally:
    bpy.context.window.scene = previous_scene
