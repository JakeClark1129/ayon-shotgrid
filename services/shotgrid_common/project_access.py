"""Give AYON users access to the projects they are assigned to in ShotGrid.

ShotGrid lists the people working on a project in the Project 'users'
field. When enabled in the addon settings ('Project access sync'), each of
those users that is synced to AYON (has 'sg_user_id' in its data) gets
access groups on the matching AYON project, chosen from the user's
ShotGrid permission group.

Only access granted by this sync is ever changed or removed: what was
granted is remembered in the user's data under `GRANTED_DATA_KEY`, and a
project whose access groups were changed by hand in AYON is left alone.
Admins, managers and service users have access to every project and are
skipped.
"""
from typing import Any, Dict, List, Optional

import ayon_api

from utils import get_logger

log = get_logger(__file__)

# User data key holding {project name: access groups granted by this sync}.
GRANTED_DATA_KEY = "sg_project_access"


def get_project_access_settings(
    addon_settings: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Project access sync settings, or None when disabled."""
    access_settings = (addon_settings or {}).get("project_access_sync") or {}
    if not access_settings.get("enabled"):
        return None
    return access_settings


def get_access_groups_for_permission_group(
    permission_group: Optional[str],
    access_settings: Dict[str, Any],
) -> List[str]:
    """AYON access groups for a ShotGrid permission group name."""
    for mapping in access_settings.get("permission_group_mapping") or []:
        sg_name = (mapping.get("sg_permission_group") or "").strip().lower()
        if (
            sg_name
            and permission_group
            and sg_name == permission_group.strip().lower()
        ):
            return list(mapping.get("ayon_access_groups") or [])
    return list(access_settings.get("default_access_groups") or [])


def compute_access_change(
    project_name: str,
    user_data: Dict[str, Any],
    assigned: bool,
    desired_groups: List[str],
):
    """Work out the access change for one user on one project.

    Args:
        project_name (str): AYON project name.
        user_data (dict): The AYON user's data.
        assigned (bool): The user is in the ShotGrid project's 'users'.
        desired_groups (list[str]): Access groups the settings map the
            user's permission group to.

    Returns:
        tuple[Optional[list[str]], Optional[dict]]: New access groups for
            the project ([] removes access) or None to leave them, and the
            new granted map or None when it does not change.
    """
    granted = dict(user_data.get(GRANTED_DATA_KEY) or {})
    current = (user_data.get("accessGroups") or {}).get(project_name)
    previously_granted = granted.get(project_name)
    managed = (
        previously_granted is not None
        and sorted(current or []) == sorted(previously_granted)
    )

    if assigned:
        if not desired_groups:
            return None, None
        if not current:
            granted[project_name] = desired_groups
            return desired_groups, granted
        if managed and sorted(current) != sorted(desired_groups):
            granted[project_name] = desired_groups
            return desired_groups, granted
        if previously_granted is not None and not managed:
            # Changed by hand in AYON since: stop managing it.
            granted.pop(project_name)
            return None, granted
        return None, None

    if previously_granted is None:
        return None, None
    granted.pop(project_name)
    if managed:
        return [], granted
    # Changed by hand in AYON: keep the access, stop managing it.
    return None, granted


def sync_project_access(
    sg_session,
    sg_project: Dict[str, Any],
    project_name: str,
    addon_settings: Optional[Dict[str, Any]],
) -> Optional[Dict[str, int]]:
    """Grant or remove AYON project access from the ShotGrid project users.

    Args:
        sg_session (shotgun_api3.Shotgun): ShotGrid session.
        sg_project (dict): The ShotGrid project ('id' is required).
        project_name (str): The AYON project name.
        addon_settings (dict): ShotGrid addon settings.

    Returns:
        Optional[dict[str, int]]: Counts of 'granted', 'updated',
            'removed' and 'unknown' (ShotGrid users not in AYON), or None
            when the sync is disabled.
    """
    access_settings = get_project_access_settings(addon_settings)
    if not access_settings:
        return None

    sg_project_users = sg_session.find_one(
        "Project", [["id", "is", sg_project["id"]]], ["users"]
    ) or {}
    sg_user_ids = [
        user["id"]
        for user in sg_project_users.get("users") or []
        if user.get("type") == "HumanUser"
    ]
    permission_group_by_sg_id = {}
    if sg_user_ids:
        for sg_user in sg_session.find(
            "HumanUser",
            [["id", "in", sg_user_ids]],
            ["permission_rule_set"],
        ):
            rule_set = sg_user.get("permission_rule_set") or {}
            permission_group_by_sg_id[sg_user["id"]] = rule_set.get("name")

    counts = {"granted": 0, "updated": 0, "removed": 0, "unknown": 0}
    matched_sg_ids = set()

    for user_name in [
        user["name"] for user in ayon_api.get_users(fields={"name"})
    ]:
        ayon_user = ayon_api.get_user(user_name) or {}
        user_data = ayon_user.get("data") or {}
        sg_user_id = user_data.get("sg_user_id")
        if not sg_user_id:
            continue
        try:
            sg_user_id = int(sg_user_id)
        except (TypeError, ValueError):
            continue
        if (
            user_data.get("isAdmin")
            or user_data.get("isManager")
            or user_data.get("isService")
        ):
            # Have access to every project already.
            matched_sg_ids.add(sg_user_id)
            continue

        assigned = sg_user_id in permission_group_by_sg_id
        if assigned:
            matched_sg_ids.add(sg_user_id)
        desired_groups = get_access_groups_for_permission_group(
            permission_group_by_sg_id.get(sg_user_id), access_settings)

        new_groups, new_granted = compute_access_change(
            project_name, user_data, assigned, desired_groups)

        if new_groups is not None:
            had_access = bool(
                (user_data.get("accessGroups") or {}).get(project_name))
            response = ayon_api.patch(
                f"users/{user_name}/accessGroups",
                accessGroups=[
                    {"project": project_name, "accessGroups": new_groups}
                ],
            )
            response.raise_for_status()
            if not new_groups:
                counts["removed"] += 1
                log.info(f"Removed '{user_name}' from '{project_name}'.")
            elif had_access:
                counts["updated"] += 1
                log.info(f"Set '{user_name}' on '{project_name}' to "
                         f"{new_groups}.")
            else:
                counts["granted"] += 1
                log.info(f"Gave '{user_name}' {new_groups} on "
                         f"'{project_name}'.")

        if new_granted is not None:
            response = ayon_api.patch(
                f"users/{user_name}",
                data={GRANTED_DATA_KEY: new_granted},
            )
            response.raise_for_status()

    unknown = set(sg_user_ids) - matched_sg_ids
    counts["unknown"] = len(unknown)
    if unknown:
        log.warning(
            f"{len(unknown)} ShotGrid user(s) on '{project_name}' are not "
            f"synced to AYON (ShotGrid ids: {sorted(unknown)}). Run the "
            "user sync to create them."
        )
    log.info(f"Project access sync for '{project_name}': {counts}")
    return counts
