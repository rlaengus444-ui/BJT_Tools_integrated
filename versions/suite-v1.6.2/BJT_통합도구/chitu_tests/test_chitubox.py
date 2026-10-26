"""Synthetic format fixtures; these do not claim coverage of every writer."""

import struct

import numpy as np
import pytest
import trimesh

from chitu_overlap.chitubox import (
    ChituboxFormatError,
    MAGIC,
    parse_chitubox,
    read_chitubox,
)
from chitu_overlap.geometry import check_overlaps


TABLE = 512
STRIDE = 680
TAIL = 256


def project_blob(placements=((0, 0, 0),), *, shared_geometry=False, pointer_block=412):
    """Encode known table fields, including centered raw geometry and clones."""
    mesh = trimesh.creation.box(extents=(2, 2, 2))
    soup = np.asarray(mesh.triangles, dtype="<f4").tobytes()
    count = len(placements)
    geometry_start = TABLE + STRIDE * count
    blob = bytearray(geometry_start + len(soup) * (1 if shared_geometry else count))
    struct.pack_into("<4I", blob, 0, MAGIC, count, TABLE, pointer_block)
    if pointer_block:
        struct.pack_into("<2I", blob, pointer_block + 8, TABLE - 32, 32)
    for index, position in enumerate(placements):
        entry = TABLE + STRIDE * index
        encoded_name = f"cube-{index}.stl".encode()
        blob[entry:entry + len(encoded_name)] = encoded_name
        tail = entry + TAIL
        start = geometry_start + (0 if shared_geometry else index * len(soup))
        struct.pack_into("<3f4I", blob, tail, *position, 0xFFFFFFFF, start, len(soup), 0)
        blob[start:start + len(soup)] = soup
    return blob


def test_reads_geometry_with_authored_xy_and_lift():
    result = parse_chitubox(project_blob(((12, -4, 3),)))
    assert len(result) == 1
    assert result[0].name == "cube-0.stl"
    np.testing.assert_allclose(result[0].mesh.bounds, [[11, -5, 3], [13, -3, 5]])
    assert result[0].mesh.is_volume


def test_shared_geometry_still_yields_independent_placed_instances():
    result = parse_chitubox(project_blob(((0, 0, 0), (1, 0, 0)), shared_geometry=True))
    assert len(result) == 2
    assert result[0].mesh is not result[1].mesh
    report = check_overlaps(result)
    assert report["status"] == "violation"
    assert report["complete"]


def test_separate_and_vertically_stacked_instances_are_clear():
    result = parse_chitubox(project_blob(((0, 0, 0), (0, 0, 3))))
    assert check_overlaps(result)["status"] == "clear"


def test_extension_is_irrelevant_when_binary_header_matches(tmp_path):
    path = tmp_path / "project.chitu"
    path.write_bytes(project_blob())
    assert len(read_chitubox(path)) == 1


def test_header_without_pointer_block_uses_declared_table():
    assert len(parse_chitubox(project_blob(pointer_block=0))) == 1


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04" + b"\0" * 1024, struct.pack("<I", 0xAB231253) + b"\0" * 1024])
def test_rejects_wrong_container(data):
    with pytest.raises(ChituboxFormatError):
        parse_chitubox(data)


def test_declared_table_must_fit_completely():
    blob = project_blob()
    struct.pack_into("<I", blob, 4, 0xFFFFFFFF)
    with pytest.raises(ChituboxFormatError, match="테이블"):
        parse_chitubox(blob)


def test_does_not_guess_geometry_when_pointer_is_bad():
    blob = project_blob()
    struct.pack_into("<I", blob, TABLE + TAIL + 16, len(blob) - 1)
    with pytest.raises(ChituboxFormatError, match="삼각형"):
        parse_chitubox(blob)


def test_geometry_cannot_point_into_instance_table():
    blob = project_blob()
    struct.pack_into("<I", blob, TABLE + TAIL + 16, TABLE)
    with pytest.raises(ChituboxFormatError, match="삼각형"):
        parse_chitubox(blob)


def test_non_whole_triangle_region_is_rejected():
    blob = project_blob()
    struct.pack_into("<I", blob, TABLE + TAIL + 20, 37)
    with pytest.raises(ChituboxFormatError, match="삼각형"):
        parse_chitubox(blob)


@pytest.mark.parametrize("field", [0, 4, 8])
def test_nonfinite_placement_is_rejected(field):
    blob = project_blob()
    struct.pack_into("<f", blob, TABLE + TAIL + field, float("nan"))
    with pytest.raises(ChituboxFormatError, match="위치"):
        parse_chitubox(blob)


def test_nonfinite_vertices_are_rejected():
    blob = project_blob()
    geometry_start = struct.unpack_from("<I", blob, TABLE + TAIL + 16)[0]
    struct.pack_into("<f", blob, geometry_start, float("inf"))
    with pytest.raises(ChituboxFormatError, match="삼각형 좌표"):
        parse_chitubox(blob)


def test_native_support_frame_is_explicitly_unsupported():
    blob = project_blob()
    struct.pack_into("<I", blob, TABLE + TAIL + 12, 100)
    with pytest.raises(ChituboxFormatError, match="서포트/래프트"):
        parse_chitubox(blob)


def test_disagreeing_pointer_blocks_are_rejected():
    blob = project_blob()
    struct.pack_into("<I", blob, 412 + 8, 0)
    with pytest.raises(ChituboxFormatError, match="일치"):
        parse_chitubox(blob)
