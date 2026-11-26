"""Folder hierarchy rules: folder type mapping, AssetCategory fields and
root relocation. Uses a minimal fake ShotGrid session, no server needed.
"""
import pytest

import utils
from constants import SHOTGRID_TYPE_ATTRIB


class FakeShotgrid:
    """Just enough of shotgun_api3.Shotgun for `get_sg_entities`."""

    def __init__(self, navchains, entities):
        self._navchains = navchains
        self._entities = entities

    def find_one(self, entity_type, filters, fields=None):
        return {
            "type": "Project",
            "id": 1,
            "name": "test",
            "code": "test",
            "tracking_settings": {"navchains": dict(self._navchains)},
        }

    def schema_entity_read(self, project_entity=None):
        return {
            entity_type: {"visible": {"value": True}}
            for entity_type in self._entities
        }

    def find(self, entity_type, filters=None, fields=None):
        return [dict(e) for e in self._entities.get(entity_type, [])]


def _entity(entity_type, entity_id, code, **fields):
    data = {
        "type": entity_type,
        "id": entity_id,
        "code": code,
        "project": {"type": "Project", "id": 1},
        "sg_ayon_id": None,
        "sg_ayon_sync_status": None,
    }
    data.update(fields)
    return data


PROJECT = {"type": "Project", "id": 1, "code": "test", "name": "test"}

MASTER_ASSET_SETTINGS = {
    "compatibility_settings": {
        "default_task_type": "Generic",
        "folder_type_mapping": [
            {"sg_entity_type": "CustomEntity04",
             "ayon_folder_type": "MasterAsset"},
        ],
        "asset_category_fields": [
            {"sg_entity_type": "Asset", "sg_field": "sg_asset_type",
             "skip_when_empty": False},
            {"sg_entity_type": "CustomEntity04", "sg_field": "sg_type",
             "skip_when_empty": True},
        ],
    }
}


def _get_entities(sg, settings, enabled):
    return utils.get_sg_entities(
        sg, PROJECT, enabled, "code", {}, settings)


def _parent_of(parents, key):
    return next(p for p, children in parents.items() if key in children)


def test_master_asset_hierarchy():
    master = _entity("CustomEntity04", 10, "KyleLaptop", sg_type="gfx")
    untyped = _entity("CustomEntity04", 11, "Untyped", sg_type=None)
    linked = _entity(
        "Asset", 20, "KyleLaptop_Screen01", sg_asset_type="screen",
        sg_asset_group={"type": "CustomEntity04", "id": 10})
    unlinked = _entity(
        "Asset", 21, "Loose", sg_asset_type="prop", sg_asset_group=None)
    sg = FakeShotgrid(
        navchains={"Asset": "sg_asset_group", "CustomEntity04": "sg_type"},
        entities={"CustomEntity04": [master, untyped],
                  "Asset": [linked, unlinked]},
    )

    dicts, parents = _get_entities(
        sg, MASTER_ASSET_SETTINGS, ["Project", "CustomEntity04", "Asset"])

    # Master Asset grouped by its own sg_type.
    assert _parent_of(parents, "CustomEntity04_10") == "gfx"
    assert dicts["gfx"]["folder_type"] == "AssetCategory"
    assert "gfx" in parents[1]
    # Linked Asset goes under its Master Asset, not under 'screen'.
    assert _parent_of(parents, "Asset_20") == "CustomEntity04_10"
    assert "screen" not in dicts
    # Unlinked Asset keeps the previous behaviour (sg_asset_type).
    assert _parent_of(parents, "Asset_21") == "prop"
    # Master Asset with an empty sg_type is skipped.
    assert "CustomEntity04_11" not in dicts
    # The SG type is kept on the dict; mapping is applied on creation.
    assert dicts["CustomEntity04_10"]["folder_type"] == "CustomEntity04"


