""".nom export: write a scene, read it back with nom_file and the importer, compare.

Runs inside Blender: blender --background --factory-startup --python tests/test_export.py
"""
import math, os, sys, tempfile
from types import SimpleNamespace
import numpy
import bpy
import bmesh
from mathutils import Matrix, Vector

ADDON_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ADDON_DIR)
sys.path.insert(0, os.path.join(ADDON_DIR, "nomad_blender_link"))
import nomad_blender_link as addon
import nom_file
addon.register()

TMP = tempfile.mkdtemp()
OUT = os.path.join(TMP, "export.nom")
fails = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label} {detail}")
    if not condition:
        fails.append(label)


def world_vertices(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    values = numpy.array([list(evaluated.matrix_world @ v.co) for v in mesh.vertices])
    evaluated.to_mesh_clear()
    return values


def attribute(mesh, name, key="value"):
    found = mesh.attributes.get(name)
    if found is None:
        return None
    size = len(found.data) * (4 if key == "color" else 1)
    values = numpy.empty(size, numpy.float32 if key != "value" or found.data_type == "FLOAT" else numpy.int32)
    if found.data_type == "BOOLEAN":
        values = numpy.empty(size, bool)
    found.data.foreach_get(key, values)
    return values


# ------------------------------------------------------------------ scene setup
bpy.data.objects.remove(bpy.data.objects["Cube"])
scene = bpy.context.scene

rig = bpy.data.objects.new("Rig", None)
scene.collection.objects.link(rig)
rig.matrix_world = Matrix.LocRotScale(Vector((1, 2, 3)), Matrix.Rotation(0.7, 3, "Z").to_quaternion(), Vector((1, 1, 1)))

bm = bmesh.new()
bm.loops.layers.uv.new()
bmesh.ops.create_cube(bm, size=2.0, calc_uvs=True)
body_mesh = bpy.data.meshes.new("Body")
bm.to_mesh(body_mesh)
bm.free()
uv = body_mesh.uv_layers[0]
uv.data.foreach_set("uv", numpy.random.default_rng(1).random(len(uv.data) * 2).astype(numpy.float32))
body = bpy.data.objects.new("Body", body_mesh)
scene.collection.objects.link(body)
body.parent = rig
body.matrix_basis = Matrix.LocRotScale(Vector((0.5, 0, 0)), Matrix.Rotation(0.3, 3, "X").to_quaternion(), Vector((1, 2, 1)))

count = len(body_mesh.vertices)
rng = numpy.random.default_rng(2)
colors = body_mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
color_values = numpy.column_stack((rng.random((count, 3)), numpy.linspace(0.2, 1.0, count))).astype(numpy.float32)
colors.data.foreach_set("color", color_values.ravel())
body_mesh.color_attributes.active_color = colors
roughness = rng.random(count).astype(numpy.float32)
addon.set_float_attribute(body_mesh, addon.ROUGHNESS_ATTRIBUTE, roughness)
mask = rng.random(count).astype(numpy.float32)
addon.set_sculpt_mask(body_mesh, mask)
groups = numpy.array([0, 1, 1, 2, 2, 2], numpy.int32)
addon.set_face_groups(body_mesh, groups)
hidden = numpy.array([False, False, True, False, False, False])
addon.set_hidden_faces(body_mesh, hidden)

body.shape_key_add(name="Basis")
lift = body.shape_key_add(name="Lift")
lift.data.foreach_set("co", (numpy.array([list(v.co) for v in body_mesh.vertices]) + [0, 0, 1]).astype(numpy.float32).ravel())
lift.value = 0.5
muted = body.shape_key_add(name="Muted")
muted.data.foreach_set("co", (numpy.array([list(v.co) for v in body_mesh.vertices]) + [3, 0, 0]).astype(numpy.float32).ravel())
muted.value = 1.0
muted.mute = True
body.active_shape_key_index = 1

material = bpy.data.materials.new("Clay")
material.use_nodes = True
shader = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
shader.inputs["Roughness"].default_value = 0.3
shader.inputs["Metallic"].default_value = 0.6
image = bpy.data.images.new("swatch", 4, 4)
image.pixels = [0.25] * 64
image.filepath_raw = os.path.join(TMP, "swatch.png")
image.file_format = "PNG"
image.save()
image.pack()
texture = material.node_tree.nodes.new("ShaderNodeTexImage")
texture.image = image
material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
body_mesh.materials.append(material)

twin = bpy.data.objects.new("Twin", body_mesh)
scene.collection.objects.link(twin)
twin.location = (-4, 0, 0)

bpy.ops.mesh.primitive_cylinder_add(vertices=12, end_fill_type="NGON", location=(0, 5, 0))
ngon = bpy.context.active_object
ngon.name = "Ngon"

bpy.ops.mesh.primitive_cube_add(location=(0, -5, 0))
bent = bpy.context.active_object
bent.name = "Bent"
bent.modifiers.new("Subdivision", "SUBSURF").levels = 1

lamp = bpy.data.objects["Light"]
lamp.data.type = "SPOT"
lamp.data.spot_size = 0.8
lamp.data.energy = 500.0
lamp.data.color = (1.0, 0.5, 0.25)
lens = bpy.data.objects["Camera"]
lens.data.sensor_fit = "VERTICAL"
lens.data.lens = 35.0
expected_fov = lens.data.angle_y

bpy.context.view_layer.update()
expected_still = {obj.name: obj.matrix_world.copy() for obj in (lamp, lens)}
expected = {obj.name: (obj.matrix_world.copy(), world_vertices(obj)) for obj in (body, twin, ngon, bent)}
expected_rig = rig.matrix_world.copy()
expected_bent_count = len(expected["Bent"][1])
original_uvs = numpy.empty(len(uv.data) * 2, numpy.float32)
uv.data.foreach_get("uv", original_uvs)
original_bytes = bytes(image.packed_file.data)

# ---------------------------------------------------------------------- export
print("-- export")
objects_out, skipped = addon.export_nom(bpy.context, OUT, lights=False)
check("lights off: four meshes, light and camera skipped", (objects_out, skipped) == (4, 2), f"({objects_out}, {skipped})")
check("lights off: no light or camera entries", "lights" not in nom_file.NomFile(OUT).json
      and "cameras" not in nom_file.NomFile(OUT).json)

for obj in bpy.context.view_layer.objects:
    obj.select_set(obj.name in {"Body", "Twin"})
bpy.context.view_layer.objects.active = body
objects_out, skipped = addon.export_nom(bpy.context, OUT)
check("six objects exported, nothing skipped", (objects_out, skipped) == (6, 0), f"({objects_out}, {skipped})")

nom = nom_file.NomFile(OUT)
check("version 7", nom.version == 7)
nodes = {node.name: node for node in nom_file.scene(nom)}
check("selection", sorted(name for name, node in nodes.items() if node.source.get("selected")) == ["Body", "Twin"])
check("active object", [name for name, node in nodes.items() if node.source.get("selected_main")] == ["Body"])
names = [node.name for node in nom_file.scene(nom)]
check("hierarchy: Body under Rig", names == ["Bent", "Camera", "Light", "Ngon", "Rig", "Body", "Twin"], str(names))
check("Twin shares Body's mesh entry", len(nom.json["meshes"]) == 3)
arrays = [entry for mesh in nom.json["meshes"] for entry in mesh.values() if isinstance(entry, dict)]
check("every array raw", arrays and all(entry["lz4"] is False for entry in arrays))
check("one embedded image", len(nom.json.get("images", [])) == 1)
light_entry = nom_file.entry(nom, "lights", nodes["Light"].light)
check("light keys", light_entry.get("type") == "spot" and "light_type" not in light_entry
      and abs(light_entry["spot_angle"] - 0.8) < 1e-6 and light_entry["power"] == 500.0, str(light_entry))
camera_entry = nom_file.entry(nom, "cameras", nodes["Camera"].camera)
check("camera keys", abs(camera_entry.get("fovy", 0) - math.degrees(expected_fov)) < 1e-4
      and camera_entry["orthographic"] is False and len(camera_entry["pivot"]) == 3, str(camera_entry))
ngon_geometry = nom_file.geometry(nom, nodes["Ngon"].mesh)
check("n-gon caps triangulated", len(ngon_geometry.faces) == 12 + 2 * 10, f"({len(ngon_geometry.faces)})")
settings = nom.json["settings"]
check("camera written, eye in front of the pivot", settings["camera_translation"][2] > 0)

print("-- camera without a viewport")
real_find = addon.find_view3d
addon.find_view3d = lambda: None
writer = nom_file.NomWriter()
addon.export_nom_view(writer, Matrix.Identity(4), [bent])
camera = writer.json["settings"]
check("front view", camera["camera_rotation"] == [0, 0, 0, 1] and camera["camera_translation"][2] > 0)
check("pivot at the framed bounds", (Vector(camera["camera_pivot"]) - addon.TO_NOMAD @ bent.location).length < 1e-5)

# the viewport camera: Nomad's eye and view direction must match Blender's
print("-- viewport camera")
view_matrix = (Matrix.Translation((0.3, -0.2, -9.0)) @ Matrix.Rotation(0.4, 4, "X") @ Matrix.Rotation(-0.9, 4, "Z"))
region = SimpleNamespace(view_matrix=view_matrix, view_location=Vector((0.5, 1.0, -0.25)))
addon.find_view3d = lambda: (SimpleNamespace(region_3d=region), None)
writer = nom_file.NomWriter()
addon.export_nom_view(writer, Matrix.Identity(4), [])
addon.find_view3d = real_find
camera = writer.json["settings"]
q = Matrix.Identity(3).to_quaternion()
q.x, q.y, q.z, q.w = camera["camera_rotation"]
pivot = Vector(camera["camera_pivot"])
eye = pivot + q.conjugated() @ Vector(camera["camera_translation"])
blender_eye = view_matrix.inverted().translation
check("eye matches", (eye - addon.TO_NOMAD @ blender_eye).length < 1e-5)
forward = view_matrix.inverted().to_3x3() @ Vector((0, 0, -1))
check("view direction matches", (q @ (addon.TO_NOMAD.to_3x3() @ forward) - Vector((0, 0, -1))).length < 1e-5)
check("pivot in Nomad space", (pivot - addon.TO_NOMAD @ Vector((0.5, 1.0, -0.25))).length < 1e-6)

# ---------------------------------------------------------------- re-import
print("-- re-import")
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
for mesh in list(bpy.data.meshes):
    bpy.data.meshes.remove(mesh)
created, missed = addon.import_nom(bpy.context, OUT)
bpy.context.view_layer.update()
objects = {obj.name: obj for obj in created}
check("every node back", sorted(objects) == ["Bent", "Body", "Camera", "Light", "Ngon", "Rig", "Twin"],
      str(sorted(objects)))
for name, matrix in expected_still.items():
    check(f"{name} world matrix", numpy.allclose(numpy.array(objects[name].matrix_world), numpy.array(matrix), atol=1e-5))
light = objects["Light"].data
check("light type, cone, power", light.type == "SPOT" and abs(light.spot_size - 0.8) < 1e-6
      and abs(light.energy - 500.0) < 1e-4)
check("light color", numpy.allclose(tuple(light.color), (1.0, 0.5, 0.25), atol=1e-6))
check("camera field of view", abs(objects["Camera"].data.angle_y - expected_fov) < 1e-5)
check("Rig world", numpy.allclose(numpy.array(objects["Rig"].matrix_world), numpy.array(expected_rig), atol=1e-5))
for name, (matrix, vertices) in expected.items():
    obj = objects[name]
    check(f"{name} world matrix", numpy.allclose(numpy.array(obj.matrix_world), numpy.array(matrix), atol=1e-5))
    check(f"{name} world vertices", numpy.allclose(world_vertices(obj), vertices, atol=1e-5))

body = objects["Body"]
mesh = body.data
check("Twin is an instance", objects["Twin"].data is mesh)
check("Body is parented to Rig", body.parent is objects["Rig"])
keys = mesh.shape_keys.key_blocks
check("shape keys back", [key.name for key in keys] == ["Basis", "Lift", "Muted"])
check("Lift value", abs(keys["Lift"].value - 0.5) < 1e-6)
check("Muted stays muted", keys["Muted"].mute)
check("active key", body.active_shape_key_index == 1)
imported_uvs = numpy.empty(len(mesh.uv_layers[0].data) * 2, numpy.float32)
mesh.uv_layers[0].data.foreach_get("uv", imported_uvs)
check("uvs per corner", numpy.allclose(imported_uvs, original_uvs, atol=1e-6))
imported_colors = attribute(mesh, addon.COLOR_ATTRIBUTE, "color").reshape(-1, 4)
check("colors", numpy.allclose(imported_colors, color_values, atol=0.01))
check("roughness", numpy.allclose(attribute(mesh, addon.ROUGHNESS_ATTRIBUTE), roughness, atol=1 / 255))
check("mask", numpy.allclose(attribute(mesh, addon.MASK_ATTRIBUTE), mask, atol=1e-4))
check("face groups", numpy.array_equal(attribute(mesh, addon.FACE_GROUP_ATTRIBUTE), groups))
check("hidden faces", numpy.array_equal(attribute(mesh, addon.HIDE_FACE_ATTRIBUTE), hidden))
check("Bent carries the modifier result", len(objects["Bent"].data.vertices) == expected_bent_count)

shader = next(node for node in mesh.materials[0].node_tree.nodes if node.type == "BSDF_PRINCIPLED")
check("material roughness", abs(shader.inputs["Roughness"].default_value - 0.3) < 1e-6)
check("material metalness", abs(shader.inputs["Metallic"].default_value - 0.6) < 1e-6)
images = [node.image for node in mesh.materials[0].node_tree.nodes if node.type == "TEX_IMAGE" and node.image]
check("texture bytes round-trip", any(bytes(image.packed_file.data) == original_bytes for image in images if image.packed_file))

# ---------------------------------------------------------------- selection
print("-- selected only")
for obj in bpy.context.view_layer.objects:
    obj.select_set(obj.name == "Twin")
meshes, skipped = addon.export_nom(bpy.context, OUT, selection=True)
selected = nom_file.NomFile(OUT)
check("only the selection", meshes == 1 and [node.name for node in nom_file.scene(selected)] == ["Twin"])

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
