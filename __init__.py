import json

import bpy
from mathutils import Matrix, Vector
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup


ADDON_LABEL = "Deform to Corrective"
AUTO_VALUE = "__AUTO__"
DEFAULT_TOLERANCE_RELATIVE = 1.0e-6
MIN_EPSILON_ABSOLUTE = 1.0e-6
MIN_TOLERANCE_ABSOLUTE = 1.0e-7
SINGULAR_DETERMINANT_THRESHOLD = 1.0e-10

RIG_CANDIDATE_TYPES = {
    'ARMATURE',
    'HOOK',
    'LATTICE',
    'MESH_DEFORM',
    'SURFACE_DEFORM',
}

DEFORMATION_TYPES = {
    'CLOTH',
    'SOFT_BODY',
    'CAST',
    'CURVE',
    'DISPLACE',
    'HOOK',
    'LAPLACIANDEFORM',
    'LATTICE',
    'MESH_CACHE',
    'MESH_SEQUENCE_CACHE',
    'MESH_DEFORM',
    'SHRINKWRAP',
    'SIMPLE_DEFORM',
    'SMOOTH',
    'CORRECTIVE_SMOOTH',
    'LAPLACIANSMOOTH',
    'SURFACE_DEFORM',
    'WARP',
    'WAVE',
}

NON_TARGET_TYPES = {
    'COLLISION',
    'PARTICLE_SYSTEM',
}

KNOWN_TOPOLOGY_CHANGING_TYPES = {
    'ARRAY',
    'BOOLEAN',
    'BUILD',
    'DECIMATE',
    'EDGE_SPLIT',
    'EXPLODE',
    'FLUID',
    'MASK',
    'MIRROR',
    'MULTIRES',
    'REMESH',
    'SCREW',
    'SKIN',
    'SOLIDIFY',
    'SUBSURF',
    'TRIANGULATE',
    'VOLUME_TO_MESH',
    'WELD',
    'WIREFRAME',
}

_RIG_ITEMS_CACHE = []


def _active_mesh(context):
    obj = context.active_object if context else None
    if obj and obj.type == 'MESH':
        return obj
    return None


def _enabled_modifiers(obj):
    return [mod for mod in obj.modifiers if mod.show_viewport]


def _modifier_index(obj, modifier):
    if modifier is None:
        return -1
    for index, mod in enumerate(obj.modifiers):
        if mod == modifier:
            return index
    return -1


def _active_modifier(obj):
    try:
        return obj.modifiers.active
    except Exception:
        return None


def _auto_rig_modifier(obj):
    enabled = _enabled_modifiers(obj)
    active = _active_modifier(obj)

    if active in enabled and active.type == 'ARMATURE':
        return active

    for mod in enabled:
        if mod.type == 'ARMATURE':
            return mod

    for mod in enabled:
        if mod.type in RIG_CANDIDATE_TYPES:
            return mod

    return None


def _is_auto_deformation_candidate(mod):
    return (
        mod.show_viewport
        and mod.type not in NON_TARGET_TYPES
        and mod.type not in KNOWN_TOPOLOGY_CHANGING_TYPES
        and mod.type != 'ARMATURE'
        and mod.type in DEFORMATION_TYPES
    )


def _candidate_deformation_modifiers(obj, rig_modifier=None):
    rig_index = _modifier_index(obj, rig_modifier)
    candidates = []
    for index, mod in enumerate(obj.modifiers):
        if rig_index >= 0 and index <= rig_index:
            continue
        if _is_auto_deformation_candidate(mod):
            candidates.append(mod)
    return candidates


def _resolve_rig_modifier(obj, settings):
    value = settings.rig_modifier
    if not value or value == AUTO_VALUE:
        return _auto_rig_modifier(obj)
    return obj.modifiers.get(value)


def _stored_deformation_selection(obj):
    if not getattr(obj, "dtc_deformation_selection_manual", False):
        return None
    try:
        value = json.loads(obj.dtc_deformation_selection_json or "[]")
    except Exception:
        return []
    return [name for name in value if isinstance(name, str)]


def _set_stored_deformation_selection(obj, names, manual=True):
    obj.dtc_deformation_selection_manual = bool(manual)
    obj.dtc_deformation_selection_json = json.dumps(list(names)) if manual else "[]"


def _resolve_deformation_modifiers(obj, rig_modifier=None):
    candidates = _candidate_deformation_modifiers(obj, rig_modifier)
    stored = _stored_deformation_selection(obj)

    if stored is None:
        return candidates

    selected_names = set(stored)
    return [mod for mod in candidates if mod.name in selected_names]