def test_default_settings_keep_asset_category_behaviour():
    asset = _entity("Asset", 20, "Chair", sg_asset_type="prop")
    sg = FakeShotgrid(
        navchains={"Asset": "sg_asset_type"},
        entities={"Asset": [asset]},
    )
    # Settings without 'asset_category_fields' (older settings).
    settings = {"compatibility_settings": {"default_task_type": "Generic"}}

    _, parents = _get_entities(sg, settings, ["Project", "Asset"])

    assert _parent_of(parents, "Asset_20") == "prop"
    assert "prop" in parents[1]


def test_asset_linked_to_unsynced_entity_uses_category():
    asset = _entity(
        "Asset", 20, "Chair", sg_asset_type="prop",
        sg_asset_group={"type": "CustomEntity04", "id": 10})
    sg = FakeShotgrid(
        navchains={"Asset": "sg_asset_group"},
        entities={"Asset": [asset]},
    )
    settings = {"compatibility_settings": {"default_task_type": "Generic"}}

    # CustomEntity04 is not enabled, so it cannot be the parent.
    _, parents = _get_entities(sg, settings, ["Project", "Asset"])

    assert _parent_of(parents, "Asset_20") == "prop"


def test_folder_type_mapping():
    assert utils.get_ay_folder_type(
        "CustomEntity04", MASTER_ASSET_SETTINGS) == "MasterAsset"
    assert utils.get_ay_folder_type("Shot", MASTER_ASSET_SETTINGS) == "Shot"
    assert utils.get_sg_entity_type(
        "MasterAsset", MASTER_ASSET_SETTINGS) == "CustomEntity04"
    assert utils.get_sg_entity_type("Shot", MASTER_ASSET_SETTINGS) is None
    assert utils.get_ay_folder_type("Shot", {}) == "Shot"


class _Hub:
    def __init__(self):
        self.project_entity = object()


class _Folder:
    def __init__(self, folder_type):
        self.folder_type = folder_type


@pytest.mark.parametrize("mode, parent, expected", [
    ("root_relocate", "project", True),
    ("root_relocate", "Sequence", False),
    ("root_relocate", "AssetCategory", False),
    ("type_grouping", "Sequence", True),
    ("type_grouping", "AssetCategory", False),
])
def test_should_apply_reparenting(mode, parent, expected):
    hub = _Hub()
    natural_parent = (
        hub.project_entity if parent == "project" else _Folder(parent))
    settings = {"compatibility_settings": {"folder_parenting": {
        mode: {"enabled": True, "presets": []},
    }}}
    assert utils.should_apply_reparenting(
        hub, natural_parent, settings) is expected


def test_category_dict_shape():
    dicts, parents = {}, {}
    import collections
    parents = collections.defaultdict(set)
    key = utils._add_asset_category("Screen GFX", PROJECT, dicts, parents)
    assert key == "screen_gfx"
    assert dicts[key]["attribs"][SHOTGRID_TYPE_ATTRIB] == "AssetCategory"
    assert dicts[key]["label"] == "Screen GFX"
    assert key in parents[1]


class _FakeFolder:
    def __init__(self, name, folder_type, parent, folder_id):
        self.name = name
        self.folder_type = folder_type
        self.parent = parent
        self.id = folder_id
        self.children = []
        if parent is not None:
            parent.children.append(self)

    def get_children(self):
        return self.children

    @property
    def path(self):
        if self.parent is None:
            return ""
        return f"{self.parent.path}/{self.name}"


class _FakeHub:
    """Just enough of EntityHub for folder re-parenting."""

    def __init__(self):
        self.project_entity = _FakeFolder("project", None, None, "p")
        self._by_id = {"p": self.project_entity}

    def add_new_folder(self, folder_type, name, parent_id, **kwargs):
        folder = _FakeFolder(
            name, folder_type, self._by_id[parent_id], f"f{len(self._by_id)}")
        self._by_id[folder.id] = folder
        return folder


