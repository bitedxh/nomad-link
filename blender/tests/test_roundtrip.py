"""Export round-trip: a Blender scene survives export_nom + import_nom, object by object.

A synthetic scene covers deep hierarchies (non-uniform parent scale, parent inverses, skew,
hidden parents, lights and cameras inside the tree) and shape keys (negative, above 1, muted).
Then every shipped .nom is imported, exported, re-imported and compared; that part skips when
Nomad's assets/ is not beside the repo.

Runs inside Blender: blender --background --factory-startup --python tests/test_roundtrip.py
"""
import glob, math, os, sys, tempfile
import numpy
import bpy
from mathutils import Matrix, Vector

ADDON_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ASSETS = os.path.abspath(os.path.join(ADDON_DIR, *[".."] * 3, "assets"))
sys.path.insert(0, ADDON_DIR)
import nomad_blender_link as addon
addon.register()

OUT = os.path.join(tempfile.mkdtemp(), "roundtrip.nom")
UNSKEW = "nomad_unskew"  # the importer's skew carriers: they come and go, the worlds must not
fails = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label} {detail}")
    if not condition:
        fails.append(label)


def clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for collection in (bpy.data.meshes, bpy.data.lights, bpy.data.cameras, bpy.data.materials, bpy.data.images):
        for item in list(collection):
            collection.remove(item)


def floats(collection, attribute, components):
    values = numpy.empty(len(collection) * components, numpy.float32)
    collection.foreach_get(attribute, values)
    return values.reshape(-1, components).astype(numpy.float64)


def ancestor(obj):
    parent = obj.parent
    while parent is not None and parent.name.startswith(UNSKEW):
        parent = parent.parent
    return parent.name if parent else None