def rig_modifier_items(self, context):
    global _RIG_ITEMS_CACHE

    obj = _active_mesh(context)
    items = [
        (
            AUTO_VALUE,
            "Auto",
            "Automatically detect the primary enabled rig modifier (Armature preferred)",
        )
    ]

    if obj:
        for index, mod in enumerate(obj.modifiers):
            if not mod.show_viewport or mod.type not in RIG_CANDIDATE_TYPES:
                continue
            items.append(
                (
                    str(mod.name),
                    f"{index + 1}. {mod.name}",
                    f"Use '{mod.name}' as the rig anchor for automatic deformation selection",
                )
            )

    _RIG_ITEMS_CACHE = items
    return _RIG_ITEMS_CACHE


class DTC_Settings(PropertyGroup):
    rig_modifier: EnumProperty(
        name="Rig Modifier",
        description="Rig/pre-deform anchor. Auto prefers the first enabled Armature modifier",
        items=rig_modifier_items,
    )

    output_name: StringProperty(
        name="Output Shape Key",
        description="Name for the baked editable corrective Shape Key",
        default="Corrective",
    )

    iterations: IntProperty(
        name="Iterations",
        description="Refinement iterations for the numerical inverse solve",
        default=3,
        min=1,
        max=8,
    )

    epsilon_relative: FloatProperty(
        name="Probe Epsilon",
        description="Numerical Jacobian probe size as a fraction of the Basis bounding-box diagonal",
        default=1.0e-4,
        min=1.0e-7,
        max=1.0e-2,
        soft_min=1.0e-6,
        soft_max=1.0e-3,
        precision=7,
    )

    show_advanced: BoolProperty(
        name="Advanced",
        default=False,
    )

    has_result: BoolProperty(default=False, options={'HIDDEN'})
    last_output_key: StringProperty(default="", options={'HIDDEN'})
    last_rig_modifier: StringProperty(default="", options={'HIDDEN'})
    last_deformation_modifiers: StringProperty(default="", options={'HIDDEN'})
    last_max_error: FloatProperty(default=0.0, options={'HIDDEN'})
    last_mean_error: FloatProperty(default=0.0, options={'HIDDEN'})
    last_epsilon: FloatProperty(default=0.0, options={'HIDDEN'})
    last_singular_count: IntProperty(default=0, options={'HIDDEN'})


def _force_update(context):
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    depsgraph.update()
    return depsgraph


def _evaluated_positions(context, obj, expected_count, stage_name="selected modifier stack"):
    depsgraph = _force_update(context)
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()

    try:
        count = len(mesh_eval.vertices)
        if count != expected_count:
            raise RuntimeError(
                f"Vertex count changes in the {stage_name} "
                f"(base {expected_count}, evaluated {count}). "
                "Shape Keys require matching topology."
            )
        return [v.co.copy() for v in mesh_eval.vertices]
    finally:
        obj_eval.to_mesh_clear()


def _mesh_diagonal(basis):
    coords = [p.co for p in basis.data]
    if not coords:
        return 1.0

    min_co = Vector((
        min(v.x for v in coords),
        min(v.y for v in coords),
        min(v.z for v in coords),
    ))
    max_co = Vector((
        max(v.x for v in coords),
        max(v.y for v in coords),
        max(v.z for v in coords),
    ))

    diagonal = (max_co - min_co).length
    return diagonal if diagonal > 0.0 else 1.0


def _set_key_coordinates(context, key_block, coords):
    for index, co in enumerate(coords):
        key_block.data[index].co = co
    _force_update(context)


def _ensure_basis(obj):
    if obj.data.shape_keys is None:
        basis = obj.shape_key_add(name="Basis", from_mix=False)
        return basis, True
    return obj.data.shape_keys.key_blocks[0], False


def _resolved_selection_text(obj, settings):
    rig = _resolve_rig_modifier(obj, settings)
    deformations = _resolve_deformation_modifiers(obj, rig)
    rig_name = rig.name if rig else "None"
    return rig_name, [mod.name for mod in deformations]


