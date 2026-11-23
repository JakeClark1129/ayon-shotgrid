from pydantic import validator

from ayon_server.exceptions import BadRequestException
from ayon_server.entities.core.attrib import attribute_library
from ayon_server.settings import BaseSettingsModel, SettingsField
from ayon_server.settings.enum import (
    secrets_enum,
    task_types_enum,
    anatomy_presets_enum,
    folder_types_enum,
)


def default_shotgrid_entities():
    """The entity types that exist in ShotGrid.

    Project custom entities (CustomEntity01..CustomEntity50) are included so
    studios can sync them as folders; use 'Folder type mapping' to give
    them a readable AYON folder type.
    """
    return [
        "Project",
        "Episode",
        "Sequence",
        "Scene",
        "Shot",
        "Asset",
        "Task",
        "Version",
        "Playlist",
        "Note",
        "Reply",
    ] + [f"CustomEntity{index:02d}" for index in range(1, 51)]


def default_shotgrid_enabled_entities():
    """The entity types in ShotGrid that are enabled by default in AYON."""
    return [
        "Project",
        "Episode",
        "Sequence",
        "Shot",
        "Asset",
        "Task",
        "Version",
        "Note"
    ]


def default_shotgrid_reparenting_entities():
    """The ShotGrid entity types folder re-parenting presets can target.

    A preset for an entity type also moves the AssetCategory folders built
    for that type (see 'AssetCategory fields').
    """
    return [
        "Episode",
        "Sequence",
        "Shot",
        "Asset",
    ] + [f"CustomEntity{index:02d}" for index in range(1, 51)]


def get_default_folder_attributes():
    """Get AYON's Folder attributes

    Get all the `attribs` for Folder entities in a list
    to be consumed by the `default_factory` in the
    `ShotgridCompatibilitySettings.custom_attribs_map`
    settings.
    """
    attributes = []

    for attr_dict in attribute_library.data.get("folder", {}):
        attr_name = attr_dict["name"]

        if attr_name in ["shotgridId", "shotgridType", "tools"]:
            continue

        is_enum = "enum" in attr_dict
        attr_map = {
            "ayon": attr_name,
            "sg": "",
            "type": "list" if is_enum else attr_dict["type"],
            "scope": default_shotgrid_enabled_entities()
        }

        # Project.startDate is usually not editable in Flow
        if attr_name == "startDate":
            reduce_scope = default_shotgrid_enabled_entities()
            reduce_scope.remove("Project")
            attr_map["scope"] = reduce_scope

        if attr_map not in attributes:
            attributes.append(attr_map)

    return attributes


class ShotgridServiceSettings(BaseSettingsModel):
    """Specific settings for the ShotGrid Services: Processor, Leecher and
    Transmitter.

    The different services process events from either ShotGrid or AYON,
    this field allows to control how long to wait between each event
    is processed.
    """
    polling_frequency: int = SettingsField(
        default=10,
        title="How often (in seconds) to process ShotGrid related events.",
    )

    script_key: str = SettingsField(
        default="",
        enum_resolver=secrets_enum,
        title="ShotGrid's Script api key",
        description=(
            "AYON Secret used for Service related server operations "
            "Secret should lead to ShotGrid's Script api key. "
            "See more at: https://developer.shotgridsoftware.com/python-api/"
            "authentication.html#setting-up-shotgrid"
        ),
    )

    script_name: str = SettingsField(
        default="",
        placeholder="Create and Paste a script name here",
        title="ShotGrid's Script Name",
        description=(
            "AYON Secret used for Service related server operations "
            "Secret should lead to ShotGrid's Script Name. "
            "See more at: https://developer.shotgridsoftware.com/python-api/"
            "authentication.html#setting-up-shotgrid"
        ),
    )


class AttributesMappingModel(BaseSettingsModel):
    _layout = "compact"
    ayon: str = SettingsField(title="AYON")
    type: str = SettingsField(
        title="Field type",
        disabled=True,
    )
    sg: str = SettingsField(title="SG")
    scope: list[str] = SettingsField(
        title="Scope",
        default_factory=list,
        enum_resolver=default_shotgrid_entities
    )