def snapshot():
    """Everything the round-trip must keep, keyed by object name."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    state = {}
    for obj in bpy.data.objects:
        if obj.name.startswith(UNSKEW):
            continue
        world = numpy.array(obj.matrix_world)
        record = {"type": obj.type, "world": world, "parent": ancestor(obj),
                  "hidden": obj.hide_get() if obj.type != "EMPTY" else False}
        if obj.type == "MESH":
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            co = floats(mesh.vertices, "co", 3)
            record["vertices"] = co @ world[:3, :3].T + world[:3, 3]
            record["faces"] = len(mesh.polygons)
            color = mesh.color_attributes.active_color
            record["colors"] = floats(color.data, "color", 4) if color is not None else None
            evaluated.to_mesh_clear()
            keys = obj.data.shape_keys.key_blocks if obj.data.shape_keys else ()
            record["keys"] = [(key.name, round(key.value, 5), key.mute, floats(key.data, "co", 3)) for key in keys]
            record["active_key"] = obj.active_shape_key_index if keys else 0
            record["data"] = obj.data.name
        elif obj.type == "LIGHT":
            record["light"] = (obj.data.type, round(obj.data.energy, 4), tuple(round(c, 5) for c in obj.data.color))
        elif obj.type == "CAMERA":
            record["camera"] = (obj.data.type, round(obj.data.angle_y, 5))
        state[obj.name] = record
    return state


def instance_groups(state):
    groups = {}
    for name, record in state.items():
        if record["type"] == "MESH":
            groups.setdefault(record["data"], set()).add(name)
    return sorted(sorted(group) for group in groups.values())


def compare(label, before, after):
    """True when after keeps every object of before; reports the first differences."""
    problems = []
    if sorted(before) != sorted(after):
        problems.append(f"objects {sorted(set(before) ^ set(after))[:6]}")
    for name in sorted(set(before) & set(after)):
        a, b = before[name], after[name]
        size = max(1.0, float(numpy.abs(a["world"][:3, 3]).max()))
        if a["type"] != b["type"] and not (a["type"] == "EMPTY" or b["type"] == "EMPTY"):
            problems.append(f"{name}: type {a['type']} -> {b['type']}")
            continue
        if a["parent"] != b["parent"]:
            problems.append(f"{name}: parent {a['parent']} -> {b['parent']}")
        if not numpy.allclose(a["world"], b["world"], atol=1e-4 * size):
            problems.append(f"{name}: world matrix")
        if a["hidden"] != b["hidden"]:
            problems.append(f"{name}: hidden {a['hidden']} -> {b['hidden']}")
        if a["type"] == "MESH":
            extent = max(1.0, float(numpy.abs(a["vertices"]).max())) if len(a["vertices"]) else 1.0
            if a["faces"] != b["faces"]:
                problems.append(f"{name}: faces {a['faces']} -> {b['faces']}")
            if a["vertices"].shape != b["vertices"].shape or not numpy.allclose(a["vertices"], b["vertices"],
                                                                                 atol=1e-4 * extent):
                problems.append(f"{name}: world vertices")
            if (a["colors"] is None) != (b["colors"] is None) or (
                    a["colors"] is not None and not numpy.allclose(a["colors"], b["colors"], atol=0.02)):
                problems.append(f"{name}: colors")
            if [key[:3] for key in a["keys"]] != [key[:3] for key in b["keys"]]:
                problems.append(f"{name}: keys {[k[:3] for k in a['keys']]} -> {[k[:3] for k in b['keys']]}")
            elif any(not numpy.allclose(x[3], y[3], atol=1e-4 * extent) for x, y in zip(a["keys"], b["keys"])):
                problems.append(f"{name}: key positions")
            if a["active_key"] != b["active_key"]:
                problems.append(f"{name}: active key {a['active_key']} -> {b['active_key']}")
        if a.get("light") != b.get("light"):
            problems.append(f"{name}: light {a.get('light')} -> {b.get('light')}")
        if a.get("camera") != b.get("camera"):
            problems.append(f"{name}: camera {a.get('camera')} -> {b.get('camera')}")
    if instance_groups(before) != instance_groups(after):
        problems.append("instance groups")
    check(label, not problems, "; ".join(problems[:6]))
    return not problems


def roundtrip(label, cascade=()):
    """`cascade`: objects a hidden parent hides in Nomad, whatever their own state in Blender."""
    before = snapshot()
    for name in cascade:
        before[name]["hidden"] = True
    addon.export_nom(bpy.context, OUT)
    clear()
    addon.import_nom(bpy.context, OUT)
    bpy.context.view_layer.update()
    return compare(label, before, snapshot())


def link(obj, parent=None, keep=True):
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        world = obj.matrix_world.copy()
        obj.parent = parent
        if keep:  # Ctrl+P: the parent's current world lands in matrix_parent_inverse
            obj.matrix_parent_inverse = parent.matrix_world.inverted()
        obj.matrix_world = world
    bpy.context.view_layer.update()
    return obj


def cube_mesh(name, size=1.0):
    mesh = bpy.data.meshes.new(name)
    s = size
    mesh.from_pydata([(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s), (-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)],
                     [], [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    return mesh


# ------------------------------------------------------------- synthetic scene
print("-- synthetic hierarchy and layers")
clear()
root = bpy.data.objects.new("Root", None)
root.matrix_world = Matrix.LocRotScale(Vector((1, -2, 0.5)), Matrix.Rotation(0.4, 3, "Y").to_quaternion(),
                                       Vector((1, 2, 3)))
link(root)

arm = bpy.data.objects.new("Arm", cube_mesh("Arm"))
link(arm, root, keep=False)
# rotated inside the non-uniformly scaled parent: a skewed world Blender cannot author
arm.matrix_basis = Matrix.Translation((0.5, 0, 0.3)) @ Matrix.Rotation(0.5, 4, "X")

hand_mesh = cube_mesh("Hand", 0.5)
rng = numpy.random.default_rng(7)
color = hand_mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
color.data.foreach_set("color", numpy.column_stack((rng.random((8, 3)), numpy.ones(8))).astype(numpy.float32).ravel())
hand = bpy.data.objects.new("Hand", hand_mesh)
hand.matrix_world = Matrix.Translation((3, 1, 2)) @ Matrix.Rotation(1.1, 4, "Z")
link(hand, arm, keep=False)
hand.shape_key_add(name="Basis")
base = floats(hand_mesh.vertices, "co", 3)
for name, shift, value, mute in (("Fist", (0, 0, 1), -0.5, False), ("Spread", (1, 0, 0), 1.7, False),
                                 ("Off", (0, 2, 0), 0.8, True)):
    key = hand.shape_key_add(name=name)
    key.slider_min, key.slider_max = -2.0, 2.0
    key.data.foreach_set("co", (base + shift + rng.random((8, 3)) * 0.1).astype(numpy.float32).ravel())
    key.value = value
    key.mute = mute
hand.active_shape_key_index = 2

lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "SUN"))
lamp.data.energy = 3.0
lamp.matrix_world = Matrix.Translation((0, 0, 5)) @ Matrix.Rotation(0.3, 4, "X")
link(lamp, hand)

hidden = bpy.data.objects.new("Hidden", None)
link(hidden, root)
hidden.hide_set(True)
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
cam.data.type = "ORTHO"
cam.matrix_world = Matrix.Translation((0, -8, 1)) @ Matrix.Rotation(math.pi / 2, 4, "X")
link(cam, hidden)
ghost = bpy.data.objects.new("Ghost", cube_mesh("Ghost"))
link(ghost, hidden)
ghost.hide_set(True)
shown = bpy.data.objects.new("Shown", cube_mesh("Shown"))
shown.location = (0, 3, 0)
link(shown, ghost)

twin = bpy.data.objects.new("HandTwin", hand_mesh)
twin.matrix_world = Matrix.Translation((-3, 0, 0))
link(twin)
twin.active_shape_key_index = 2  # one mesh entry holds one active layer for all its instances

bpy.context.view_layer.update()
axes = [Vector(arm.matrix_world.col[i][:3]) for i in range(3)]
check("the scene is skewed where intended", max(abs(axes[0].dot(axes[1])), abs(axes[1].dot(axes[2])),
                                                abs(axes[0].dot(axes[2]))) > 1e-2)
roundtrip("synthetic scene round-trips", cascade=["Shown"])
check("skew needed an unskew carrier on re-import", any(o.name.startswith(UNSKEW) for o in bpy.data.objects))

# the re-imported scene goes around once more: the carriers export as groups and must not drift
roundtrip("second round-trip is stable")

# ------------------------------------------------------------ shipped .nom files
files = sorted(glob.glob(os.path.join(ASSETS, "**", "*.nom"), recursive=True))
if not files:
    print("--  no assets/ beside the repo, corpus skipped")
for path in files:
    clear()
    created, _ = addon.import_nom(bpy.context, path)
    bpy.context.view_layer.update()
    keys = sum(len(o.data.shape_keys.key_blocks) - 1 for o in created if o.type == "MESH" and o.data.shape_keys)
    roundtrip(f"{os.path.relpath(path, ASSETS)} ({len(created)} objects, {keys} layers)")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
