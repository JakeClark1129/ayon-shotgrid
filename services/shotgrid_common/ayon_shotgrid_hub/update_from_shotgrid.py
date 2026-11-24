"""Module that handles creation, update or removal of AYON entities
based on ShotGrid Events.

The updates come through `meta` dictionaries such as:
"meta": {
    "id": 1274,
    "type": "entity_retirement",
    "entity_id": 1274,
    "class_name": "Shot",
    "entity_type": "Shot",
    "display_name": "bunny_099_012",
    "retirement_date": "2023-03-31 15:26:16 UTC"
}

And most of the times it fetches the ShotGrid entity as an AYON dict like:
{
    "label": label,
    "name": name,
    SHOTGRID_ID_ATTRIB: ShotGrid id,
    CUST_FIELD_CODE_ID: ayon id stored in ShotGrid,
    CUST_FIELD_CODE_SYNC: sync status stored in ShotGrid,
    "type": the entity type,
}

"""
import json
import collections

import shotgun_api3
import ayon_api

from ayon_api import slugify_string

from typing import Dict, List, Optional, Any

from utils import (
    create_new_ayon_entity,
    get_sg_entity_as_ay_dict,
    get_sg_entity_parent_field,
    get_reparenting_from_settings,
    get_asset_category_fields,
    get_asset_category_value,
    get_attribs_to_update_for_sg_change,
    should_apply_reparenting,
    is_reparentable_sg_type,
    update_ay_entity_custom_attributes,
    handle_comment,
    handle_reply,
)
from constants import (
    CUST_FIELD_CODE_ID,  # ShotGrid Field for the AYON ID.
    SHOTGRID_ID_ATTRIB,  # AYON Entity Attribute.
    SHOTGRID_TYPE_ATTRIB,  # AYON Entity Attribute.
    SHOTGRID_REMOVED_VALUE,  # Value for removed entities.
)

from utils import get_logger


log = get_logger(__file__)


