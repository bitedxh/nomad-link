# Nomad Blender Link 0.11.47-dev.2

Fork of upstream 0.11.47 (commit 08391c8) with one optional object setting. The extension id is still `nomad_blender_link`. Install this build instead of the official one and instead of the older "Nomad Blender Link Dev" add-on (`nomad_blender_link_dev`). Do not enable both: they register the same operators.

**Keep Blender Placement** is off by default. It is on the active object in the 3D Viewport → Nomad panel.

Turn it on for an object whose placement is owned by Blender: a Child Of or other constraint, a bone parent, or a cutter you have hidden from the render. While it is on, Nomad still replaces the mesh, the materials, and can still delete the object, but these incoming fields are ignored for that object and for its parents:

- transform and parent
- name
- visibility, including the render hide flag
- selection lock

What goes back to Nomad is the matrix Blender is actually displaying (constraints and bone parents included). Objects without the option keep upstream behavior: the link sends the authored transform, not the constraint result, and incoming transforms are applied.

A newly created object always takes Nomad's transform and parent once, even if the option is on. That is the placement. Later updates keep it. This is what stops Get, Replace All, and a Nomad-side object that arrives under a new link id from sitting at the world origin.

## What upstream 0.11.47 already does

Upstream has no placement lock. There is no button that ignores incoming transforms. **Send Modifier Results** sends the modifier stack as geometry and then refuses Nomad's mesh; it does not send constraints and it does not freeze placement.

**File → Export → Nomad Sculpt (.nom)** does write the evaluated world matrix, so a one-shot file already includes constraints. The live link and Send do not, unless Keep Blender Placement is on for that object.

A Nomad transform that is slightly skewed is kept on `.nom` import. That is unrelated to objects landing on the origin.

## Remesh

Matching is by link id (`mesh_id`), not by topology.

- Same link id: the existing object is rebuilt in place. Vertex and face counts can change. Shape keys are replaced by the sculpt layers in that packet (none, if the remesh flattened them). UVs, masks, and face groups are whatever the packet contains. A Mirror, Subdivision, Armature, or Multires modifier is left on the object. Vertex group names survive; weights survive only when the vertex count is unchanged. Upstream 0.11.45 documents the same weight rule.
- Same link id, and Keep Blender Placement on: the object stays where Blender put it while the vertices switch into Nomad's new local space. A remesh that recenters the object in Nomad will look offset. Turn the option off before that remesh, or send the object again from Blender afterwards. Upstream follows Nomad's new matrix, which is what you want for a recenter.
- New link id: Blender creates a second object and gives it Nomad's transform. The previous object stays until Nomad deletes it. This build does not leave the new one at the origin. If the geometry id is unchanged, the two objects share one mesh and both show the remeshed topology.
- A sculpt delta whose `vertex_count` does not match the Blender mesh is dropped and a full mesh is requested. Upstream 0.11.47 applied any index that still fell in range, which paints the old vertices after a rebuild.

## Dropped from the 0.11.46-dev.1 experiment

Side-by-side package id, the renamed panel, the block on automatic updates, and default-on placement. Those were install guards. The placement behavior itself was not in upstream and is kept, off by default, with the origin hole closed.

## Checks still needing a real Nomad

Headless Blender 5.2.2 covered simulated packets only. In Nomad: Get a new mesh and confirm it is not on the origin; move an existing linked mesh in Nomad and confirm it moves in Blender unless Keep Blender Placement is on; pose a bone-parented mesh and confirm Nomad receives the posed location; remesh, and confirm one object follows Nomad's new shape and position rather than a second mesh appearing at the origin.