class FolderReparentingParentsModel(BaseSettingsModel):
    folder_type: str = SettingsField(
        "asset",
        title="Parent Ayon Folder Type",
        enum_resolver=folder_types_enum,
        description="Type of the parent folder in AYON",
    )
    folder_name: str = SettingsField(
        "assets",
        title="Parent Ayon Folder Name",
        description="Name of the parent folder in AYON.",
    )


class FolderReparentingPresetsModel(BaseSettingsModel):

    filter_by_sg_entity_type: str = SettingsField(
        "Asset",
        title="Filter by ShotGrid Entity Type",
        enum_resolver=default_shotgrid_reparenting_entities,
        description=("Type of the ShotGrid entity to filter preset on."),
    )
    parents: list[FolderReparentingParentsModel] = SettingsField(
        title="Parents",
        default_factory=list,
        description=(
            "List of parent folders. If empty default behavior will be used. "
            "The order of the parents from top to bottom is important."
            "Within 'Root relocation' type the first parent will be "
            "the root folder."
        ),
    )


class FolderReparentingRelocateModel(BaseSettingsModel):
    """Re-parent folders with Root relocation"""
    enabled: bool = SettingsField(
        False,
        title="Enabled",
        description="Enable or disable the re-parenting",
    )

    presets: list[FolderReparentingPresetsModel] = SettingsField(
        title="Presets",
        default_factory=list,
        description=(
            "List of presets for re-parenting. "
            "If empty default behavior will be used."
        ),
    )


class FolderReparentingTypeGroupingModel(BaseSettingsModel):
    """Re-parent folders with Type grouping"""
    enabled: bool = SettingsField(
        False,
        title="Enabled",
        description="Enable or disable the re-parenting",
    )

    presets: list[FolderReparentingPresetsModel] = SettingsField(
        title="Presets",
        default_factory=list,
        description=(
            "List of presets for re-parenting. "
            "If empty default behavior will be used."
        ),
    )


class FolderReparentingModel(BaseSettingsModel):

    root_relocate: FolderReparentingRelocateModel = SettingsField(
        default_factory=FolderReparentingRelocateModel,
        title="Root relocation",
    )

    type_grouping: FolderReparentingTypeGroupingModel = SettingsField(
        default_factory=FolderReparentingTypeGroupingModel,
        title="Type grouping",
    )

class FolderTypeMappingModel(BaseSettingsModel):
    _layout = "compact"
    sg_entity_type: str = SettingsField(
        "",
        title="ShotGrid entity type",
        enum_resolver=default_shotgrid_entities,
    )
    ayon_folder_type: str = SettingsField(
        "",
        title="AYON folder type",
        description="Folder type name used in AYON, e.g. 'MasterAsset'.",
    )


class AssetCategoryFieldModel(BaseSettingsModel):
    _layout = "compact"
    sg_entity_type: str = SettingsField(
        "Asset",
        title="ShotGrid entity type",
        enum_resolver=default_shotgrid_entities,
    )
    sg_field: str = SettingsField(
        "sg_asset_type",
        title="ShotGrid field",
        description="Field whose value names the AssetCategory folder.",
    )
    skip_when_empty: bool = SettingsField(
        False,
        title="Skip entity when empty",
        description=(
            "Do not sync the entity (and its children) while the field is "
            "empty. Otherwise it is placed directly under the project."
        ),
    )


def default_asset_category_fields():
    return [
        {
            "sg_entity_type": "Asset",
            "sg_field": "sg_asset_type",
            "skip_when_empty": False,
        },
    ]