def _relocate_settings(*presets):
    return {"compatibility_settings": {"folder_parenting": {
        "root_relocate": {"enabled": True, "presets": [
            {"filter_by_sg_entity_type": sg_type,
             "parents": [{"folder_type": "Folder", "folder_name": root}]}
            for sg_type, root in presets
        ]},
        "type_grouping": {"enabled": False, "presets": []},
    }}}


def _relocated_path(hub, sg_ay_dict, settings):
    parent = utils.get_reparenting_from_settings(hub, sg_ay_dict, settings)
    return parent.path if parent else None


def test_category_follows_preset_of_grouped_entity_type():
    settings = _relocate_settings(
        ("CustomEntity04", "assets"), ("Asset", "loose_assets"))
    hub = _FakeHub()

    master_type = {"folder_type": "AssetCategory",
                   "category_of": "CustomEntity04"}
    asset_type = {"folder_type": "AssetCategory", "category_of": "Asset"}
    legacy = {"folder_type": "AssetCategory"}  # no category_of: Asset

    assert _relocated_path(hub, master_type, settings) == "/assets"
    assert _relocated_path(hub, asset_type, settings) == "/loose_assets"
    assert _relocated_path(hub, legacy, settings) == "/loose_assets"


def test_custom_entity_preset_like_sequence():
    # Without an AssetCategory field the Master Asset is top-level and is
    # moved by its own preset, the same way a Sequence is.
    settings = _relocate_settings(("CustomEntity04", "assets"))
    hub = _FakeHub()
    assert utils.is_reparentable_sg_type("CustomEntity04")
    assert utils.should_apply_reparenting(hub, hub.project_entity, settings)
    master = {"folder_type": "CustomEntity04"}
    assert _relocated_path(hub, master, settings) == "/assets"


def test_master_asset_category_records_its_entity_type():
    master = _entity("CustomEntity04", 10, "KyleLaptop", sg_type="gfx")
    sg = FakeShotgrid(
        navchains={"CustomEntity04": "sg_type"},
        entities={"CustomEntity04": [master]},
    )
    dicts, _ = _get_entities(
        sg, MASTER_ASSET_SETTINGS, ["Project", "CustomEntity04"])
    assert dicts["gfx"]["category_of"] == "CustomEntity04"


class _SchemaShotgrid:
    def __init__(self, schema):
        self.schema = schema
        self.reads = 0

    def schema_field_read(self, entity_type):
        self.reads += 1
        return self.schema


def test_calculated_fields_refresh_on_change():
    utils._SG_DERIVED_FIELDS_CACHE.clear()
    sg = _SchemaShotgrid({
        "sg_cut_in": {"data_type": {"value": "number"}},
        "sg_head_in": {"data_type": {"value": "number"}},
        "sg_handle_start": {"data_type": {"value": "calculated"}},
        "sg_handle_end": {"data_type": {"value": "calculated"}},
    })
    attribs_map = {
        "frameStart": "cut_in",
        "handleStart": "handle_start",
        "handleEnd": "handle_end",
        "description": "",
    }

    # Head In is not mapped, but handles may depend on it.
    assert utils.get_attribs_to_update_for_sg_change(
        sg, "Shot", "sg_head_in", attribs_map
    ) == ["handleStart", "handleEnd"]
    # Mapped field plus the calculated ones.
    assert utils.get_attribs_to_update_for_sg_change(
        sg, "Shot", "sg_cut_in", attribs_map
    ) == ["frameStart", "handleStart", "handleEnd"]
    # Schema read once, then cached.
    assert sg.reads == 1


def test_no_calculated_fields_keeps_previous_behaviour():
    utils._SG_DERIVED_FIELDS_CACHE.clear()
    sg = _SchemaShotgrid({"sg_cut_in": {"data_type": {"value": "number"}}})
    assert utils.get_attribs_to_update_for_sg_change(
        sg, "Shot", "sg_cut_in", {"frameStart": "cut_in", "x": "y"}
    ) == ["frameStart"]
    assert utils.get_attribs_to_update_for_sg_change(
        sg, "Shot", "description", {"frameStart": "cut_in"}
    ) == []


