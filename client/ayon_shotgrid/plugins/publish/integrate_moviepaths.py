import os
import re
from typing import Dict, Any, List, Optional

import ayon_api
import pyblish.api

from ayon_core.lib import filter_profiles


class IntegrateMoviePath(pyblish.api.InstancePlugin):
    """Store paths of published representations on the Flow (SG) Version.

    Each settings profile maps Flow Version fields (e.g. 'sg_path_to_movie',
    'sg_path_to_frames' or any custom text field) to the published
    representation whose path should be written there.

    Profiles without field mappings keep the previous behaviour: one
    representation fills 'sg_path_to_movie' (and 'sg_path_to_frames' when it
    is an image sequence), falling back to the thumbnail.

    Dispatches event to update synchronized Version paths to limit race
    conditions.

    Must be called after full `Integrate` when both Version and Representations
    are present in DB.
    """

    order = pyblish.api.IntegratorOrder + 0.45
    label = "Integrate event for Flow movie paths"
    settings_category = "shotgrid"

    profiles = []

    def process(self, instance: pyblish.api.Instance):
        product_base_type = instance.data.get("productBaseType")
        if not product_base_type:
            product_base_type = instance.data["productType"]

        if instance.data.get("farm"):
            self.log.debug(
                f"`{product_base_type}` should be processed on farm,"
                f" skipping."
            )
            return

        published_representations = instance.data.get(
            "published_representations"
        )
        if not published_representations:
            self.log.debug("Instance does not have published representations")
            return

        representations = [
            repre_info["representation"]
            for repre_info in published_representations.values()
        ]
        profile = self._get_representation_profile(instance)
        version_entity = instance.data["versionEntity"]
        has_slate = "slate" in version_entity["attrib"]["families"]

        field_mappings = (profile or {}).get("fields") or []
        if field_mappings:
            flow_data = self._get_mapped_fields(
                representations, field_mappings)
            if flow_data and has_slate:
                flow_data["sg_frames_have_slate"] = True
        else:
            flow_data = self._get_legacy_fields(
                representations,
                (profile or {}).get("repre_names") or [],
                has_slate,
            )

        if not flow_data:
            return

        self._trigger_event(instance, flow_data)

    def _get_representation_profile(self, instance: pyblish.api.Instance):
        host_name = instance.context.data["hostName"]
        product_base_type = instance.data.get("productBaseType")
        if not product_base_type:
            product_base_type = instance.data["productType"]
        task_name = None
        task_type = None
        task_entity = instance.data.get("taskEntity")
        if task_entity:
            task_type = task_entity["taskType"]
            task_name = task_entity["name"]

        profile = filter_profiles(
            self.profiles,
            {
                "host_names": host_name,
                "product_base_types": product_base_type,
                "task_names": task_name,
                "task_types": task_type,
            },
            logger=self.log,
        )
        return profile

    @staticmethod
    def _is_sequence(representation: Dict[str, Any]) -> bool:
        return len(representation.get("files") or []) > 1

    def _find_representation(
        self,
        representations: List[Dict[str, Any]],
        repre_names: List[str],
        sequences_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """First published representation found from `repre_names`."""
        by_name = {repre["name"]: repre for repre in representations}
        for repre_name in repre_names:
            representation = by_name.get(repre_name)
            if not representation:
                continue
            if sequences_only and not self._is_sequence(representation):
                self.log.debug(f"`{repre_name}` is not an image sequence")
                continue
            return representation
        return None

    def _get_path(
        self,
        representation: Dict[str, Any],
        sequence_format: str = "hashes",
    ) -> str:
        """Local path of a representation.

        Image sequences have their frame number replaced by '####'
        ('hashes') or '%04d' ('printf').
        """
        local_path = os.path.normpath(representation["attrib"]["path"])
        if not self._is_sequence(representation):
            return local_path

        padding = 0
        match = re.search(r"\.(\d+)\.", local_path)
        if match:
            padding = len(match.group(1))
        if sequence_format == "printf":
            token = f"%0{padding}d" if padding else "%d"
        else:
            token = "#" * padding
        return re.sub(r"\.\d+\.", lambda _: f".{token}.", local_path)

    def _get_mapped_fields(
        self,
        representations: List[Dict[str, Any]],
        field_mappings: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Flow Version field values from the profile's field mappings."""
        flow_data = {}
        for mapping in field_mappings:
            sg_field = mapping.get("sg_field")
            if not sg_field:
                continue
            representation = self._find_representation(
                representations,
                mapping.get("repre_names") or [],
                sequences_only=mapping.get("sequences_only", False),
            )
            if not representation:
                self.log.debug(
                    f"No representation found for Flow field `{sg_field}`")
                continue
            flow_data[sg_field] = self._get_path(
                representation, mapping.get("sequence_format", "hashes"))
            self.log.debug(
                f"Using `{representation['name']}` for `{sg_field}`")
        return flow_data

    def _get_legacy_fields(
        self,
        representations: List[Dict[str, Any]],
        repre_names: List[str],
        has_slate: bool,
    ) -> Dict[str, Any]:
        """Previous behaviour for profiles without field mappings.

        We are storing local paths for external processing, some studios might
        have tools to handle review files in another processes.
        """
        flow_data = {}
        representation = self._find_representation(
            representations, repre_names)
        if representation:
            path = self._get_path(representation)
            flow_data["sg_path_to_movie"] = path
            if self._is_sequence(representation):
                flow_data["sg_path_to_frames"] = path
            if has_slate:
                flow_data["sg_frames_have_slate"] = True
            return flow_data

        thumbnail = self._find_representation(representations, ["thumbnail"])
        if thumbnail:
            thumbnail_path = self._get_path(thumbnail)
            flow_data.update({
                "sg_path_to_movie": thumbnail_path,
                "sg_path_to_frames": thumbnail_path,
            })
        return flow_data

    def _trigger_event(
        self,
        instance: pyblish.api.Instance,
        flow_data: Dict[str, Any]
    ):
        """Triggers event to update media path on Flow(SG) Version

        Temporarily via addon server endpoint to mitigate bug in
        enroll_event_job.ignore_sender_types. When resolved could be changed
        to simple dispatch_event.
        """

        project_name = instance.context.data["projectName"]
        version_id = instance.data["versionEntity"]["id"]
        flow_data["versionId"] = version_id

        self.log.debug(f"Sending event for {version_id} with {flow_data}")

        addon = instance.context.data["ayonAddonsManager"]["shotgrid"]

        endpoint = addon.get_server_addon_endpoint(
            project_name, "trigger_mediapath"
        )
        response = ayon_api.post(
            endpoint,
            **flow_data,
        )
        response.raise_for_status("Cannot trigger update of media paths.")
