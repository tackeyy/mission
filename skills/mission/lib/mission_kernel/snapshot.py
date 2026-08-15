"""Paired MissionState and GuidanceFacts decoding with recombination binding."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from .codec_v4 import _decode_v4_object
from .guidance import GuidanceFacts, decode_legacy_guidance, decode_v5_guidance
from .json_codec import decode_json_object, thaw_json_object
from .model import MissionState, SchemaOrigin, SnapshotProvenance
from .versions import read_schema_version


@dataclass(frozen=True)
class Snapshot:
    state: MissionState
    guidance: GuidanceFacts
    provenance: SnapshotProvenance

    def __post_init__(self) -> None:
        if (
            self.state.snapshot_provenance != self.provenance
            or self.guidance.provenance != self.provenance
            or self.state.identity.session_id != self.provenance.session_id
            or self.state._snapshot_binding is None
            or self.state._snapshot_binding is not self.guidance._snapshot_binding
        ):
            raise ValueError("snapshot-provenance-mismatch")


def decode_snapshot(source: bytes) -> Snapshot:
    frozen = decode_json_object(source)
    document = thaw_json_object(frozen)
    schema_origin = read_schema_version(document, max_reader_version=5)
    if schema_origin is SchemaOrigin.V5:
        from .codec_v5 import _decode_v5_object

        state = _decode_v5_object(document)
    else:
        state = _decode_v4_object(document, frozen)
    provenance = SnapshotProvenance(
        schema_origin=schema_origin,
        session_id=state.identity.session_id,
        document_digest="sha256:" + hashlib.sha256(source).hexdigest(),
    )
    bound_state = replace(state, snapshot_provenance=provenance)
    guidance = (
        decode_v5_guidance(document["guidance"], provenance)
        if schema_origin is SchemaOrigin.V5
        else decode_legacy_guidance(document, provenance)
    )
    binding = object()
    object.__setattr__(bound_state, "_snapshot_binding", binding)
    object.__setattr__(guidance, "_snapshot_binding", binding)
    return Snapshot(bound_state, guidance, provenance)


def encode_v5_snapshot(snapshot: Snapshot) -> bytes:
    if not isinstance(snapshot, Snapshot):
        raise TypeError("encode_v5_snapshot expects Snapshot")
    snapshot.__post_init__()
    if snapshot.state.schema_origin is not SchemaOrigin.V5:
        raise ValueError("encode-v5-requires-v5-snapshot")
    from .codec_v5 import encode_v5_state

    return encode_v5_state(snapshot.state, snapshot.guidance)