class DTC_OT_toggle_deformation(Operator):
    bl_idname = "object.deform_to_corrective_toggle_deformation"
    bl_label = "Toggle Deformation Modifier"
    bl_description = "Include or exclude this deformation modifier from the corrective bake"
    bl_options = {'INTERNAL'}

    modifier_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.deform_to_corrective
        rig = _resolve_rig_modifier(obj, settings)
        candidates = _candidate_deformation_modifiers(obj, rig)
        valid_names = [mod.name for mod in candidates]

        if self.modifier_name not in valid_names:
            return {'CANCELLED'}

        current = [mod.name for mod in _resolve_deformation_modifiers(obj, rig)]
        selected = set(current)
        if self.modifier_name in selected:
            selected.remove(self.modifier_name)
        else:
            selected.add(self.modifier_name)

        _set_stored_deformation_selection(
            obj,
            [name for name in valid_names if name in selected],
            manual=True,
        )
        return {'FINISHED'}


class DTC_OT_select_all_deformations(Operator):
    bl_idname = "object.deform_to_corrective_select_all_deformations"
    bl_label = "Select All Enabled"
    bl_description = "Use all currently enabled supported deformation modifiers after the rig"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        obj = _active_mesh(context)
        _set_stored_deformation_selection(obj, [], manual=False)
        return {'FINISHED'}