def test_non_editable_fields_count_as_derived():
    utils._SG_DERIVED_FIELDS_CACHE.clear()
    sg = _SchemaShotgrid({
        "sg_cut_in": {"data_type": {"value": "number"},
                      "editable": {"value": True}},
        "sg_handle_start": {"data_type": {"value": "number"},
                            "editable": {"value": False}},
    })
    assert utils.get_sg_derived_fields(sg, "Shot") == {"sg_handle_start"}


def _validate(schema, entries):
    import mock
    import validate

    class _Sg:
        def schema_field_read(self, entity_type):
            return schema

    with mock.patch.object(
        validate.ayon_api, "get_attributes_schema",
        return_value={"attributes": []},
    ):
        validate.validate_custom_attribs_map(_Sg(), entries)


def test_validation_accepts_read_only_and_calculated_fields():
    schema = {
        "sg_handle_start": {"data_type": {"value": "calculated"},
                            "editable": {"value": False}},
        "sg_handle_end": {"data_type": {"value": "number"},
                          "editable": {"value": False}},
    }
    _validate(schema, [
        {"ayon": "handleStart", "sg": "handle_start", "type": "integer",
         "scope": ["Shot"]},
        {"ayon": "handleEnd", "sg": "handle_end", "type": "integer",
         "scope": ["Shot"]},
    ])


def test_validation_still_rejects_wrong_types():
    schema = {"sg_cut_in": {"data_type": {"value": "list"},
                            "editable": {"value": True},
                            "properties": {}}}
    with pytest.raises(ValueError, match="invalid type"):
        _validate(schema, [{"ayon": "frameStart", "sg": "cut_in",
                            "type": "integer", "scope": ["Version"]}])


class _FieldShotgrid:
    """Fake Flow with a field schema and a few linked entities."""

    def __init__(self, field_schema, entities=None):
        self.field_schema = field_schema
        self.entities = entities or {}

    def schema_field_read(self, entity_type, field):
        if field not in self.field_schema:
            raise RuntimeError("no such field")
        return {field: self.field_schema[field]}

    def find_one(self, entity_type, filters, fields):
        field, _, value = filters[0]
        for entity in self.entities.get(entity_type, []):
            if field not in entity:
                raise RuntimeError("no such field")
            if entity[field].lower() == value.lower():
                return entity
        return None


def test_product_type_to_text_list_and_entity_fields():
    sg = _FieldShotgrid(
        {
            "sg_text": {"data_type": {"value": "text"}},
            "sg_version_type": {
                "data_type": {"value": "list"},
                "properties": {"valid_values": {"value": ["Comp", "Plate"]}},
            },
            "sg_output_type": {
                "data_type": {"value": "entity"},
                "properties": {
                    "valid_types": {"value": ["CustomNonProjectEntity14"]}},
            },
        },
        {"CustomNonProjectEntity14": [{"id": 7, "code": "comp"}]},
    )
    value = utils.get_sg_field_value_for_text
    assert value(sg, "Version", "sg_text", "comp") == "comp"
    assert value(sg, "Version", "sg_version_type", "comp") == "Comp"
    assert value(sg, "Version", "sg_version_type", "element") is None
    assert value(sg, "Version", "sg_output_type", "comp") == {
        "type": "CustomNonProjectEntity14", "id": 7}
    assert value(sg, "Version", "sg_output_type", "element") is None
    assert value(sg, "Version", "sg_missing", "comp") is None


def test_version_product_type_field_setting():
    assert utils.get_version_product_type_field({}) == "sg_version_type"
    assert utils.get_version_product_type_field({"compatibility_settings": {
        "version_product_type_field": "sg_output_type"}}) == "sg_output_type"
    assert utils.get_version_product_type_field({"compatibility_settings": {
        "version_product_type_field": ""}}) is None
