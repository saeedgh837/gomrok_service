"""Deterministic record ids for cloud sync (spec §4).

Existing store ids are ``uuid4().hex[:8]`` — 32 bits, not unique enough once
several machines push into one host. Migration therefore *derives* the cloud id
instead of minting one, so a retry, an outbox replay or a second machine all
land on the same row:

    uuid5(NAMESPACE_URL, "<source_installation_id>/<entity>/<legacy_id>")

``source_installation_id`` is the UUID minted once on first bootstrap and kept
in ``cloud_config.json`` (spec §5).

Argument problems (blank or non-string parts, an unknown entity) raise plain
``ValueError``; record-level validation lives in ``cloud_sync.mapper``.
"""
import uuid

__all__ = ["ENTITIES", "legacy_to_uuid", "new_uuid"]

# The exact SQL table names of spec §3 — the entity vocabulary shared by the
# mapper, the HTTP client and the host schema. Do not add local-only stores
# (profiles.json, estelam_store.json, …) here: they never sync.
ENTITIES = (
    "customers",
    "customer_transactions",
    "owners",
    "identities",
    "crm_devices",
    "periods",
    "settings",
)


def legacy_to_uuid(source_installation_id, entity, legacy_id):
    """Map one legacy record id onto its stable 36-character cloud UUID.

    Pure and deterministic: the same triple always yields the same UUID, and a
    different ``source_installation_id`` always yields a different one — that
    is what keeps two installations' eight-hex ids from colliding on the host.
    """
    parts = {
        "source_installation_id": source_installation_id,
        "entity": entity,
        "legacy_id": legacy_id,
    }
    for name, value in parts.items():
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{name} must be a non-empty string, got {value!r}")
    if entity not in ENTITIES:
        raise ValueError(
            f"entity must be one of {', '.join(ENTITIES)}, got {entity!r}")
    name = f"{parts['source_installation_id']}/{entity}/{parts['legacy_id']}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def new_uuid():
    """A fresh uuid4 string — for client-side operation ids (spec §6.1)."""
    return str(uuid.uuid4())