class ShotgridCompatibilitySettings(BaseSettingsModel):
    """Settings to define relationships between ShotGrid and AYON."""

    default_task_type: str = SettingsField(
        default="Generic",
        title="Default Task Type",
        enum_resolver=task_types_enum,
        description=("Default Task Type for SG tasks with missing pipeline step"),
    )
    shotgrid_enabled_entities: list[str] = SettingsField(
        title="ShotGrid Enabled Entities",
        default_factory=default_shotgrid_enabled_entities,
        enum_resolver=default_shotgrid_entities,
        description=(
            "The Entities that are enabled in ShotGrid, disable "
            "any that you do not use."
        ),
    )

    custom_attribs_map: list[AttributesMappingModel] = SettingsField(
        title="Folder Attributes Map",
        default_factory=get_default_folder_attributes,
        description=(
            "AYON attributes <> ShotGrid fields (without 'sg_' prefix!) "
            "mapping. Empty ones will be ignored. Scope is the list of "
            "ShotGrid entities that the mapping applies to. Disable any."
        ),
    )

    @validator("custom_attribs_map")
    def ensure_requests(cls, value):
        """ Ensure custom attribs map does not contain duplicated SG fields.
        """
        all_sg_fields = set()
        all_ayon_attributes = set()
        for entry in value:
            if entry.sg and entry.sg in all_sg_fields:
                raise BadRequestException(f"Duplicate mapped SG field: {entry.sg}")
            if entry.ayon and entry.ayon in all_ayon_attributes:
                raise BadRequestException(f"Duplicate mapped AYON attribute: {entry.ayon}")

            if entry.sg:
                all_sg_fields.add(entry.sg)
            if entry.ayon:
                all_ayon_attributes.add(entry.ayon)

        return value

    folder_parenting: FolderReparentingModel = SettingsField(
        title="Folder re-parenting",
        default_factory=FolderReparentingModel,
        description=("Parent folders for AYON folders matching to SG types."),
    )

    folder_type_mapping: list[FolderTypeMappingModel] = SettingsField(
        title="Folder type mapping",
        default_factory=list,
        description=(
            "AYON folder type to use for a ShotGrid entity type. Entity "
            "types not listed keep their ShotGrid name (Shot -> Shot)."
        ),
    )

    asset_category_fields: list[AssetCategoryFieldModel] = SettingsField(
        title="AssetCategory fields",
        default_factory=default_asset_category_fields,
        description=(
            "Group entities under an 'AssetCategory' folder named after a "
            "field value, when they are not linked to a synced parent "
            "entity (see the project's Tracking Settings)."
        ),
    )

    @validator("folder_type_mapping")
    def ensure_unique_folder_type_mapping(cls, value):
        sg_types = [entry.sg_entity_type for entry in value]
        ay_types = [entry.ayon_folder_type for entry in value]
        for label, names in (("ShotGrid entity type", sg_types),
                             ("AYON folder type", ay_types)):
            duplicates = {n for n in names if names.count(n) > 1}
            if duplicates:
                raise BadRequestException(
                    f"Duplicate {label} in folder type mapping: "
                    + ", ".join(sorted(duplicates)))
        return value


def _sequence_format_enum():
    return [
        {"value": "hashes", "label": "Hashes (render.####.exr)"},
        {"value": "printf", "label": "printf (render.%04d.exr)"},
    ]


class MoviePathFieldModel(BaseSettingsModel):
    """Flow Version field filled with the path of a published file."""
    _layout = "expanded"
    sg_field: str = SettingsField(
        "",
        title="Flow Version field",
        description=(
            "Field on the Flow (SG) Version that receives the path, e.g. "
            "'sg_path_to_movie', 'sg_path_to_frames' or a custom text field."
        ),
    )
    repre_names: list[str] = SettingsField(
        default_factory=list,
        title="Representation names",
        description=(
            "Published representations to take the path from, in order of "
            "preference; the first one published is used."
        ),
    )
    sequences_only: bool = SettingsField(
        False,
        title="Image sequences only",
        description="Ignore single-file representations for this field.",
    )
    sequence_format: str = SettingsField(
        "hashes",
        title="Image sequence path format",
        enum_resolver=_sequence_format_enum,
    )