def create_ay_entity_from_sg_event(
    sg_event: Dict,
    sg_project: Dict,
    sg_session: shotgun_api3.Shotgun,
    ayon_entity_hub: ayon_api.entity_hub.EntityHub,
    sg_enabled_entities: List[str],
    project_code_field: str,
    custom_attribs_map: Optional[Dict[str, str]] = None,
    addon_settings: Optional[Dict[str, str]] = None
):
    """Create an AYON entity from a ShotGrid Event.

    Args:
        sg_event (dict): The `meta` key from a ShotGrid Event.
        sg_project (dict): The ShotGrid project.
        sg_session (shotgun_api3.Shotgun): The ShotGrid API session.
        ayon_entity_hub (ayon_api.entity_hub.EntityHub): The AYON EntityHub.
        sg_enabled_entities (list[str]): List of entity strings enabled.
        project_code_field (str): The Shotgrid project code field.
        custom_attribs_map (Optional[dict]): A dictionary that maps ShotGrid
            attributes to Ayon attributes.
        addon_settings (Optional[dict]): A dictionary of Settings

    Returns:
        ay_entity (ayon_api.entity_hub.EntityHub.Entity): The newly
            created entity.
    """
    default_task_type = addon_settings[
        "compatibility_settings"]["default_task_type"]
    sg_parent_field = get_sg_entity_parent_field(
        sg_session,
        sg_project,
        sg_event["entity_type"],
        sg_enabled_entities,
    )

    category_fields = get_asset_category_fields(addon_settings)
    extra_fields = _get_parent_query_fields(
        sg_event["entity_type"], sg_parent_field, category_fields)

    sg_ay_dict = get_sg_entity_as_ay_dict(
        sg_session,
        sg_event["entity_type"],
        sg_event["entity_id"],
        project_code_field,
        default_task_type,
        custom_attribs_map=custom_attribs_map,
        extra_fields=extra_fields,
    )

    log.debug(f"ShotGrid Entity as AYON dict: {sg_ay_dict}")
    if not sg_ay_dict:
        log.warning(
            f"Entity {sg_event['entity_type']} <{sg_event['entity_id']}> "
            "no longer exists in ShotGrid, aborting..."
        )
        return

    if sg_ay_dict["type"].lower() == "comment":
        if sg_ay_dict["attribs"]["shotgridType"] == "Note":
            handle_comment(
                sg_ay_dict,
                sg_session,
                ayon_entity_hub,
            )
            return
        if sg_ay_dict["attribs"]["shotgridType"] == "Reply":
            handle_reply(
                sg_ay_dict,
                sg_session,
                ayon_entity_hub,
            )
            return

    ayon_id_stored_in_sg = sg_ay_dict["data"].get(CUST_FIELD_CODE_ID)
    if ayon_id_stored_in_sg:
        # Revived entity, check if it's still in the Server
        ay_entity = ayon_entity_hub.get_or_query_entity_by_id(
            ayon_id_stored_in_sg,
            [sg_ay_dict["type"]]
        )

        if ay_entity:
            log.debug("ShotGrid Entity exists in AYON.")
            # Ensure AYON Entity has the correct ShotGrid ID
            ay_entity = _update_sg_id(
                ay_entity,
                custom_attribs_map,
                sg_ay_dict,
                ayon_entity_hub.project_entity
            )

            return ay_entity

    ay_parent_entity = None
    items_to_create = collections.deque()
    try:
        while ay_parent_entity is None:
            items_to_create.append(sg_ay_dict)
            ay_parent_entity = _get_ayon_parent_entity(
                ayon_entity_hub,
                project_code_field,
                sg_ay_dict,
                sg_parent_field,
                sg_project,
                sg_session,
                addon_settings,
                sg_enabled_entities=sg_enabled_entities,
            )

            sg_parent = sg_ay_dict["data"].get(sg_parent_field)
            if not ay_parent_entity and not sg_parent:
                ay_parent_entity = ayon_entity_hub.project_entity

            if not ay_parent_entity:
                # The linked parent is not in AYON yet: create it first.
                parent_type = sg_parent["type"]
                parent_parent_field = get_sg_entity_parent_field(
                    sg_session,
                    sg_project,
                    parent_type,
                    sg_enabled_entities,
                )
                sg_ay_dict = get_sg_entity_as_ay_dict(
                    sg_session,
                    parent_type,
                    sg_parent["id"],
                    project_code_field,
                    default_task_type,
                    custom_attribs_map=custom_attribs_map,
                    extra_fields=_get_parent_query_fields(
                        parent_type, parent_parent_field, category_fields),
                )
                sg_parent_field = parent_parent_field
    except SkipSgEntity as exc:
        log.warning(f"Not creating {sg_event['entity_type']} "
                    f"<{sg_event['entity_id']}> in AYON yet: {exc}")
        return None

    while items_to_create:
        sg_ay_dict = items_to_create.pop()

        shotgrid_type = sg_ay_dict["attribs"][SHOTGRID_TYPE_ATTRIB]
        sg_parent_field = get_sg_entity_parent_field(
            sg_session,
            sg_project,
            shotgrid_type,
            sg_enabled_entities,
        )
        ay_parent_entity = _get_ayon_parent_entity(
            ayon_entity_hub,
            project_code_field,
            sg_ay_dict,
            sg_parent_field,
            sg_project,
            sg_session,
            addon_settings,
            sg_enabled_entities=sg_enabled_entities,
        )

        ay_entity = create_new_ayon_entity(
            sg_session,
            ayon_entity_hub,
            ay_parent_entity,
            sg_ay_dict,
            addon_settings=addon_settings,
        )

    return ay_entity


