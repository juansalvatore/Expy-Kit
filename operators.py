import bpy
from bpy.props import BoolProperty
from bpy.props import EnumProperty
from bpy.props import FloatProperty
from bpy.props import IntProperty
from bpy.props import StringProperty
from bpy.props import CollectionProperty
from bpy.props import FloatVectorProperty

from bpy_extras.io_utils import ImportHelper

from itertools import chain

from . import bone_mapping
from . import bone_utils
from . import fbx_helper

from importlib import reload
reload(bone_mapping)
reload(bone_utils)
reload(fbx_helper)

from mathutils import Vector
from math import pi
import os
import typing


status_types = (
    ('enable', "Enable", "Enable All Constraints"),
    ('disable', "Disable", "Disable All Constraints"),
    ('remove', "Remove", "Remove All Constraints")
)


skeleton_types = (
    ('unreal', "Unreal", "UE4 Skeleton"),
    ('rigify', "Rigify", "Rigify Skeleton"),
    ('rigify_meta', "Rigify Metarig", "Rigify Metarig"),
    ('rigify_fk', "Rigify FK Controls", "Rigify FK"),
    ('rigify_ik', "Rigify IK Controls", "Rigify IK"),
    ('mixamo', "Mixamo", "Mixamo Skeleton"),
    ('--', "--", "None")
)


class ConstraintStatus(bpy.types.Operator):
    """Disable/Enable bone constraints."""
    bl_idname = "object.expykit_set_constraints_status"
    bl_label = "Enable/disable constraints"
    bl_options = {'REGISTER', 'UNDO'}

    set_status: EnumProperty(items=status_types,
                              name="Status",
                              default='enable')

    selected_only: BoolProperty(name="Only Selected",
                                default=False)

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        if context.mode != 'POSE':
            return False
        if context.object.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        bones = context.selected_pose_bones if self.selected_only else context.object.pose.bones
        if self.set_status == 'remove':
            for bone in bones:
                for constr in reversed(bone.constraints):
                    bone.constraints.remove(constr)
        else:
            for bone in bones:
                for constr in bone.constraints:
                    constr.mute = self.set_status == 'disable'
        return {'FINISHED'}


