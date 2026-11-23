from typing import Any


def _convert_product_base_types(overrides: dict[str, Any]) -> None:
    profiles = (
        overrides
        .get("publish", {})
        .get("IntegrateMoviePath", {})
        .get("profiles")
    )
    if not profiles:
        return

    for profile in profiles:
        if "product_base_types" not in profile and "product_types" in profile:
            profile["product_base_types"] = profile.pop("product_types")


def _convert_frames_repre_names(overrides: dict[str, Any]) -> None:
    """'frames_repre_names' (short-lived) -> 'fields' mappings."""
    profiles = (
        overrides
        .get("publish", {})
        .get("IntegrateMoviePath", {})
        .get("profiles")
    )
    if not profiles:
        return

    for profile in profiles:
        frames_repre_names = profile.pop("frames_repre_names", None)
        if not frames_repre_names or profile.get("fields"):
            continue
        profile["fields"] = [
            {
                "sg_field": "sg_path_to_movie",
                "repre_names": list(profile.get("repre_names") or []),
                "sequences_only": False,
                "sequence_format": "hashes",
            },
            {
                "sg_field": "sg_path_to_frames",
                "repre_names": list(frames_repre_names),
                "sequences_only": True,
                "sequence_format": "hashes",
            },
        ]


async def convert_settings_overrides(
    source_version: str,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    _convert_product_base_types(overrides)
    _convert_frames_repre_names(overrides)
    return overrides