class DTC_OT_bake_corrective(Operator):
    bl_idname = "object.deform_to_corrective_bake"
    bl_label = "Save as Corrective"
    bl_description = (
        "Capture the combined current result of the selected deformation modifiers and bake it "
        "as one editable Shape Key while compensating for the live rig stack"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        obj = _active_mesh(context)
        settings = context.scene.deform_to_corrective

        if not obj:
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        if not settings.output_name.strip():
            self.report({'ERROR'}, "Output Shape Key name cannot be empty.")
            return {'CANCELLED'}

        base_count = len(obj.data.vertices)
        if base_count == 0:
            self.report({'ERROR'}, "Active mesh has no vertices.")
            return {'CANCELLED'}

        rig_modifier = _resolve_rig_modifier(obj, settings)
        deformation_modifiers = _resolve_deformation_modifiers(obj, rig_modifier)

        if settings.rig_modifier not in {'', AUTO_VALUE} and rig_modifier is None:
            self.report({'ERROR'}, "Selected Rig Modifier no longer exists.")
            return {'CANCELLED'}

        if not deformation_modifiers:
            self.report(
                {'ERROR'},
                "No deformation modifiers selected. Enable/select at least one in the Deform N-panel.",
            )
            return {'CANCELLED'}

        rig_index = _modifier_index(obj, rig_modifier)
        deformation_indices = [_modifier_index(obj, mod) for mod in deformation_modifiers]
        if any(index < 0 for index in deformation_indices):
            self.report({'ERROR'}, "A selected Deformation Modifier no longer exists.")
            return {'CANCELLED'}

        if rig_modifier is not None and any(index <= rig_index for index in deformation_indices):
            self.report(
                {'ERROR'},
                "All selected Deformation Modifiers must be below the Rig Modifier in the stack.",
            )
            return {'CANCELLED'}

        deformation_index = max(deformation_indices)
        selected_deformation_names = {mod.name for mod in deformation_modifiers}

        old_mode = obj.mode
        old_active_key_index = obj.active_shape_key_index
        old_show_only = obj.show_only_shape_key
        modifier_viewport_states = {mod.name: mod.show_viewport for mod in obj.modifiers}
        modifier_render_states = {mod.name: mod.show_render for mod in obj.modifiers}

        created_basis = False
        corrective = None
        progress_started = False
        completed = False
        original_shape_values = {}

        settings.has_result = False

        try:
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

            basis, created_basis = _ensure_basis(obj)
            key_data = obj.data.shape_keys

            if not key_data.use_relative:
                raise RuntimeError("Only Relative Shape Keys are supported in this version.")

            original_keys = list(key_data.key_blocks)
            original_shape_values = {
                kb.name: kb.value for kb in original_keys if kb != basis
            }
            obj.show_only_shape_key = False

            for index, mod in enumerate(obj.modifiers):
                if index > deformation_index:
                    mod.show_viewport = False

            target_positions = _evaluated_positions(
                context,
                obj,
                base_count,
                stage_name=f"captured stack through '{obj.modifiers[deformation_index].name}'",
            )

            for index, mod in enumerate(obj.modifiers):
                if index > deformation_index:
                    mod.show_viewport = False
                elif mod.name in selected_deformation_names:
                    mod.show_viewport = False
                else:
                    mod.show_viewport = modifier_viewport_states.get(mod.name, mod.show_viewport)

            for kb in original_keys:
                if kb != basis:
                    kb.value = 0.0

            _force_update(context)

            mesh_size = _mesh_diagonal(basis)
            epsilon = max(mesh_size * settings.epsilon_relative, MIN_EPSILON_ABSOLUTE)
            tolerance = max(mesh_size * DEFAULT_TOLERANCE_RELATIVE, MIN_TOLERANCE_ABSOLUTE)

            _evaluated_positions(
                context,
                obj,
                base_count,
                stage_name="pre-deformation inverse stack",
            )

            corrective = obj.shape_key_add(name=settings.output_name.strip(), from_mix=False)
            corrective.relative_key = basis
            for index in range(base_count):
                corrective.data[index].co = basis.data[index].co.copy()
            corrective.value = 1.0
            _force_update(context)

            total_steps = max(1, settings.iterations * 5 + 1)
            step = 0
            context.window_manager.progress_begin(0, total_steps)
            progress_started = True

            singular_total = 0
            final_max = 0.0
            final_mean = 0.0

            print("\n" + "=" * 64)
            print("DEFORM TO CORRECTIVE")
            print("=" * 64)
            print("Object        :", obj.name)
            print("Rig anchor    :", rig_modifier.name if rig_modifier else "None")
            print("Deformations  :", ", ".join(mod.name for mod in deformation_modifiers))
            print("Output        :", corrective.name)
            print("Vertices      :", base_count)
            print("Probe epsilon :", epsilon)
            print("Tolerance     :", tolerance)
            print("Active inverse stack:")
            for index, mod in enumerate(obj.modifiers):
                if index <= deformation_index and mod.show_viewport:
                    print("  ", mod.name, f"({mod.type})")
            print("")

            for iteration in range(settings.iterations):
                print(f"---------------- ITERATION {iteration + 1} ----------------")

                current_rest = [corrective.data[i].co.copy() for i in range(base_count)]

                _set_key_coordinates(context, corrective, current_rest)
                posed_base = _evaluated_positions(context, obj, base_count, "inverse stack")
                step += 1
                context.window_manager.progress_update(step)

                probe_x = [co + Vector((epsilon, 0.0, 0.0)) for co in current_rest]
                _set_key_coordinates(context, corrective, probe_x)
                posed_x = _evaluated_positions(context, obj, base_count, "inverse stack")
                step += 1
                context.window_manager.progress_update(step)

                probe_y = [co + Vector((0.0, epsilon, 0.0)) for co in current_rest]
                _set_key_coordinates(context, corrective, probe_y)
                posed_y = _evaluated_positions(context, obj, base_count, "inverse stack")
                step += 1
                context.window_manager.progress_update(step)

                probe_z = [co + Vector((0.0, 0.0, epsilon)) for co in current_rest]
                _set_key_coordinates(context, corrective, probe_z)
                posed_z = _evaluated_positions(context, obj, base_count, "inverse stack")
                step += 1
                context.window_manager.progress_update(step)

                _set_key_coordinates(context, corrective, current_rest)

                new_rest = []
                max_before = 0.0
                mean_before = 0.0
                max_correction = 0.0
                singular_this_iteration = 0

                for index in range(base_count):
                    target_pos = target_positions[index]
                    current_pos = posed_base[index]
                    error = target_pos - current_pos
                    error_length = error.length

                    max_before = max(max_before, error_length)
                    mean_before += error_length

                    dx = (posed_x[index] - posed_base[index]) / epsilon
                    dy = (posed_y[index] - posed_base[index]) / epsilon
                    dz = (posed_z[index] - posed_base[index]) / epsilon

                    matrix = Matrix((
                        (dx.x, dy.x, dz.x),
                        (dx.y, dy.y, dz.y),
                        (dx.z, dy.z, dz.z),
                    ))

                    determinant = matrix.determinant()
                    if abs(determinant) < SINGULAR_DETERMINANT_THRESHOLD:
                        singular_this_iteration += 1
                        inverse = matrix.inverted_safe()
                    else:
                        inverse = matrix.inverted()

                    rest_correction = inverse @ error

                    if not all(abs(component) < mesh_size * 1000.0 for component in rest_correction):
                        print(f"WARNING: vertex {index} produced an absurd correction; skipped")
                        rest_correction = Vector((0.0, 0.0, 0.0))

                    max_correction = max(max_correction, rest_correction.length)
                    new_rest.append(current_rest[index] + rest_correction)

                mean_before /= base_count
                singular_total += singular_this_iteration

                _set_key_coordinates(context, corrective, new_rest)
                solved_positions = _evaluated_positions(context, obj, base_count, "inverse stack")
                step += 1
                context.window_manager.progress_update(step)

                errors = [
                    (target_positions[i] - solved_positions[i]).length
                    for i in range(base_count)
                ]
                final_max = max(errors) if errors else 0.0
                final_mean = (sum(errors) / base_count) if base_count else 0.0

                print(f"error before solve: max={max_before:.9f}, mean={mean_before:.9f}")
                print(f"max rest correction: {max_correction:.9f}")
                print("singular matrices:", singular_this_iteration)
                print(f"error after solve : max={final_max:.9f}, mean={final_mean:.9f}\n")

                if final_max <= tolerance:
                    print("Tolerance reached.")
                    break

            corrective.value = 1.0
            _force_update(context)
            final_positions = _evaluated_positions(context, obj, base_count, "inverse stack")
            final_errors = [
                (target_positions[i] - final_positions[i]).length
                for i in range(base_count)
            ]
            final_max = max(final_errors) if final_errors else 0.0
            final_mean = (sum(final_errors) / base_count) if base_count else 0.0

            worst = sorted(((err, i) for i, err in enumerate(final_errors)), reverse=True)

            settings.last_output_key = corrective.name
            settings.last_rig_modifier = rig_modifier.name if rig_modifier else "None"
            settings.last_deformation_modifiers = ", ".join(mod.name for mod in deformation_modifiers)
            settings.last_max_error = final_max
            settings.last_mean_error = final_mean
            settings.last_epsilon = epsilon
            settings.last_singular_count = singular_total
            settings.has_result = True

            print("\n" + "=" * 64)
            print("DONE")
            print("=" * 64)
            print("Corrective      :", corrective.name)
            print(f"FINAL MAX ERROR : {final_max:.10f}")
            print(f"FINAL MEAN ERROR: {final_mean:.10f}")
            print("Worst remaining vertices:")
            for err, index in worst[:15]:
                print(f"  vertex {index:5d}: {err:.10f}")
            print("Total near-singular matrices:", singular_total)
            print("=" * 64)

            completed = True

        except Exception as exc:
            if corrective is not None:
                try:
                    obj.shape_key_remove(corrective)
                    corrective = None
                except Exception:
                    pass

            if created_basis:
                try:
                    if obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) == 1:
                        obj.shape_key_clear()
                except Exception:
                    pass

            self.report({'ERROR'}, f"Save as Corrective failed: {exc}")
            print(f"[{ADDON_LABEL}] ERROR: {exc}")

        finally:
            if progress_started:
                context.window_manager.progress_end()

            for mod in obj.modifiers:
                if mod.name in modifier_viewport_states:
                    mod.show_viewport = modifier_viewport_states[mod.name]
                if mod.name in modifier_render_states:
                    mod.show_render = modifier_render_states[mod.name]

            if completed:
                for deformation_modifier in deformation_modifiers:
                    deformation_modifier.show_viewport = False
                    deformation_modifier.show_render = False

            key_data = obj.data.shape_keys
            if key_data:
                for kb in key_data.key_blocks:
                    if kb.name in original_shape_values:
                        kb.value = original_shape_values[kb.name]

                if completed and corrective is not None:
                    corrective.value = 1.0

            obj.show_only_shape_key = old_show_only

            if key_data and len(key_data.key_blocks):
                if completed and corrective is not None:
                    for index, kb in enumerate(key_data.key_blocks):
                        if kb == corrective:
                            obj.active_shape_key_index = index
                            break
                else:
                    obj.active_shape_key_index = min(old_active_key_index, len(key_data.key_blocks) - 1)

            _force_update(context)

            if old_mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode=old_mode)
                except Exception:
                    pass

        if not completed:
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"Saved '{settings.last_output_key}' from {len(deformation_modifiers)} deformation modifier(s) — "
            f"max error {settings.last_max_error:.6g}",
        )
        return {'FINISHED'}