def sync_ay_entity_list_from_sg_event(
    sg_event_meta: Dict,
    sg_project: Dict,
    sg_session: shotgun_api3.Shotgun,
):
    # in sg all playlists are supported
    # get sg playlist and all linked versions
    playlist = sg_session.find_one(
        "Playlist",
        [["id", "is", sg_event_meta["entity_id"]]],
        ["id", "code", "versions", "sg_ayon_id", "locked"]
    )

    ay_version_items = []
    if playlist:
        # get ayon_id for all sg versions in a single batched query
        version_ids = [v["id"] for v in playlist.get("versions", [])]
        if version_ids:
            for sg_version in sg_session.find(
                "Version",
                [["id", "in", version_ids]],
                ["sg_ayon_id"]
            ):
                if sg_version.get("sg_ayon_id"):
                    ay_version_items.append({"entityId": sg_version["sg_ayon_id"]})

    match sg_event_meta["type"]:
        case "new_entity":
            rest_payload = {
                "project_name": sg_project["name"],
                "entity_type": "version",
                "label": playlist["code"],
            }
            if ay_version_items:
                rest_payload["items"] = ay_version_items

            log.info(f"Creating AYON EntityList for SG Playlist: {playlist['id']}")
            entity_list = ayon_api.raw_post(
                f"/projects/{sg_project['name']}/lists",
                json={
                    "project_name": sg_project["name"],
                    "entity_type": "version",
                    "label": playlist["code"],
                    "attrib": {"sg_id": playlist["id"]},
                    "items": ay_version_items,
                }
            )
            # save back ayon id on sg playlist
            sg_session.update(
                "Playlist",
                playlist["id"],
                {
                    "sg_ayon_id": entity_list["id"]
                }
            )

        case "attribute_change":
            if not playlist:
                log.info("SG Playlist was deleted. Skipping update.")
                return
            if sg_event_meta["attribute_name"] not in ["versions", "locked", "code"]:
                log.info(
                    "SG event not of a supported attribute type. Skipping update."
                )
                return

            log.info(f"Updating AYON EntityList: {playlist['sg_ayon_id']}")
            log.info(f"\t- version = {ay_version_items}")
            log.info(f"\t- label = {playlist['code']}")
            log.info(f"\t- active = {not playlist.get('locked', False)}")
            ayon_api.raw_patch(    # doesn't respect 'active' attribute or support adding versions
                f"/projects/{sg_project['name']}/lists/{playlist['sg_ayon_id']}",
                json={
                    "project_name": sg_project["name"],
                    "entity_type": "version",
                    "label": playlist["code"],
                    "attrib": {"sg_id": playlist["id"]},
                }
            )
            if ay_version_items:
                ayon_api.raw_patch(
                    f"/projects/{sg_project['name']}/lists/{playlist['sg_ayon_id']}/items",
                    json={"items": ay_version_items, "mode": "replace"},
                )

            # Empty list, delete all items.
            # raw_patch or items=[] and mode="replace" does not clean the list.
            else:
                current = ayon_api.get_entity_list_rest(sg_project["name"], playlist["sg_ayon_id"])
                for item in current.get("items", []):
                    ayon_api.delete_entity_list_item(sg_project["name"], playlist["sg_ayon_id"], item["id"])

            ayon_api.update_entity_list(    # and update active attrib here
                project_name=sg_project["name"],
                list_id=playlist["sg_ayon_id"],
                active=not playlist.get("locked", False),
                label=playlist["code"],
            )
        case "entity_retirement":
            ay_entity_lists = ayon_api.get_entity_lists(
                sg_project["name"], fields=["id", "label", "allAttrib"])
            sg_playlist_id = str(sg_event_meta["entity_id"])

            ay_list_id_to_delete = None
            for ay_entity_list in ay_entity_lists:
                log.debug(f"{ay_entity_list = }")
                all_attrib = ay_entity_list.get("allAttrib")
                if isinstance(all_attrib, str):
                    all_attrib = json.loads(all_attrib)
                if all_attrib is None:
                    all_attrib = {}

                if int(all_attrib.get("sg_id", -1)) == int(sg_playlist_id):
                    ay_list_id_to_delete = ay_entity_list["id"]

            if ay_list_id_to_delete:
                log.info(f"Deleting AYON EntityList: {ay_list_id_to_delete}")
                ayon_api.delete_entity_list(
                    sg_project["name"],
                    ay_list_id_to_delete
                )
            else:
                log.info("No matching AYON EntityList found for deletion.")


class SkipSgEntity(Exception):
    """The ShotGrid entity should not be created in AYON (yet)."""


def _get_parent_query_fields(sg_type, sg_parent_field, category_fields):
    """Extra fields to query to work out where `sg_type` goes in AYON."""
    fields = []
    if sg_parent_field and sg_parent_field != "project":
        fields.append(sg_parent_field)
    category_field = category_fields.get(sg_type)
    if category_field and category_field["sg_field"] not in fields:
        fields.append(category_field["sg_field"])
    return fields


def _get_or_create_asset_category(
    ayon_entity_hub, sg_session, category, addon_settings,
    category_of="Asset",
):
    """Find or create the AssetCategory folder for `category`.

    `category_of` is the ShotGrid entity type being grouped; its folder
    re-parenting preset decides where the category goes.
    """
    category_parent = ayon_entity_hub.project_entity
    if should_apply_reparenting(
        ayon_entity_hub, category_parent, addon_settings
    ):
        category_parent = get_reparenting_from_settings(
            ayon_entity_hub,
            {"folder_type": "AssetCategory", "category_of": category_of},
            addon_settings,
        ) or category_parent

    name = slugify_string(category).lower()
    for child in category_parent.children:
        if (
            child.folder_type == "AssetCategory"
            and child.name.lower() == name
        ):
            return child

    return create_new_ayon_entity(
        sg_session,
        ayon_entity_hub,
        category_parent,
        {
            "folder_type": "AssetCategory",
            "type": "AssetCategory",
            "name": name,
            "label": category,
            "data": {CUST_FIELD_CODE_ID: name},
            "attribs": {
                SHOTGRID_ID_ATTRIB: name,
                SHOTGRID_TYPE_ATTRIB: "AssetCategory"
            }
        },
        addon_settings=addon_settings,
    )


