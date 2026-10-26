"""Regression checks for bounded, exact CHITUBOX geometry loading."""

import struct
import threading

import numpy as np
import pytest
import trimesh

from chitu_overlap import chitubox as loader


def blob(payloads, positions=None):
    if positions is None:
        positions = [(0, 0, 0)] * len(payloads)
    table = 512
    pointer = 412
    start = table + 680 * len(payloads)
    data = bytearray(start + sum(len(value) for value in payloads))
    struct.pack_into('<4I', data, 0, loader.MAGIC, len(payloads), table, pointer)
    struct.pack_into('<2I', data, pointer + 8, table - 32, 32)
    for index, (payload, position) in enumerate(zip(payloads, positions)):
        entry = table + index * 680
        data[entry:entry + 9] = b'model.stl'
        struct.pack_into('<3f4I', data, entry + 256, *position, 0xFFFFFFFF, start, len(payload), 0)
        data[start:start + len(payload)] = payload
        start += len(payload)
    return data


def cube_payload():
    return np.asarray(trimesh.creation.box(extents=(2, 2, 2)).triangles, dtype='<f4').tobytes()


def track_mappings(monkeypatch):
    original = loader.mmap.mmap
    mappings = []

    def create(*args, **kwargs):
        mapped = original(*args, **kwargs)
        mappings.append(mapped)
        return mapped

    monkeypatch.setattr(loader.mmap, 'mmap', create)
    return mappings


def test_disk_reader_maps_and_closes_without_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'mapped.chitubox'
    path.write_bytes(blob([cube_payload()]))
    mappings = track_mappings(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError('Whole-file read_bytes is forbidden')

    monkeypatch.setattr(type(path), 'read_bytes', forbidden)
    objects = loader.read_chitubox(path)
    assert len(objects) == 1
    assert len(mappings) == 1 and mappings[0].closed
    assert objects[0].mesh.is_watertight
    path.unlink()


def test_mmap_nan_error_preserves_format_error_and_closes(tmp_path, monkeypatch):
    payload = np.frombuffer(cube_payload(), dtype='<f4').copy()
    payload[0] = np.nan
    path = tmp_path / 'nan.chitubox'
    path.write_bytes(blob([payload.tobytes()]))
    mappings = track_mappings(monkeypatch)
    with pytest.raises(loader.ChituboxFormatError, match='NaN'):
        loader.read_chitubox(path)
    assert mappings[0].closed
    path.unlink()


def test_identical_payload_spans_index_once_and_share_only_faces(monkeypatch):
    original = loader._index_geometry
    indexed = []

    def count(*args, **kwargs):
        indexed.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(loader, '_index_geometry', count)
    objects = loader.parse_chitubox(blob([cube_payload(), cube_payload()], [(0, 0, 0), (5, -2, 3)]))
    first, second = [obj.mesh for obj in objects]
    assert len(indexed) == 1
    assert np.shares_memory(first.faces, second.faces)
    assert not np.shares_memory(first.vertices, second.vertices)
    assert loader.native_geometry_key(first) == loader.native_geometry_key(second)
    np.testing.assert_allclose(second.bounds, [[4, -3, 3], [6, -1, 5]])


def test_native_array_buffers_are_immutable_and_replacement_invalidates_token():
    mesh = loader.parse_chitubox(blob([cube_payload()]))[0].mesh
    assert loader.native_geometry_key(mesh)
    with pytest.raises(AttributeError):
        mesh.metadata['_native_geometry_provenance'].digest = 'forged'
    for array in [mesh.vertices, mesh.faces]:
        with pytest.raises(ValueError):
            array.setflags(write=True)
    mesh.vertices = mesh.vertices.copy()
    assert loader.native_geometry_key(mesh) is None


def test_face_replacement_and_mesh_copy_cannot_reuse_native_certificate():
    mesh = loader.parse_chitubox(blob([cube_payload()]))[0].mesh
    copied = mesh.copy()
    assert loader.native_geometry_key(copied) is None
    mesh.faces = mesh.faces.copy()
    assert loader.native_geometry_key(mesh) is None


def test_equal_sized_inverted_payload_gets_distinct_identity():
    original = np.frombuffer(cube_payload(), dtype='<f4').reshape(-1, 3, 3)
    inverted = original[:, ::-1, :].copy()
    objects = loader.parse_chitubox(blob([original.tobytes(), inverted.tobytes()]))
    assert loader.native_geometry_key(objects[0].mesh) != loader.native_geometry_key(objects[1].mesh)
    assert objects[0].mesh.volume > 0
    assert objects[1].mesh.volume < 0


def test_signed_zero_positions_weld_numerically():
    tetra = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
        process=False,
    )
    soup = np.asarray(tetra.triangles, dtype='<f4').copy()
    zeros = np.flatnonzero(soup.ravel() == 0)
    soup.ravel()[zeros[::2]] = -0.0
    mesh = loader.parse_chitubox(blob([soup.tobytes()]))[0].mesh
    assert len(mesh.vertices) == 4
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(1 / 6)


def test_distinct_sub_tolerance_vertices_are_not_silently_merged():
    soup = np.asarray([
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[1e-9, 0, 0], [1, 0, 0], [0, 1, 0]],
    ], dtype='<f4')
    mesh = loader.parse_chitubox(blob([soup.tobytes()]))[0].mesh
    assert len(mesh.vertices) == 4
    assert np.count_nonzero(mesh.vertices[:, 0] == float(np.float32(1e-9))) == 1