class DTC_PT_panel(Panel):
    bl_label = "Deform to Corrective"
    bl_idname = "DTC_PT_deform_to_corrective"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Deform"

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = _active_mesh(context)
        settings = context.scene.deform_to_corrective

        rig_name, deformation_names = _resolved_selection_text(obj, settings)
        rig_modifier = _resolve_rig_modifier(obj, settings)
        deformation_candidates = _candidate_deformation_modifiers(obj, rig_modifier)
        selected_names = set(deformation_names)

        col = layout.column(align=True)
        col.prop(settings, "rig_modifier")

        layout.separator()
        header = layout.row(align=True)
        header.label(text="Deformation Modifiers")
        header.operator(
            "object.deform_to_corrective_select_all_deformations",
            text="All Enabled",
            icon='CHECKMARK',
        )

        deformer_box = layout.box()
        if deformation_candidates:
            for mod in deformation_candidates:
                row = deformer_box.row(align=True)
                selected = mod.name in selected_names
                op = row.operator(
                    "object.deform_to_corrective_toggle_deformation",
                    text=mod.name,
                    icon='CHECKBOX_HLT' if selected else 'CHECKBOX_DEHLT',
                    emboss=False,
                )
                op.modifier_name = mod.name
                row.label(text=mod.type.replace('_', ' ').title())
        else:
            deformer_box.label(text="No enabled supported deformers found", icon='INFO')

        detected = layout.box()
        detected.scale_y = 0.85
        if deformation_names:
            detected.label(
                text=f"Rig: {rig_name}   •   Baking {len(deformation_names)} modifier(s)",
                icon='CHECKMARK',
            )
        else:
            detected.label(text=f"Rig: {rig_name}   •   Nothing selected", icon='INFO')

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "output_name")
        col.prop(settings, "iterations")

        advanced_row = layout.row(align=True)
        advanced_row.prop(
            settings,
            "show_advanced",
            text="Advanced",
            emboss=False,
            icon='TRIA_DOWN' if settings.show_advanced else 'TRIA_RIGHT',
        )
        if settings.show_advanced:
            advanced = layout.box()
            advanced.prop(settings, "epsilon_relative", text="Epsilon")

        layout.separator()
        button = layout.row()
        button.scale_y = 1.25
        button.operator("object.deform_to_corrective_bake", text="Save as Corrective", icon='SHAPEKEY_DATA')

        if settings.has_result:
            result = layout.box()
            result.label(text=f"Last Bake: {settings.last_output_key}", icon='CHECKMARK')
            result.label(
                text=f"{settings.last_rig_modifier}  →  {settings.last_deformation_modifiers}"
            )
            result.label(text=f"Max Error:  {settings.last_max_error:.9g}")
            result.label(text=f"Mean Error: {settings.last_mean_error:.9g}")
            if settings.show_advanced:
                result.label(text=f"Epsilon:    {settings.last_epsilon:.9g}")
                if settings.last_singular_count:
                    result.label(
                        text=f"Near-singular matrices: {settings.last_singular_count}",
                        icon='ERROR',
                    )