def _get_ayon_parent_entity(
    ayon_entity_hub,
    project_code_field,
    sg_ay_dict,
    sg_parent_field,
    sg_project,
    sg_session,
    addon_settings,
    sg_enabled_entities=None,
):
    """Tries to find parent entity in AYON

    Mirrors `get_sg_entities` used by the full project sync:

    1. Linked to a ShotGrid parent entity (Tracking Settings) -> that
       entity's AYON folder, or None when it is not in AYON yet so the
       caller creates it first.
    2. Otherwise, an 'AssetCategory fields' entry for the entity type ->
       an AssetCategory folder named after the field value.
    3. Otherwise the project.

    Folder re-parenting settings are applied to whatever would otherwise
    sit directly under the project.

    Args:
        ayon_entity_hub (ayon_api.entity_hub.EntityHub): The AYON EntityHub.
        project_code_field (str): The Shotgrid project code field.
        sg_ay_dict (dict): The ShotGrid entity ready for AYON consumption.:
        sg_parent_field (str): 'project'|'sequence'
        sg_project (dict): The ShotGrid project.
        sg_session (shotgun_api3.Shotgun): The ShotGrid API session.
        addon_settings (Optional[dict]): A dictionary of Settings. Used to
            query location of custom folders (`shots`, `sequences`)
        sg_enabled_entities (Optional[list[str]]): Synced entity types.

    Raises:
        SkipSgEntity: The entity's AssetCategory field is empty and the
            settings say to skip it until it is filled in.

    Returns:
        ay_entity (ayon_api.entity_hub.EntityHub.Entity):
            FolderEntity|ProjectEntity
    """
    default_task_type = addon_settings[
        "compatibility_settings"]["default_task_type"]

    shotgrid_type = sg_ay_dict["attribs"][SHOTGRID_TYPE_ATTRIB]
    sg_parent = sg_ay_dict["data"].get(sg_parent_field)
    category_field = get_asset_category_fields(
        addon_settings).get(shotgrid_type)

    linked_parent = (
        sg_parent
        if isinstance(sg_parent, dict) and sg_parent.get("id")
        else None
    )
    if linked_parent and (
        not category_field
        or sg_enabled_entities is None
        or linked_parent["type"] in sg_enabled_entities
    ):
        # Find parent entity ID
        sg_parent_entity_dict = get_sg_entity_as_ay_dict(
            sg_session,
            linked_parent["type"],
            linked_parent["id"],
            project_code_field,
            default_task_type,
        )

        log.debug(f"ShotGrid Parent entity: {sg_parent_entity_dict}")
        return ayon_entity_hub.get_or_query_entity_by_id(
            sg_parent_entity_dict["data"].get(CUST_FIELD_CODE_ID),
            [
                (
                    "task"
                    if sg_parent_entity_dict["type"] == "task"
                    else "folder"
                )
            ],
        )

    if category_field:
        category = get_asset_category_value(
            sg_ay_dict["data"].get(category_field["sg_field"]))
        if category:
            return _get_or_create_asset_category(
                ayon_entity_hub, sg_session, category, addon_settings,
                category_of=shotgrid_type)
        if category_field["skip_when_empty"]:
            raise SkipSgEntity(
                f"field '{category_field['sg_field']}' is empty")

    # Parent is the project, unless re-parenting settings move it.
    log.debug(f"ShotGrid Parent is the Project: {sg_project}")
    ay_parent_entity = ayon_entity_hub.project_entity
    if (
        is_reparentable_sg_type(shotgrid_type)
        and should_apply_reparenting(
            ayon_entity_hub, ay_parent_entity, addon_settings)
    ):
        ay_parent_entity = get_reparenting_from_settings(
            ayon_entity_hub,
            sg_ay_dict,
            addon_settings
        ) or ay_parent_entity
    return ay_parent_entity


