import bpy
from bpy.props import BoolProperty
from bpy.props import EnumProperty
from bpy.props import FloatProperty

from . import bone_mapping
from . import bone_utils
from importlib import reload
reload(bone_mapping)
reload(bone_utils)


status_types = (
    ('enable', "Enable", "Enable All Constraints"),
    ('disable', "Disable", "Disable All Constraints"),
    ('remove', "Remove", "Remove All Constraints")
)


skeleton_types = (
    ('unreal', "Unreal", "UE4 Skeleton"),
    ('rigify', "Rigify", "Rigify Skeleton"),
    ('rigify_meta', "Rigify Metarig", "Rigify Metarig"),
    ('mixamo', "Mixamo", "Mixamo Skeleton"),
    ('--', "--", "None")
)


class ConstraintStatus(bpy.types.Operator):
    """Disable/Enable bone constraints."""
    bl_idname = "object.charigty_set_constraints_status"
    bl_label = "Enable/disable constraints"
    bl_options = {'REGISTER', 'UNDO'}

    set_status: EnumProperty(items=status_types,
                              name="Status",
                              default='enable')

    selected_only: BoolProperty(name="Only Selected",
                                default=False)

    @classmethod
    def poll(cls, context):
        return all((context.object, context.mode == 'POSE', context.object.type == 'ARMATURE'))

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


class RevertDotBoneNames(bpy.types.Operator):
    """Reverts dots in bones that have renamed by Unreal Engine"""
    bl_idname = "object.charigty_dot_bone_names"
    bl_label = "Revert dots in Names (from UE4 renaming)"
    bl_options = {'REGISTER', 'UNDO'}

    sideletters_only: BoolProperty(name="Only Side Letters",
                                   description="i.e. '_L' to '.L'",
                                   default=True)

    selected_only: BoolProperty(name="Only Selected",
                                default=False)

    @classmethod
    def poll(cls, context):
        return all((context.object, context.mode == 'POSE', context.object.type == 'ARMATURE'))

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
    bl_idname = "object.charigty_convert_bone_names"
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
        return all((context.object, context.mode == 'POSE', context.object.type == 'ARMATURE'))

    @staticmethod
    def skeleton_from_type(skeleton_type):
        # TODO: this would be better handled by EnumTypes
        if skeleton_type == 'mixamo':
            return bone_mapping.MixamoSkeleton()
        if skeleton_type == 'rigify':
            return bone_mapping.RigifySkeleton()
        if skeleton_type == 'rigify_meta':
            return bone_mapping.RigifyMeta()
        if skeleton_type == 'unreal':
            return bone_mapping.UnrealSkeleton()

    def execute(self, context):
        src_skeleton = self.skeleton_from_type(self.source)
        trg_skeleton = self.skeleton_from_type(self.target)

        if all((src_skeleton, trg_skeleton, src_skeleton != trg_skeleton)):
            bone_names_map = src_skeleton.conversion_map(trg_skeleton)

            if self.strip_prefix:
                for bone in context.object.data.bones:
                    if self._separator not in bone.name:
                        continue

                    bone.name = bone.name.rsplit(self._separator, 1)[1]

            for src_name, trg_name in bone_names_map.items():
                src_bone = context.object.data.bones.get(src_name, None)
                if not src_bone:
                    continue
                src_bone.name = trg_name

            #TODO: drivers

        return {'FINISHED'}


class UpdateMetarig(bpy.types.Operator):
    """Match current rig pose to metarig"""
    # TODO


class ActionRangeToScene(bpy.types.Operator):
    """Set Playback range to current action Start/End"""
    bl_idname = "object.charigty_action_to_range"
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
        if not obj.animation_data.action:
            return False

        return True

    def execute(self, context):
        action_range = context.object.animation_data.action.frame_range

        scn = context.scene
        scn.frame_start = action_range[0]
        scn.frame_end = action_range[1]

        bpy.ops.action.view_all()
        return {'FINISHED'}


class MergeHeadTails(bpy.types.Operator):
    """Convert Rigify (0.5) rigs to a Game Friendly hierarchy"""
    bl_idname = "armature.charigty_merge_head_tails"
    bl_label = "Merge Head/Tails"
    bl_description = "Connect head/tails when closer than given max distance"
    bl_options = {'REGISTER', 'UNDO'}

    keep_backup: BoolProperty(
        name="Match at child head",
        description="Bring parent's bone to match child tail when possible",
        default=True
    )

    distance: FloatProperty(
        name="Distance",
        description="Max Distance for merging",
        default=0.0
    )

    # TODO:


class ConvertGameFriendly(bpy.types.Operator):
    """Convert Rigify (0.5) rigs to a Game Friendly hierarchy"""
    bl_idname = "armature.charigty_convert_gamefriendly"
    bl_label = "Rigify Game Friendly"
    bl_description = "Make the rigify deformation bones a one root rig"
    bl_options = {'REGISTER', 'UNDO'}

    keep_backup: BoolProperty(
        name="Backup",
        description="Keep copy of datablock",
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
        if obj.mode != 'EDIT':
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

        bone_utils.gamefriendly_hierarchy(ob, fix_tail=self.fix_tail, limit_scale=self.limit_scale)
        return {'FINISHED'}
