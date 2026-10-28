"""Repeat copies on Scene/Selection sends (nomad-link issue #3): nothing visible is dropped.

Runs inside Blender: blender --background --factory-startup --python tests/test_repeat_send.py
"""
import os, sys
import bpy

ADDON_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ADDON_DIR)
import nomad_blender_link as addon
addon.register()

fails = []
sent = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label} {detail}")
    if not condition:
        fails.append(label)


def capture(header, binary=b""):
    sent.append(header)


addon.connection.send = capture
addon.remote_capabilities.update({"mesh_instance", "hierarchy"})


def received(name, link_id, data=None, parent=None, repeat=False):
    """An object as receive_* leaves it: linked id, Nomad's repeat tag, wire parent."""
    if data is None and name != "Mirror":
        data = bpy.data.meshes.new(name)
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj[addon.MESH_ID] = link_id
    if repeat:
        obj[addon.REPEAT_TAG] = True
    obj.parent = parent
    return obj


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
mirror = received("Mirror", "group-1")
body = received("Body", "mesh-1", parent=mirror)
copy = received("Body Copy", "mesh-2", data=body.data, parent=mirror, repeat=True)
orphan = received("Arm Copy", "mesh-3", parent=mirror, repeat=True)  # its own datablock: no owner
bpy.context.view_layer.update()


def drain():
    while addon.pending_transfers:
        addon.flush_transfers()
        addon.pending_objects.clear()  # every request acked


def by_name(name):
    return next((header for header in sent if header.get("name") == name), None)


print("-- scene send")
addon.send_scope(bpy.context.scene, "SCENE")
drain()
names = [header.get("name") for header in sent]
check("every object travels", sorted(names) == ["Arm Copy", "Body", "Body Copy", "Mirror"], str(names))
check("the owner goes ahead of its copy", names.index("Body") < names.index("Body Copy"), str(names))
owner, shared, alone = by_name("Body"), by_name("Body Copy"), by_name("Arm Copy")
check("owner: full, unflagged", owner["type"] == "mesh_full" and "repeat" not in owner)
check("shared copy: instance, flagged, under the repeater",
      shared["type"] == "mesh_instance" and shared.get("repeat") is True and shared.get("parent_id") == "group-1",
      str({key: shared.get(key) for key in ("type", "repeat", "parent_id")}))
check("ownerless copy: full, flagged, under the repeater",
      alone["type"] == "mesh_full" and alone.get("repeat") is True and alone.get("parent_id") == "group-1",
      str({key: alone.get(key) for key in ("type", "repeat", "parent_id")}))
check("the group stays unflagged", "repeat" not in by_name("Mirror"))

print("-- selection send of a copy")
sent.clear()
addon.sent_geometry.clear()
for obj in bpy.context.view_layer.objects:
    obj.select_set(obj == copy)
addon.send_scope(bpy.context.scene, "SELECTION")
drain()
names = [header.get("name") for header in sent]
check("a selected copy brings its owner first", names == ["Body", "Body Copy"], str(names))

print("-- request_mesh for the ownerless copy")
sent.clear()
addon.receive_packet({"type": "request_mesh", "link_id": "mesh-3"}, b"")
drain()
names = [header.get("name") for header in sent]
check("the copy itself answers, not a selection send", names == ["Arm Copy"], str(names))

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