def _update_sg_id(ay_entity, custom_attribs_map, sg_ay_dict, project_entity):
    ayon_entity_sg_id = str(
        ay_entity.attribs.get_attribute(SHOTGRID_ID_ATTRIB).value)
    # Ensure AYON Entity has the correct Shotgrid ID
    ay_shotgrid_id = str(
        sg_ay_dict["attribs"].get(SHOTGRID_ID_ATTRIB, ""))
    if ayon_entity_sg_id != ay_shotgrid_id:
        ay_entity.attribs.set(
            SHOTGRID_ID_ATTRIB,
            ay_shotgrid_id
        )
        ay_entity.attribs.set(
            SHOTGRID_TYPE_ATTRIB,
            sg_ay_dict["type"]
        )
    update_ay_entity_custom_attributes(
        ay_entity, sg_ay_dict, custom_attribs_map, ay_project=project_entity
    )

    return ay_entity


def update_ayon_entity_from_sg_event(
    sg_event: Dict,
    sg_project: Dict,
    sg_session: shotgun_api3.Shotgun,
    ayon_entity_hub: ayon_api.entity_hub.EntityHub,
    sg_enabled_entities: List[str],
    project_code_field: str,
    addon_settings: Dict[str, Any],
    custom_attribs_map: Optional[Dict[str, str]] = None,
):
    """Try to update an entity in AYON.

    Args:
        sg_event (dict): The `meta` key from a ShotGrid Event.
        sg_project (dict): The ShotGrid project.
        sg_session (shotgun_api3.Shotgun): The ShotGrid API session.
        ayon_entity_hub (ayon_api.entity_hub.EntityHub): The AYON EntityHub.
        sg_enabled_entities (list[str]): List of entity strings enabled.
        project_code_field (str): The ShotGrid project code field.
        addon_settings (dict): A dictionary of Settings.
        custom_attribs_map (dict): A dictionary that maps ShotGrid
            attributes to AYON attributes.

    Returns:
        ay_entity (ayon_api.entity_hub.EntityHub.Entity): The modified entity.

    """
    default_task_type = addon_settings[
        "compatibility_settings"]["default_task_type"]

    sg_ay_dict = get_sg_entity_as_ay_dict(
        sg_session,
        sg_event["entity_type"],
        sg_event["entity_id"],
        project_code_field,
        default_task_type,
        custom_attribs_map=custom_attribs_map
    )

    if not sg_ay_dict:
        log.warning(
            f"Entity {sg_event['entity_type']} <{sg_event['entity_id']}> "
            "no longer exists in ShotGrid, aborting..."
        )
        return

    if sg_ay_dict["type"].lower() == "comment":
        if sg_ay_dict["attribs"]["shotgridType"] == "Note":
            handle_comment(
                sg_ay_dict,
                sg_session,
                ayon_entity_hub,
            )
        else:
            handle_reply(
                sg_ay_dict,
                sg_session,
                ayon_entity_hub,
            )
        return

    # if the entity does not have an AYON ID, try to create it
    # and no need to update
    if not sg_ay_dict["data"].get(CUST_FIELD_CODE_ID):
        log.debug(f"Creating AYON Entity: {sg_ay_dict}")
        try:
            create_ay_entity_from_sg_event(
                sg_event,
                sg_project,
                sg_session,
                ayon_entity_hub,
                sg_enabled_entities,
                project_code_field,
                custom_attribs_map
            )
        except Exception:
            log.debug("AYON Entity could not be created", exc_info=True)
        return

    ay_entity = ayon_entity_hub.get_or_query_entity_by_id(
        sg_ay_dict["data"].get(CUST_FIELD_CODE_ID),
        [sg_ay_dict["type"]]
    )

    if not ay_entity:
        raise ValueError("Unable to update a non existing entity.")

    # Ensure AYON Entity has the correct ShotGrid ID
    ayon_entity_sg_id = str(
        ay_entity.attribs.get(SHOTGRID_ID_ATTRIB, "")
    )
    sg_entity_sg_id = str(
        sg_ay_dict["attribs"].get(SHOTGRID_ID_ATTRIB, "")
    )


    # We need to check for existence in `ayon_entity_sg_id` as it could be
    # that it's a new entity and it doesn't have a ShotGrid ID yet.
    if ayon_entity_sg_id and ayon_entity_sg_id != sg_entity_sg_id:
        log.error("Mismatching ShotGrid IDs, aborting...")
        raise ValueError("Mismatching ShotGrid IDs, aborting...")

    # Update entity label.
    if ay_entity.entity_type != "version":
        log.debug(f"Updating AYON Entity: {ay_entity.name}")
    else:
        log.debug(f"Updating AYON Entity: {ay_entity}")

    # Update the AYON attribute matching the SG field, and attributes
    # mapped to calculated fields that may depend on it.
    values_to_update = get_attribs_to_update_for_sg_change(
        sg_session,
        sg_ay_dict["attribs"][SHOTGRID_TYPE_ATTRIB],
        sg_event.get("attribute_name"),
        custom_attribs_map,
    )

    update_ay_entity_custom_attributes(
        ay_entity,
        sg_ay_dict,
        custom_attribs_map,
        values_to_update=values_to_update,
        ay_project=ayon_entity_hub.project_entity
    )

    ayon_entity_hub.commit_changes()

    if sg_ay_dict["data"].get(CUST_FIELD_CODE_ID) != ay_entity.id:
        sg_session.update(
            sg_ay_dict["attribs"][SHOTGRID_TYPE_ATTRIB],
            sg_ay_dict["attribs"][SHOTGRID_ID_ATTRIB],
            {
                CUST_FIELD_CODE_ID: ay_entity.id
            }
        )

    ay_entity.attribs.set(
        SHOTGRID_ID_ATTRIB,
        sg_ay_dict["attribs"].get(SHOTGRID_ID_ATTRIB, "")
    )
    ay_entity.attribs.set(
        SHOTGRID_TYPE_ATTRIB,
        sg_ay_dict["attribs"].get(SHOTGRID_TYPE_ATTRIB, "")
    )

    return ay_entity