class MoviePathProfile(BaseSettingsModel):
    """Profile to select which published paths are stored on the Version"""
    _layout = "expanded"
    host_names: list[str] = SettingsField(
        default_factory=list, title="Host names"
    )
    product_base_types: list[str] = SettingsField(
        default_factory=list,
        title="Product base types"
    )
    task_types: list[str] = SettingsField(
        default_factory=list,
        title="Task types",
        enum_resolver=task_types_enum
    )
    task_names: list[str] = SettingsField(
        default_factory=list,
        title="Task names")
    fields: list[MoviePathFieldModel] = SettingsField(
        default_factory=list,
        title="Flow field mappings",
        description=(
            "Flow Version field <- published representation path. When "
            "set, 'Selected representation names' below is ignored."
        ),
    )
    repre_names: list[str] = SettingsField(
        default_factory=list,
        title="Selected representation names (legacy)",
        description=(
            "Used only without field mappings: the first representation "
            "found fills sg_path_to_movie, and sg_path_to_frames too when "
            "it is an image sequence. Falls back to the thumbnail."
        ),
    )


class IntegrateMoviePathModel(BaseSettingsModel):
    profiles: list[MoviePathProfile] =  SettingsField(
        default_factory=list,
        title="Profiles for selected representations for movie path"
    )


class ShotgridPublishPlugins(BaseSettingsModel):
    IntegrateMoviePath: IntegrateMoviePathModel = SettingsField(
        default_factory=IntegrateMoviePathModel,
        title="Synchronize movie path information to Flow(SG)",
        scope=["studio", "project"],
    )


class ShotgridSettings(BaseSettingsModel):
    """ShotGrid integration settings.

    Main setting for the AYON x ShotGrid integration, these need to be filled
    out in order to for the services to correctly operate.
    """

    shotgrid_server: str = SettingsField(
        default="",
        title="ShotGrid URL",
        description="The URL to the ShotGrid Server we want to interact with.",
        example="https://my-site.shotgrid.autodesk.com",
        scope=["studio"]
    )

    shotgrid_no_ssl_validation: bool = SettingsField(
        False,
        title="No SSL validation",
        description="Turns off hostname matching validation for SSL certificates.",
    )
    shotgrid_project_code_field: str = SettingsField(
        default="code",
        title="ShotGrid Project Code field name",
        description=(
            "In order to create AYON projects, we need a Project Code, you "
            "can specify here which field in the ShotGrid Project "
            "entity represents it."
        ),
        example="sg_code",
        scope=["studio"],
    )
    user_login_strip_email_domain: bool = SettingsField(
        default=False,
        title="Strip email domain from user logins",
        description=(
            "When syncing users, a ShotGrid login that is an email address "
            "('jane.doe@studio.com') becomes the AYON user 'jane.doe' "
            "instead of 'jane.doe_studio.com'. Users the sync already "
            "created with the old name are renamed on the next user sync."
        ),
        scope=["studio"],
    )
    enable_shotgrid_local_storage: bool = SettingsField(
        default=True,
        title="Enable ShotGrid Local Storage.",
        description=(
            "Whether to try make use of local storage defined in ShotGrid "
            "('Site Preferences -> File Management -> Local Storage') or not."
        ),
        scope=["studio"],
    )
    shotgrid_local_storage_key: str = SettingsField(
        default="primary",
        title="ShotGrid Local Storage entry name",
        description=(
            "Name of the 'code' to select which one of the multiple "
            "possible local storages entries to use."
        ),
        example="ayon_storage",
        scope=["studio"],
    )
    anatomy_preset: str = SettingsField(
        default="_",
        title="Anatomy Preset",
        description=(
            "The anatomy preset to use for the "
            "ShotGrid synchronized projects."
        ),
        enum_resolver=anatomy_presets_enum
    )
    compatibility_settings: ShotgridCompatibilitySettings = SettingsField(
        default_factory=ShotgridCompatibilitySettings,
        title="ShotGrid <-> AYON compatibility Settings",
        description=(
            "All the settings that allow us to fine-grain the relation "
            "between ShotGrid and AYON entities."
        )
    )
    service_settings: ShotgridServiceSettings = SettingsField(
        default_factory=ShotgridServiceSettings,
        title="Service settings",
        scope=["studio"],
    )
    publish: ShotgridPublishPlugins = SettingsField(
        default_factory=ShotgridPublishPlugins, title="Publish plugins"
    )
