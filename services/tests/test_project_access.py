"""Project access sync: ShotGrid project users -> AYON access groups."""
import mock
import pytest

import project_access
from project_access import (
    GRANTED_DATA_KEY,
    compute_access_change,
    get_access_groups_for_permission_group,
)

PROJECT = "ADR001_Andersons"

SETTINGS = {
    "enabled": True,
    "default_access_groups": ["artist"],
    "permission_group_mapping": [
        {"sg_permission_group": "Manager",
         "ayon_access_groups": ["supervisor"]},
    ],
}


def test_permission_group_mapping():
    assert get_access_groups_for_permission_group(
        "Artist", SETTINGS) == ["artist"]
    assert get_access_groups_for_permission_group(
        "manager", SETTINGS) == ["supervisor"]
    assert get_access_groups_for_permission_group(
        None, SETTINGS) == ["artist"]


@pytest.mark.parametrize("data, assigned, expected_groups, expected_granted", [
    # Newly assigned, no access yet: grant and remember it.
    ({}, True, ["artist"], {PROJECT: ["artist"]}),
    # Assigned, access set by hand in AYON: leave it alone.
    ({"accessGroups": {PROJECT: ["lead"]}}, True, None, None),
    # Assigned, granted by the sync, mapping changed: update.
    ({"accessGroups": {PROJECT: ["artist"]},
      GRANTED_DATA_KEY: {PROJECT: ["artist"]}},
     True, ["supervisor"], {PROJECT: ["supervisor"]}),
    # Granted by the sync but edited by hand since: stop managing.
    ({"accessGroups": {PROJECT: ["lead"]},
      GRANTED_DATA_KEY: {PROJECT: ["artist"]}},
     True, None, {}),
    # Unassigned, granted by the sync: remove.
    ({"accessGroups": {PROJECT: ["artist"]},
      GRANTED_DATA_KEY: {PROJECT: ["artist"]}},
     False, [], {}),
    # Unassigned, access set by hand: keep.
    ({"accessGroups": {PROJECT: ["artist"]}}, False, None, None),
    # Unassigned, granted then edited by hand: keep, stop managing.
    ({"accessGroups": {PROJECT: ["lead"]},
      GRANTED_DATA_KEY: {PROJECT: ["artist"]}},
     False, None, {}),
])
def test_compute_access_change(data, assigned, expected_groups,
                               expected_granted):
    desired = ["supervisor"] if GRANTED_DATA_KEY in data else ["artist"]
    groups, granted = compute_access_change(PROJECT, data, assigned, desired)
    assert groups == expected_groups
    assert granted == expected_granted


def test_other_projects_are_untouched():
    data = {GRANTED_DATA_KEY: {"Other": ["artist"]},
            "accessGroups": {"Other": ["artist"]}}
    groups, granted = compute_access_change(PROJECT, data, True, ["artist"])
    assert groups == ["artist"]
    assert granted == {"Other": ["artist"], PROJECT: ["artist"]}


class FakeShotgrid:
    def __init__(self, project_users, permission_groups):
        self._project_users = project_users
        self._permission_groups = permission_groups

    def find_one(self, entity_type, filters, fields):
        return {"type": "Project", "id": 1, "users": [
            {"type": "HumanUser", "id": user_id}
            for user_id in self._project_users
        ]}

    def find(self, entity_type, filters, fields):
        return [
            {"type": "HumanUser", "id": user_id,
             "permission_rule_set": {"name": self._permission_groups[user_id]}}
            for user_id in filters[0][2]
        ]


def test_sync_project_access():
    users = {
        "jane": {"sg_user_id": 10},                       # new on project
        "boss": {"sg_user_id": 11},                       # Manager
        "gone": {"sg_user_id": 12,                        # removed from SG
                 "accessGroups": {PROJECT: ["artist"]},
                 GRANTED_DATA_KEY: {PROJECT: ["artist"]}},
        "admin": {"sg_user_id": 13, "isAdmin": True},     # skipped
        "local": {},                                      # not from SG
    }
    sg = FakeShotgrid([10, 11, 13, 99], {10: "Artist", 11: "Manager",
                                         13: "Admin", 99: "Artist"})
    calls = []

    def patch(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return mock.Mock()

    with mock.patch.object(project_access.ayon_api, "get_users",
                           return_value=[{"name": n} for n in users]), \
         mock.patch.object(project_access.ayon_api, "get_user",
                           side_effect=lambda n: {"name": n,
                                                  "data": users[n]}), \
         mock.patch.object(project_access.ayon_api, "patch",
                           side_effect=patch):
        counts = project_access.sync_project_access(
            sg, {"id": 1}, PROJECT,
            {"project_access_sync": SETTINGS})

    assert counts == {"granted": 2, "updated": 0, "removed": 1, "unknown": 1}
    access = {
        endpoint.split("/")[1]: kwargs["accessGroups"][0]["accessGroups"]
        for endpoint, kwargs in calls if endpoint.endswith("accessGroups")
    }
    assert access == {"jane": ["artist"], "boss": ["supervisor"],
                      "gone": []}
    granted = {
        endpoint.split("/")[1]: kwargs["data"][GRANTED_DATA_KEY]
        for endpoint, kwargs in calls if not endpoint.endswith("accessGroups")
    }
    assert granted == {"jane": {PROJECT: ["artist"]},
                       "boss": {PROJECT: ["supervisor"]},
                       "gone": {}}


def test_disabled_does_nothing():
    sg = mock.Mock()
    assert project_access.sync_project_access(
        sg, {"id": 1}, PROJECT, {"project_access_sync": {"enabled": False}}
    ) is None
    sg.find_one.assert_not_called()