def test_world_translation_happens_in_float64_after_indexing():
    objects = loader.parse_chitubox(blob([cube_payload()], [(123.12345123291016, -54.123451232910156, 3.25)]))
    mesh = objects[0].mesh
    assert mesh.vertices.dtype == np.float64
    np.testing.assert_allclose(mesh.bounds, [[122.12345123291016, -55.123451232910156, 3.25],
                                          [124.12345123291016, -53.123451232910156, 5.25]], rtol=0, atol=0)


def test_cancelled_file_loading_does_not_allocate_geometry(tmp_path, monkeypatch):
    path = tmp_path / 'cancelled.chitubox'
    path.write_bytes(blob([cube_payload()]))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(loader.ChituboxCancelled):
        loader.read_chitubox(path, cancel_event=cancel)


def test_progress_is_monotonic_and_can_cancel_between_objects(tmp_path, monkeypatch):
    path = tmp_path / 'progress.chitubox'
    path.write_bytes(blob([cube_payload(), cube_payload()]))
    cancel = threading.Event()
    events = []
    mappings = track_mappings(monkeypatch)

    def progress(value, message):
        events.append((value, message))
        if value >= 50:
            cancel.set()

    with pytest.raises(loader.ChituboxCancelled):
        loader.read_chitubox(path, cancel_event=cancel, progress=progress)
    assert events
    assert [event[0] for event in events] == sorted(event[0] for event in events)
    assert mappings[0].closed


def test_complete_progress_reaches_one_hundred():
    events = []
    loader.parse_chitubox(blob([cube_payload()]), progress=lambda value, message: events.append(value))
    assert events[0] == 0
    assert events[-1] == 100


def test_verified_hash_weld_reconstructs_every_original_position():
    rng = np.random.default_rng(19283)
    base = rng.normal(size=(2000, 3)).astype('<f4')
    soup = base[rng.integers(0, len(base), size=12_000)]
    mesh = loader.parse_chitubox(blob([soup.tobytes()]))[0].mesh
    expected = soup.astype(np.float64)
    expected[:, 2] -= expected[:, 2].min()
    np.testing.assert_array_equal(mesh.triangles.reshape(-1, 3), expected)
    assert len(mesh.vertices) == len(np.unique(soup.view('V12')))


def test_forced_hash_collision_falls_back_to_exact_positions(monkeypatch):
    original = loader._exact_vertex_index
    fallback = []

    def exact(vertices):
        fallback.append(True)
        return original(vertices)

    monkeypatch.setattr(loader, '_vertex_hash64', lambda vertices, cancel_event: np.zeros(len(vertices), dtype=np.uint64))
    monkeypatch.setattr(loader, '_exact_vertex_index', exact)
    mesh = loader.parse_chitubox(blob([cube_payload()]))[0].mesh
    assert fallback == [True]
    assert len(mesh.vertices) == 8
    expected = np.frombuffer(cube_payload(), dtype='<f4').astype(np.float64).reshape(-1, 3)
    expected[:, 2] += 1
    np.testing.assert_array_equal(mesh.triangles.reshape(-1, 3), expected)


def test_collision_in_final_chunk_is_also_detected(monkeypatch):
    monkeypatch.setattr(loader, '_VERTEX_CHUNK', 3)
    original = loader._exact_vertex_index
    fallback = []

    def hashes(vertices, cancel_event):
        values = np.arange(len(vertices), dtype=np.uint64)
        values[-1] = values[0]
        return values

    def exact(vertices):
        fallback.append(True)
        return original(vertices)

    monkeypatch.setattr(loader, '_vertex_hash64', hashes)
    monkeypatch.setattr(loader, '_exact_vertex_index', exact)
    soup = np.arange(90, dtype='<f4').reshape(-1, 3)
    mesh = loader.parse_chitubox(blob([soup.tobytes()]))[0].mesh
    expected = soup.astype(np.float64)
    expected[:, 2] -= 2
    np.testing.assert_array_equal(mesh.triangles.reshape(-1, 3), expected)
    assert fallback == [True]


def test_signed_zero_canonicalization_preserves_subnormal_positions():
    tiny = np.nextafter(np.float32(0), np.float32(1))
    soup = np.asarray([[0, -0.0, 0], [tiny, 0, 0], [0, 1, 0],
                       [-0.0, 0, 0], [tiny, 0, 0], [0, 0, 1]], dtype='<f4')
    mesh = loader.parse_chitubox(blob([soup.tobytes()]))[0].mesh
    np.testing.assert_array_equal(mesh.triangles.reshape(-1, 3), soup.astype(np.float64))
    assert len(mesh.vertices) == 4
    assert np.count_nonzero(mesh.vertices[:, 0] == float(tiny)) == 1


def test_cancellation_after_hashing_closes_mapping(tmp_path, monkeypatch):
    path = tmp_path / 'cancel-hash.chitubox'
    path.write_bytes(blob([cube_payload()]))
    mappings = track_mappings(monkeypatch)
    cancel = threading.Event()
    original = loader._vertex_hash64

    def hashes(vertices, event):
        result = original(vertices, event)
        event.set()
        return result

    monkeypatch.setattr(loader, '_vertex_hash64', hashes)
    with pytest.raises(loader.ChituboxCancelled):
        loader.read_chitubox(path, cancel_event=cancel)
    assert mappings[0].closed