class SelectConstrainedControls(bpy.types.Operator, ImportHelper):
    bl_idname = "armature.expykit_select_constrained_ctrls"
    bl_label = "Select constrained controls"
    bl_description = "Select bone controls with constraint"
    bl_options = {'PRESET', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        if context.mode != 'POSE':
            return False
        if context.object.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        ob = context.object

        for bone in ob.data.bones:
            if bone.use_deform:
                bone.select = False
                continue
            pbone = ob.pose.bones[bone.name]
            if len(pbone.constraints) == 0:
                bone.select = False
                continue

            bone.select = bool(pbone.custom_shape)

        return {'FINISHED'}


class RevertDotBoneNames(bpy.types.Operator):
    """Reverts dots in bones that have renamed by Unreal Engine"""
    bl_idname = "object.expykit_dot_bone_names"
    bl_label = "Revert dots in Names (from UE4 renaming)"
    bl_options = {'REGISTER', 'UNDO'}

    sideletters_only: BoolProperty(name="Only Side Letters",
                                   description="i.e. '_L' to '.L'",
                                   default=True)

    selected_only: BoolProperty(name="Only Selected",
                                default=False)

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        if context.mode != 'POSE':
            return False
        return context.object.type == 'ARMATURE'

    def execute(self, context):
        bones = context.selected_pose_bones if self.selected_only else context.object.pose.bones

        if self.sideletters_only:
            for bone in bones:
                for side in ("L", "R"):
                    if bone.name[:-1].endswith("_{0}_00".format(side)):
                        bone.name = bone.name.replace("_{0}_00".format(side), ".{0}.00".format(side))
                    elif bone.name.endswith("_{0}".format(side)):
                        bone.name = bone.name[:-2] + ".{0}".format(side)
        else:
            for bone in bones:
                bone.name = bone.name.replace('_', '.')

        return {'FINISHED'}


class ConvertBoneNaming(bpy.types.Operator):
    """Convert Bone Names between Naming Convention"""
    bl_idname = "object.expykit_convert_bone_names"
    bl_label = "Convert Bone Names"
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(items=skeleton_types,
                         name="Source Type",
                         default='--')

    target: EnumProperty(items=skeleton_types,
                         name="Target Type",
                         default='--')

    strip_prefix: BoolProperty(
        name="Strip Prefix",
        description="Remove prefix when found",
        default=True
    )

    # TODO: separator as a string property
    _separator = ":"

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        if context.mode != 'POSE':
            return False
        if context.object.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        src_skeleton = skeleton_from_type(self.source)
        trg_skeleton = skeleton_from_type(self.target)

        if all((src_skeleton, trg_skeleton, src_skeleton != trg_skeleton)):
            bone_names_map = src_skeleton.conversion_map(trg_skeleton)

            if self.strip_prefix:
                for bone in context.object.data.bones:
                    if self._separator not in bone.name:
                        continue

                    bone.name = bone.name.rsplit(self._separator, 1)[1]

            for src_name, trg_name in bone_names_map.items():
                try:
                    src_bone = context.object.data.bones.get(src_name, None)
                except SystemError:
                    continue
                if not src_bone:
                    continue
                src_bone.name = trg_name

            if context.object.animation_data and context.object.data.animation_data:
                for driver in chain(context.object.animation_data.drivers, context.object.data.animation_data.drivers):
                    try:
                        driver_bone = driver.data_path.split('"')[1]

                    except IndexError:
                        continue

                    try:
                        trg_name = bone_names_map[driver_bone]
                    except KeyError:
                        continue

                    driver.data_path = driver.data_path.replace('bones["{0}"'.format(driver_bone),
                                                                'bones["{0}"'.format(trg_name))

        return {'FINISHED'}


def skeleton_from_type(skeleton_type):
    # TODO: this would be better handled by EnumTypes
    if skeleton_type == 'mixamo':
        return bone_mapping.MixamoSkeleton()
    if skeleton_type == 'rigify':
        return bone_mapping.RigifySkeleton()
    if skeleton_type == 'rigify_meta':
        return bone_mapping.RigifyMeta()
    if skeleton_type == 'rigify_fk':
        return bone_mapping.RigifyCtrlsFK()
    if skeleton_type == 'rigify_ik':
        return bone_mapping.RigifyCtrlsIK()
    if skeleton_type == 'unreal':
        return bone_mapping.UnrealSkeleton()


def align_to_closer_axis(src_bone, trg_bone):
    src_rot = src_bone.matrix_local.to_3x3().inverted()
    src_x_axis = src_rot[0]
    src_y_axis = src_rot[1]
    src_z_axis = src_rot[2]

    bone_direction = trg_bone.parent.vector.normalized()
    dot_x = abs(bone_direction.dot(src_x_axis))
    dot_y = abs(bone_direction.dot(src_y_axis))
    dot_z = abs(bone_direction.dot(src_z_axis))

    matching_dot = max(dot_x, dot_y, dot_z)
    if matching_dot == dot_x:
        closer_axis = src_x_axis
    elif matching_dot == dot_y:
        closer_axis = src_y_axis
    else:
        closer_axis = src_z_axis

    offset = closer_axis * src_bone.length
    if closer_axis.dot(bone_direction) < 0:
        offset *= -1

    trg_bone.tail = trg_bone.head + offset


class ExtractMetarig(bpy.types.Operator):
    """Create Metarig from current object"""
    bl_idname = "object.expykit_extract_metarig"
    bl_label = "Extract Metarig"
    bl_description = "Create Metarig from current object"
    bl_options = {'REGISTER', 'UNDO'}

    skeleton_type: EnumProperty(items=skeleton_types,
                                name="Source Type",
                                default='--')

    offset_knee: FloatProperty(name='Offset Knee',
                               default=0.0)

    offset_elbow: FloatProperty(name='Offset Elbow',
                                default=0.0)

    offset_fingers: FloatVectorProperty(name='Offset Fingers')

    no_face: BoolProperty(name='No face bones',
                          default=True)

    rigify_names: BoolProperty(name='Use rifify names',
                               default=True)

    assign_metarig: BoolProperty(name='Assign metarig',
                                 default=True,
                                 description='Rigify will generate to the active object')

    forward_spine_roll: BoolProperty(name='Align spine frontally', default=False,
                                     description='Spine Z will face the Y axis')

    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        if context.mode != 'POSE':
            return False
        if context.object.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        src_object = context.object
        src_armature = context.object.data
        src_skeleton = skeleton_from_type(self.skeleton_type)

        if not src_skeleton:
            return {'FINISHED'}

        if self.skeleton_type != 'rigify' and self.rigify_names:
            bpy.ops.object.expykit_convert_bone_names(source=self.skeleton_type, target='rigify')
            src_skeleton = skeleton_from_type('rigify')

        try:
            metarig = next(ob for ob in bpy.data.objects if ob.type == 'ARMATURE' and ob.data.rigify_target_rig == src_object)
            met_armature = metarig.data
            create_metarig = False
        except StopIteration:
            create_metarig = True
            met_armature = bpy.data.armatures.new('metarig')
            metarig = bpy.data.objects.new("metarig", met_armature)

            context.collection.objects.link(metarig)

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')

        metarig.select_set(True)
        bpy.context.view_layer.objects.active = metarig
        bpy.ops.object.mode_set(mode='EDIT')

        if create_metarig:
            from rigify.metarigs import human
            human.create(metarig)

        met_skeleton = bone_mapping.RigifyMeta()

        def match_meta_bone(met_bone_group, src_bone_group, bone_attr, axis=None):
            met_bone = met_armature.edit_bones[getattr(met_bone_group, bone_attr)]
            src_bone = src_armature.bones.get(getattr(src_bone_group, bone_attr), None)

            if not src_bone:
                print(bone_attr, "not found in", src_armature)
                return

            met_bone.head = src_bone.head_local
            met_bone.tail = src_bone.tail_local

            if met_bone.parent and met_bone.use_connect:
                bone_dir = met_bone.vector.normalized()
                parent_dir = met_bone.parent.vector.normalized()

                if bone_dir.dot(parent_dir) < -0.6:
                    print(met_bone.name, "non aligned")
                    # TODO

            if axis:
                met_bone.roll = bone_utils.ebone_roll_to_vector(met_bone, axis)
            else:
                src_x_axis = Vector((0.0, 0.0, 1.0)) @ src_bone.matrix_local.inverted().to_3x3()
                src_x_axis.normalize()
                met_bone.roll = bone_utils.ebone_roll_to_vector(met_bone, src_x_axis)

        for bone_attr in ['hips', 'spine', 'spine1', 'spine2', 'neck', 'head']:
            if self.forward_spine_roll:
                align = Vector((0.0, -1.0, 0.0))
            else:
                align = None
            match_meta_bone(met_skeleton.spine, src_skeleton.spine, bone_attr, axis=align)

        for bone_attr in ['shoulder', 'arm', 'forearm', 'hand']:
            match_meta_bone(met_skeleton.right_arm, src_skeleton.right_arm, bone_attr)
            match_meta_bone(met_skeleton.left_arm, src_skeleton.left_arm, bone_attr)

        for bone_attr in ['upleg', 'leg', 'foot', 'toe']:
            match_meta_bone(met_skeleton.right_leg, src_skeleton.right_leg, bone_attr)
            match_meta_bone(met_skeleton.left_leg, src_skeleton.left_leg, bone_attr)

        right_leg = met_armature.edit_bones[met_skeleton.right_leg.leg]
        left_leg = met_armature.edit_bones[met_skeleton.left_leg.leg]

        offset = Vector((0.0, self.offset_knee, 0.0))
        for bone in right_leg, left_leg:
            bone.head += offset

        right_knee = met_armature.edit_bones[met_skeleton.right_arm.forearm]
        left_knee = met_armature.edit_bones[met_skeleton.left_arm.forearm]
        offset = Vector((0.0, self.offset_elbow, 0.0))

        for bone in right_knee, left_knee:
            bone.head += offset

        def match_meta_fingers(met_bone_group, src_bone_group, bone_attr):
            met_bone_names = getattr(met_bone_group, bone_attr)
            src_bone_names = getattr(src_bone_group, bone_attr)

            if not src_bone_names:
                print(bone_attr, "not found in", src_armature)
                return
            if not met_bone_names:
                print(bone_attr, "not found in", src_armature)
                return

            if 'thumb' not in bone_attr:
                met_bone = met_armature.edit_bones[met_bone_names[0]]
                src_bone = src_armature.bones.get(src_bone_names[0], None)
                if src_bone:
                    palm_bone = met_bone.parent

                    palm_bone.tail = src_bone.head_local
                    hand_bone = palm_bone.parent
                    palm_bone.head = hand_bone.head * 0.75 + src_bone.head_local * 0.25
                    palm_bone.roll = 0

            for met_bone_name, src_bone_name in zip(met_bone_names, src_bone_names):
                met_bone = met_armature.edit_bones[met_bone_name]
                try:
                    src_bone = src_armature.bones[src_bone_name]
                except KeyError:
                    print("source bone not found", src_bone_name)
                    continue

                met_bone.head = src_bone.head_local
                try:
                    met_bone.tail = src_bone.children[0].head_local
                except IndexError:
                    align_to_closer_axis(src_bone, met_bone)

                met_bone.roll = 0.0

                src_z_axis = Vector((0.0, 0.0, 1.0)) @ src_bone.matrix_local.to_3x3()

                inv_rot = met_bone.matrix.to_3x3().inverted()
                trg_z_axis = src_z_axis @ inv_rot
                dot_z = (met_bone.z_axis @ met_bone.matrix.inverted()).dot(trg_z_axis)
                met_bone.roll = dot_z * pi

                offset_fingers = Vector(self.offset_fingers) @ src_bone.matrix_local.to_3x3()
                if met_bone.head.x < 0:  # Right side
                    offset_fingers /= -100
                else:
                    offset_fingers /= 100

                if met_bone.parent.name in met_bone_names and met_bone.children:
                    met_bone.head += offset_fingers
                    met_bone.tail += offset_fingers
                    # met_bone.head += offset/100
                    # met_bone.tail += offset/100

        for bone_attr in ['thumb', 'index', 'middle', 'ring', 'pinky']:
            match_meta_fingers(met_skeleton.right_fingers, src_skeleton.right_fingers, bone_attr)
            match_meta_fingers(met_skeleton.left_fingers, src_skeleton.left_fingers, bone_attr)

        met_armature.edit_bones['spine.003'].tail = met_armature.edit_bones['spine.004'].head
        met_armature.edit_bones['spine.005'].head = (met_armature.edit_bones['spine.004'].head + met_armature.edit_bones['spine.006'].head) / 2

        # find foot vertices
        foot_verts = {}
        foot_ob = None
        # pick object with most foot verts
        for ob in bone_utils.iterate_rigged_obs(src_object):
            if src_skeleton.left_leg.foot not in ob.vertex_groups:
                continue
            grouped_verts = bone_utils.get_group_verts(ob, src_skeleton.left_leg.foot, threshold=0.8)
            if len(grouped_verts) > len(foot_verts):
                foot_verts = grouped_verts
                foot_ob = ob

        if foot_verts:
            # find rear verts (heel)
            rearest_y = max([foot_ob.data.vertices[v].co[1] for v in foot_verts])
            leftmost_x = max([foot_ob.data.vertices[v].co[0] for v in foot_verts])  # FIXME: we should counter rotate verts for more accuracy
            rightmost_x = min([foot_ob.data.vertices[v].co[0] for v in foot_verts])

            for side in "L", "R":
                heel_bone = met_armature.edit_bones['heel.02.' + side]

                heel_bone.head.y = rearest_y
                heel_bone.tail.y = rearest_y

                if heel_bone.head.x > 0:
                    heel_head = leftmost_x
                    heel_tail = rightmost_x
                else:
                    heel_head = rightmost_x * -1
                    heel_tail = leftmost_x * -1
                heel_bone.head.x = heel_head
                heel_bone.tail.x = heel_tail

                spine_bone = met_armature.edit_bones['spine']
                pelvis_bone = met_armature.edit_bones['pelvis.' + side]
                pelvis_bone.head = spine_bone.head
                pelvis_bone.tail.z = spine_bone.tail.z

                spine_bone = met_armature.edit_bones['spine.003']
                breast_bone = met_armature.edit_bones['breast.' + side]
                breast_bone.head.z = spine_bone.head.z
                breast_bone.tail.z = spine_bone.head.z

        if self.no_face:
            for bone_name in bone_mapping.rigify_face_bones:
                try:
                    face_bone = met_armature.edit_bones[bone_name]
                except KeyError:
                    continue

                met_armature.edit_bones.remove(face_bone)

        bpy.ops.object.mode_set(mode='POSE')
        if self.assign_metarig:
            met_armature.rigify_target_rig = src_object

        metarig.parent = src_object.parent
        return {'FINISHED'}


class ActionRangeToScene(bpy.types.Operator):
    """Set Playback range to current action Start/End"""
    bl_idname = "object.expykit_action_to_range"
    bl_label = "Action Range to Scene"
    bl_description = "Match scene range with current action range"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object

        if not obj:
            return False
        if not obj.mode == 'POSE':
            return False
        if not obj.animation_data:
            return False
        if not obj.animation_data.action:
            return False

        return True

    def execute(self, context):
        action_range = context.object.animation_data.action.frame_range

        scn = context.scene
        scn.frame_start = action_range[0]
        scn.frame_end = action_range[1]

        try:
            bpy.ops.action.view_all()
        except RuntimeError:
            # we are not in the timeline context, we won't set the timeline view
            pass
        return {'FINISHED'}


class MergeHeadTails(bpy.types.Operator):
    """Convert Rigify (0.5) rigs to a Game Friendly hierarchy"""
    bl_idname = "armature.expykit_merge_head_tails"
    bl_label = "Merge Head/Tails"
    bl_description = "Connect head/tails when closer than given max distance"
    bl_options = {'REGISTER', 'UNDO'}

    at_child_head: BoolProperty(
        name="Match at child head",
        description="Bring parent's tail to match child head when possible",
        default=True
    )

    min_distance: FloatProperty(
        name="Distance",
        description="Max Distance for merging",
        default=0.0
    )

    selected_only: BoolProperty(name="Only Selected",
                                default=False)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj:
            return False
        if obj.mode != 'EDIT':
            return False
        if obj.type != 'ARMATURE':
            return False

        return True

    def execute(self, context):
        if self.selected_only:
            selected_names = [bone.name for bone in context.selected_bones]
            bones = [bone for bone in context.object.data.edit_bones if bone.name in selected_names]
        else:
            bones = context.object.data.edit_bones

        for bone in bones:
            if bone.use_connect:
                continue
            if not bone.parent:
                continue

            distance = (bone.parent.tail - bone.head).length
            if distance <= self.min_distance:
                if self.at_child_head and len(bone.parent.children) == 1:
                    bone.parent.tail = bone.head

                bone.use_connect = True

        context.object.update_from_editmode()

        return {'FINISHED'}


class MakeRestPose(bpy.types.Operator):
    """Apply current pose to model and rig"""
    # TODO


class ConvertGameFriendly(bpy.types.Operator):
    """Convert Rigify (0.5) rigs to a Game Friendly hierarchy"""
    bl_idname = "armature.expykit_convert_gamefriendly"
    bl_label = "Rigify Game Friendly"
    bl_description = "Make the rigify deformation bones a one root rig"
    bl_options = {'REGISTER', 'UNDO'}

    keep_backup: BoolProperty(
        name="Backup",
        description="Keep copy of datablock",
        default=True
    )
    rename: BoolProperty(
        name="Rename",
        description="Rename rig to 'Armature'",
        default=True
    )
    eye_bones: BoolProperty(
        name="Keep eye bones",
        description="Activate 'deform' for eye bones",
        default=True
    )
    limit_scale: BoolProperty(
        name="Limit Spine Scale",
        description="Limit scale on the spine deform bones",
        default=True
    )
    fix_tail: BoolProperty(
        name="Invert Tail",
        description="Reverse the tail direction so that it spawns from hip",
        default=True
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj:
            return False
        if obj.mode != 'POSE':
            return False
        if obj.type != 'ARMATURE':
            return False
        return bool(context.active_object.data.get("rig_id"))

    def execute(self, context):
        ob = context.active_object
        if self.keep_backup:
            backup_data = ob.data.copy()
            backup_data.name = ob.name + "_GameUnfriendly_backup"
            backup_data.use_fake_user = True

        if self.rename:
            ob.name = 'Armature'
            ob.data.name = 'Armature'

        if self.eye_bones:
            # Oddly, changes to use_deform are not kept
            try:
                ob.pose.bones["MCH-eye.L"].bone.use_deform = True
                ob.pose.bones["MCH-eye.R"].bone.use_deform = True
            except KeyError:
                pass

        bpy.ops.object.mode_set(mode='EDIT')
        bone_utils.gamefriendly_hierarchy(ob, fix_tail=self.fix_tail, limit_scale=self.limit_scale)
        bpy.ops.object.mode_set(mode='POSE')
        return {'FINISHED'}


class ConstrainToArmature(bpy.types.Operator):
    bl_idname = "armature.expykit_constrain_to_armature"
    bl_label = "Constrain to Active Armature"
    bl_description = "Constrain bones of selected armatures to active armature"
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(items=skeleton_types,
                         name="Source Type",
                         default='--')

    skeleton_type: EnumProperty(items=skeleton_types,
                                name="Target Type",
                                default='--')

    ret_bones_layer: IntProperty(name="Connection bones layer",
                                 min=0, max=29, default=24,
                                 description="Armature Layer to use for connection bones")

    match_target_matrix: BoolProperty(name="Match target transform",
                                      default=False)

    mismatch_threshold: FloatProperty(
        name="Mismatching Threshold",
        description="Match target bone if not farthest than this value",
        default=0.0
    )

    check_prefix = BoolProperty(default=False, name="Check Prefix")
    _separator = ":"  # TODO: StringProperty

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) != 2:
            return False
        if context.mode != 'POSE':
            return False
        for ob in context.selected_objects:
            if ob.type != 'ARMATURE':
                return False

        return True

    def execute(self, context):
        src_skeleton = skeleton_from_type(self.source)
        trg_skeleton = skeleton_from_type(self.skeleton_type)

        if not src_skeleton:
            return {'FINISHED'}
        if not trg_skeleton:
            return {'FINISHED'}

        bone_names_map = src_skeleton.conversion_map(trg_skeleton)
        deformation_map = src_skeleton.deformation_bone_map

        trg_ob = context.active_object

        cp_suffix = 'RET'

        prefix = ""
        if self.check_prefix:
            first_bone = trg_ob.data.bones[0]
            if self._separator in first_bone.name:
                prefix = first_bone.name.rsplit(self._separator, 1)[0]
                prefix += self._separator

        for ob in context.selected_objects:
            if ob == trg_ob:
                continue

            if f'{next(iter(bone_names_map))}_{cp_suffix}' not in trg_ob.data.bones:
                # create Retarget bones
                bpy.ops.object.mode_set(mode='EDIT')
                for src_name, trg_name in bone_names_map.items():
                    if not src_name:
                        continue
                    trg_name = prefix + trg_name
                    if not trg_name:
                        continue
                    new_bone_name = bone_utils.copy_bone_to_arm(ob, trg_ob, src_name, suffix=cp_suffix)
                    if not new_bone_name:
                        continue
                    try:
                        new_parent = trg_ob.data.edit_bones[trg_name]
                    except KeyError:
                        self.report({'WARNING'}, f"{trg_name} not found in target")
                        continue

                    new_bone = trg_ob.data.edit_bones[new_bone_name]
                    mismatch = new_parent.head - new_bone.head

                    if 0.0 < mismatch.length < self.mismatch_threshold:
                        new_bone.head = new_parent.head
                        new_bone.tail += mismatch

                    new_bone.parent = new_parent

                    if self.match_target_matrix and deformation_map:
                        # counter deformation bone transform
                        def_bone = ob.data.edit_bones[deformation_map[src_name]]
                        new_bone.transform(def_bone.matrix.inverted())

                        # even transform
                        new_bone.transform(ob.matrix_world)
                        # counter target transform
                        new_bone.transform(trg_ob.matrix_world.inverted())
                        # bring under trg_bone
                        new_bone.transform(trg_ob.data.edit_bones[trg_name].matrix)

                        # orient to TARGET bone
                        trg_bone = trg_ob.data.bones[trg_name]
                        src_x_axis = Vector((0.0, 0.0, 1.0)) @ trg_bone.matrix_local.inverted().to_3x3()

                        # ctrl may have a different orient, in that case we roll them back
                        src_bone = ob.data.edit_bones[src_name]
                        ctrl_offset = src_bone.matrix @ def_bone.matrix.inverted()
                        src_x_axis = ctrl_offset @ src_x_axis
                        src_x_axis.normalize()

                        new_bone.roll = bone_utils.ebone_roll_to_vector(new_bone, src_x_axis)
                    else:
                        src_bone = ob.data.bones[src_name]
                        src_x_axis = Vector((0.0, 0.0, 1.0)) @ src_bone.matrix_local.inverted().to_3x3()
                        src_x_axis.normalize()

                        new_bone.roll = bone_utils.ebone_roll_to_vector(new_bone, src_x_axis)

                        new_bone.transform(ob.matrix_world)
                        new_bone.transform(trg_ob.matrix_world.inverted())

                    for i, L in enumerate(new_bone.layers):
                        new_bone.layers[i] = i == self.ret_bones_layer

            bpy.ops.object.mode_set(mode='POSE')
            for src_name in bone_names_map.keys():
                if not src_name:
                    continue
                try:
                    src_pbone = ob.pose.bones[src_name]
                except KeyError:
                    continue

                if not src_pbone.constraints:
                    for constr_type in 'COPY_ROTATION', 'COPY_LOCATION':
                        constr = src_pbone.constraints.new(type=constr_type)
                        constr.target = trg_ob
                        constr.subtarget = f'{src_name}_{cp_suffix}'

        return {'FINISHED'}


def validate_actions(act, path_resolve):
    for fc in act.fcurves:
        data_path = fc.data_path
        if fc.array_index:
            data_path = data_path + "[%d]" % fc.array_index
        try:
            path_resolve(data_path)
        except ValueError:
            return False  # Invalid.
    return True  # Valid.


class BakeConstrainedActions(bpy.types.Operator):
    bl_idname = "armature.expykit_bake_constrained_actions"
    bl_label = "Bake Constrained Actions"
    bl_description = "Bake Actions constrained from another Armature"
    bl_options = {'REGISTER', 'UNDO'}

    skeleton_type: EnumProperty(items=skeleton_types,
                                name="Skeleton Type",
                                default='--')

    clear_users_old: BoolProperty(name="Clear original Action Users",
                                  default=True)

    fake_user_new: BoolProperty(name="Save New Action User",
                              default=True)

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) != 2:
            return False
        if context.mode != 'POSE':
            return False
        for ob in context.selected_objects:
            if ob.type != 'ARMATURE':
                return False

        return True

    def execute(self, context):
        src_skeleton = skeleton_from_type(self.skeleton_type)
        if not src_skeleton:
            return {'FINISHED'}

        bone_names = list(bn for bn in src_skeleton.bone_names() if bn)
        trg_ob = context.active_object
        trg_ob.select_set(False)
        path_resolve = trg_ob.path_resolve

        for ob in context.selected_objects:
            if ob == trg_ob:
                # should not happen, but anyway
                continue

            for bone in ob.data.bones:
                bone.select = bone.name in bone_names

            for action in bpy.data.actions:
                if not validate_actions(action, path_resolve):
                    continue

                trg_ob.animation_data.action = action
                fr_start, fr_end = action.frame_range
                bpy.ops.nla.bake(frame_start=fr_start, frame_end=fr_end,
                                 bake_types={'POSE'}, only_selected=True,
                                 visual_keying=True, clear_constraints=False)

                ob.animation_data.action.use_fake_user = self.fake_user_new

                if self.clear_users_old:
                    action.user_clear()

            # delete Constraints
            for bone_name in bone_names:
                pbone = ob.pose.bones[bone_name]
                for constr in reversed(pbone.constraints):
                    pbone.constraints.remove(constr)

        return {'FINISHED'}