def remove_ayon_entity_from_sg_event(
    sg_event: Dict,
    sg_session: shotgun_api3.Shotgun,
    ayon_entity_hub: ayon_api.entity_hub.EntityHub,
    project_code_field: str,
    addon_settings: Dict[str, Any],
):
    """Try to remove an entity in AYON.

    Args:
        sg_event (dict): The `meta` key from a ShotGrid Event.
        sg_session (shotgun_api3.Shotgun): The ShotGrid API session.
        ayon_entity_hub (ayon_api.entity_hub.EntityHub): The AYON EntityHub.
        project_code_field (str): The ShotGrid field that contains the AYON ID.
        addon_settings (dict): A dictionary of Settings.
    """
    default_task_type = addon_settings[
        "compatibility_settings"]["default_task_type"]

    sg_ay_dict = get_sg_entity_as_ay_dict(
        sg_session,
        sg_event["entity_type"],
        sg_event["entity_id"],
        project_code_field,
        default_task_type,
        retired_only=True
    )

    if not sg_ay_dict:
        sg_ay_dict = get_sg_entity_as_ay_dict(
            sg_session,
            sg_event["entity_type"],
            sg_event["entity_id"],
            project_code_field,
            default_task_type,
            retired_only=False,
        )
        if sg_ay_dict:
            log.info(
                f"No need to remove entity {sg_event['entity_type']} "
                f"<{sg_event['entity_id']}>, it's not retired anymore."
            )
            return
        else:
            log.warning(
                f"Entity {sg_event['entity_type']} <{sg_event['entity_id']}> "
                "no longer exists in ShotGrid."
            )

    if not sg_ay_dict["data"].get(CUST_FIELD_CODE_ID):
        log.warning(
            "Entity does not have an AYON ID, aborting..."
        )
        return

    ay_entity = ayon_entity_hub.get_or_query_entity_by_id(
        sg_ay_dict["data"].get(CUST_FIELD_CODE_ID),
        ["task" if sg_ay_dict.get("type").lower() == "task" else "folder"]
    )

    if not ay_entity:
        raise ValueError("Unable to update a non existing entity.")

    if sg_ay_dict["data"].get(CUST_FIELD_CODE_ID) != ay_entity.id:
        raise ValueError("Mismatching ShotGrid IDs, aborting...")

    if not ay_entity.immutable_for_hierarchy:
        log.info(f"Deleting AYON entity: {ay_entity}")
        ayon_entity_hub.delete_entity(ay_entity)
    else:
        log.info("Entity is immutable.")
        ay_entity.attribs.set(SHOTGRID_ID_ATTRIB, SHOTGRID_REMOVED_VALUE)

    ayon_entity_hub.commit_changes()