def draw_modifier_quick_action(self, context):
    obj = _active_mesh(context)
    if obj is None:
        return

    layout = self.layout
    row = layout.row(align=True)
    row.scale_y = 1.15
    row.operator(
        "object.deform_to_corrective_bake",
        text="Save as Corrective",
        icon='SHAPEKEY_DATA',
    )
    layout.separator()


classes = (
    DTC_Settings,
    DTC_OT_toggle_deformation,
    DTC_OT_select_all_deformations,
    DTC_OT_bake_corrective,
    DTC_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.deform_to_corrective = PointerProperty(type=DTC_Settings)
    bpy.types.Object.dtc_deformation_selection_manual = BoolProperty(
        name="Manual Deformation Selection",
        default=False,
        options={'HIDDEN'},
    )
    bpy.types.Object.dtc_deformation_selection_json = StringProperty(
        name="Deformation Selection",
        default="[]",
        options={'HIDDEN'},
    )

    try:
        modifier_panel = getattr(bpy.types, "DATA_PT_modifiers", None)
        if modifier_panel is None:
            from bl_ui.properties_data_modifier import DATA_PT_modifiers as modifier_panel
        modifier_panel.prepend(draw_modifier_quick_action)
    except Exception as exc:
        print(f"[{ADDON_LABEL}] Could not add Modifiers quick button: {exc}")


def unregister():
    try:
        modifier_panel = getattr(bpy.types, "DATA_PT_modifiers", None)
        if modifier_panel is None:
            from bl_ui.properties_data_modifier import DATA_PT_modifiers as modifier_panel
        modifier_panel.remove(draw_modifier_quick_action)
    except Exception:
        pass

    if hasattr(bpy.types.Object, "dtc_deformation_selection_json"):
        del bpy.types.Object.dtc_deformation_selection_json
    if hasattr(bpy.types.Object, "dtc_deformation_selection_manual"):
        del bpy.types.Object.dtc_deformation_selection_manual
    if hasattr(bpy.types.Scene, "deform_to_corrective"):
        del bpy.types.Scene.deform_to_corrective

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