class ActionNameCandidates(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name Candidate", default="")


class RenameActionsFromFbxFiles(bpy.types.Operator, ImportHelper):
    bl_idname = "armature.expykit_rename_actions_fbx"
    bl_label = "Rename Actions from fbx data..."
    bl_description = "Rename Actions from candidate fbx files"
    bl_options = {'PRESET', 'UNDO'}

    directory: StringProperty()

    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={'HIDDEN'})

    files: CollectionProperty(
        name="File Path",
        type=bpy.types.OperatorFileListElement,
    )

    suffix: StringProperty(default="")

    def execute(self, context):
        fbx_durations = dict()
        for f in self.files:
            fbx_path = os.path.join(self.directory, f.name)
            local_time = fbx_helper.get_fbx_local_time(fbx_path)
            if not local_time:
                continue

            duration = fbx_helper.convert_from_fbx_duration(*local_time)
            duration = round(duration, 5)
            duration = str(duration)
            action_name = os.path.splitext(f.name[:-3])[0]

            try:
                fbx_durations[duration].append(action_name)
            except KeyError:  # entry doesn'exist yet
                fbx_durations[duration] = action_name
            except AttributeError:  # existing entry is not a list
                current = fbx_durations[duration]
                fbx_durations[duration] = [current, action_name]

        path_resolve = context.object.path_resolve
        for action in bpy.data.actions:
            if not validate_actions(action, path_resolve):
                continue

            start, end = action.frame_range
            ac_duration = end - start
            ac_duration /= context.scene.render.fps
            ac_duration = round(ac_duration, 5)
            ac_duration = str(ac_duration)

            try:
                fbx_match = fbx_durations[ac_duration]
            except KeyError:
                continue

            if not fbx_match:
                continue
            if isinstance(fbx_match, typing.List):
                for name in fbx_match:
                    entry = action.expykit_name_candidates.add()
                    entry.name = name
                continue

            action.name = fbx_match

        return {'FINISHED'}
