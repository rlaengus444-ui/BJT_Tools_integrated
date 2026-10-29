//! Optional in-place edge verification kernel. No triangle/STL data is changed.
//!
//! The caller owns a disjoint, writable, contiguous array for the entire call.
//! Only valid pointers supplied by that caller are supported; an FFI cannot
//! establish whether an arbitrary non-null address maps to allocated memory.

use std::mem::{align_of, size_of};
use std::panic::{catch_unwind, AssertUnwindSafe};

const OK: i32 = 0;
const NULL_POINTER: i32 = 1;
const ALIGNMENT: i32 = 2;
const LENGTH: i32 = 3;
const DIRECTION: i32 = 4;
const CONTACT: i32 = 5;
const PLATFORM: i32 = 6;
const OVERLAP: i32 = 7;
const CAPACITY: i32 = 8;
const TRANSFORM: i32 = 9;
const PANIC: i32 = 255;

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EdgeRow {
    p0: u32,
    p1: u32,
    p2: u32,
    p3: u32,
    p4: u32,
    p5: u32,
    direction: i32,
    contact: i32,
}

#[repr(C)]
#[derive(Default, Debug, PartialEq, Eq)]
pub struct EdgeCounts {
    boundary_edges: u64,
    nonmanifold_edges: u64,
    inconsistent_winding_edges: u64,
    unique_edges: u64,
    voxel_contact_edges: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct GridTransform {
    xsp: f64,
    ysp: f64,
    zsp: f64,
    xoff: f64,
    yoff: f64,
}

const _: () = assert!(size_of::<EdgeRow>() == 32);
const _: () = assert!(align_of::<EdgeRow>() == 4);
const _: () = assert!(size_of::<EdgeCounts>() == 40);
const _: () = assert!(size_of::<GridTransform>() == 40);

impl EdgeRow {
    #[inline]
    fn keys(&self) -> (u64, u64, u64) {
        // Exact ordering of rows.view('<u8').reshape(-1, 4)[:, :3].
        (
            u64::from(self.p0) | (u64::from(self.p1) << 32),
            u64::from(self.p2) | (u64::from(self.p3) << 32),
            u64::from(self.p4) | (u64::from(self.p5) << 32),
        )
    }
}

fn sort_count(rows: &mut [EdgeRow]) -> Result<EdgeCounts, i32> {
    // Reject malformed metadata before the first mutation, including shifts or
    // abs() on values outside the producer's certified domain.
    for row in rows.iter() {
        if row.direction != -1 && row.direction != 1 {
            return Err(DIRECTION);
        }
        if !(-6..=6).contains(&row.contact) {
            return Err(CONTACT);
        }
    }
    // std's unstable sort operates in place without a full-row Vec allocation.
    // Equal-key order cannot affect the five commutative incidence metrics.
    rows.sort_unstable_by_key(EdgeRow::keys);
    let mut counts = EdgeCounts::default();
    let mut i = 0;
    while i < rows.len() {
        let key = rows[i].keys();
        let mut j = i + 1;
        let mut balance = i64::from(rows[i].direction);
        while j < rows.len() && rows[j].keys() == key {
            balance += i64::from(rows[j].direction);
            j += 1;
        }
        let count = j - i;
        counts.unique_edges += 1;
        if count == 1 {
            counts.boundary_edges += 1;
        } else if count != 2 {
            counts.nonmanifold_edges += 1;
            let mut rays = 0_u32;
            let mut valid = count == 4 && balance == 0;
            let mut sign = 0;
            for row in &rows[i..j] {
                let code = row.contact;
                if code == 0 {
                    valid = false;
                    continue;
                }
                let current = if code > 0 { 1 } else { -1 };
                if sign != 0 && sign != current {
                    valid = false;
                }
                sign = current;
                let bit = 1_u32 << (code.unsigned_abs() - 1);
                if rays & bit != 0 {
                    valid = false;
                }
                rays |= bit;
            }
            if valid && (rays == 15 || rays == 51 || rays == 60) {
                counts.voxel_contact_edges += 1;
            }
            if balance != 0 {
                counts.inconsistent_winding_edges += 1;
            }
        } else if balance != 0 {
            counts.inconsistent_winding_edges += 1;
        }
        i = j;
    }
    Ok(counts)
}

#[no_mangle]
pub extern "C" fn bjt_mesh_abi_version() -> u32 { 1 }
#[no_mangle]
pub extern "C" fn bjt_mesh_edge_row_size() -> usize { size_of::<EdgeRow>() }
#[no_mangle]
pub extern "C" fn bjt_mesh_edge_row_align() -> usize { align_of::<EdgeRow>() }
#[no_mangle]
pub extern "C" fn bjt_mesh_counts_size() -> usize { size_of::<EdgeCounts>() }
#[no_mangle]
pub extern "C" fn bjt_mesh_platform_supported() -> u32 {
    u32::from(cfg!(all(target_endian = "little", target_pointer_width = "64")))
}

/// Sort private rows in place and return five exact incidence counts.
///
/// # Safety
/// Nonempty rows must describe `len` initialized EdgeRows in one owned writable
/// allocation. `out` must point to initialized writable EdgeCounts. Their memory
/// ranges must be disjoint and remain valid/exclusive until the call returns.
#[no_mangle]
pub unsafe extern "C" fn bjt_mesh_sort_count_edges(
    rows: *mut EdgeRow,
    len: usize,
    out: *mut EdgeCounts,
) -> i32 {
    if bjt_mesh_platform_supported() == 0 { return PLATFORM; }
    if out.is_null() || (len != 0 && rows.is_null()) { return NULL_POINTER; }
    if (out as usize) % align_of::<EdgeCounts>() != 0
        || (len != 0 && (rows as usize) % align_of::<EdgeRow>() != 0) {
        return ALIGNMENT;
    }
    let Some(bytes) = len.checked_mul(size_of::<EdgeRow>()) else { return LENGTH; };
    if bytes > isize::MAX as usize { return LENGTH; }
    let Some(rows_end) = (rows as usize).checked_add(bytes) else { return LENGTH; };
    let Some(out_end) = (out as usize).checked_add(size_of::<EdgeCounts>()) else { return LENGTH; };
    if bytes != 0 && (rows as usize) < out_end && (out as usize) < rows_end { return OVERLAP; }
    // Every pointer/range check precedes dereferencing either caller buffer.
    out.write(EdgeCounts::default());
    if len == 0 { return OK; }
    match catch_unwind(AssertUnwindSafe(|| {
        let data = std::slice::from_raw_parts_mut(rows, len);
        sort_count(data)
    })) {
        Ok(Ok(counts)) => { out.write(counts); OK }
        Ok(Err(status)) => status,
        Err(_) => PANIC,
    }
}

#[no_mangle]
pub extern "C" fn bjt_mesh_triangle_pack_abi_version() -> u32 { 1 }

#[inline]
fn put_f32(output: &mut [u8], offset: usize, value: f32) {
    output[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn pack_grid(q: &[i64], transform: GridTransform, output: &mut [u8]) {
    let sx = 0.5 * transform.xsp;
    let sy = 0.5 * transform.ysp;
    let sz = 0.5 * transform.zsp;
    for (index, points) in q.chunks_exact(9).enumerate() {
        let mut vertices = [0_f32; 9];
        for vertex in 0..3 {
            let offset = vertex * 3;
            // Keep NumPy's two f64 operations separate, then cast to f32.
            // In particular z has no +0.0 operation (preserve signed zero).
            let x = points[offset] as f64 * sx;
            let y = points[offset + 1] as f64 * sy;
            vertices[offset] = (x + transform.xoff) as f32;
            vertices[offset + 1] = (y + transform.yoff) as f32;
            vertices[offset + 2] = (points[offset + 2] as f64 * sz) as f32;
        }
        let ab = [vertices[3] - vertices[0], vertices[4] - vertices[1], vertices[5] - vertices[2]];
        let ac = [vertices[6] - vertices[0], vertices[7] - vertices[1], vertices[8] - vertices[2]];
        let mut nx = ab[1] * ac[2] - ab[2] * ac[1];
        let mut ny = ab[2] * ac[0] - ab[0] * ac[2];
        let mut nz = ab[0] * ac[1] - ab[1] * ac[0];
        let length = ((nx * nx + ny * ny) + nz * nz).sqrt();
        if length > 0.0 {
            nx /= length;
            ny /= length;
            nz /= length;
        } else {
            nx = 0.0; ny = 0.0; nz = 0.0;
        }
        let base = index * 50;
        put_f32(output, base, nx);
        put_f32(output, base + 4, ny);
        put_f32(output, base + 8, nz);
        for (coordinate, value) in vertices.iter().enumerate() {
            put_f32(output, base + 12 + coordinate * 4, *value);
        }
        output[base + 48] = 0;
        output[base + 49] = 0;
    }
}

/// Pack existing, ordered grid triangles; never triangulate, retain or free q.
///
/// # Safety
/// q contains len*9 readable i64 values, transform is readable/aligned, and out
/// owns at least out_bytes writable bytes. Output must not alias either input;
/// no other thread may access any borrowed input/output during the call.
#[no_mangle]
pub unsafe extern "C" fn bjt_mesh_pack_grid_triangles(
    q: *const i64,
    len: usize,
    transform: *const GridTransform,
    out: *mut u8,
    out_bytes: usize,
) -> i32 {
    if bjt_mesh_platform_supported() == 0 { return PLATFORM; }
    if transform.is_null() || (len != 0 && (q.is_null() || out.is_null())) { return NULL_POINTER; }
    if (transform as usize) % align_of::<GridTransform>() != 0
        || (len != 0 && (q as usize) % align_of::<i64>() != 0) { return ALIGNMENT; }
    let Some(input_bytes) = len.checked_mul(72) else { return LENGTH; };
    let Some(needed) = len.checked_mul(50) else { return LENGTH; };
    if input_bytes > isize::MAX as usize || needed > isize::MAX as usize { return LENGTH; }
    if out_bytes < needed { return CAPACITY; }
    let Some(q_end) = (q as usize).checked_add(input_bytes) else { return LENGTH; };
    let Some(out_end) = (out as usize).checked_add(needed) else { return LENGTH; };
    let Some(transform_end) = (transform as usize).checked_add(size_of::<GridTransform>()) else { return LENGTH; };
    if needed != 0 && (((q as usize) < out_end && (out as usize) < q_end)
        || ((transform as usize) < out_end && (out as usize) < transform_end)) { return OVERLAP; }
    let transform = transform.read();
    if ![transform.xsp, transform.ysp, transform.zsp, transform.xoff, transform.yoff].iter().all(|v| v.is_finite()) {
        return TRANSFORM;
    }
    if len == 0 { return OK; }
    match catch_unwind(AssertUnwindSafe(|| {
        let points = std::slice::from_raw_parts(q, len * 9);
        let output = std::slice::from_raw_parts_mut(out, needed);
        pack_grid(points, transform, output);
    })) {
        Ok(()) => OK,
        Err(_) => PANIC,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(key: u32, direction: i32, contact: i32) -> EdgeRow {
        EdgeRow { p0: key, p1: 0, p2: 0, p3: 0, p4: 0, p5: 0, direction, contact }
    }

    #[test]
    fn exact_layout_and_paired_word_order() {
        assert_eq!(size_of::<EdgeRow>(), 32);
        assert_eq!(align_of::<EdgeRow>(), 4);
        assert_eq!(std::mem::offset_of!(EdgeRow, direction), 24);
        assert_eq!(std::mem::offset_of!(EdgeRow, contact), 28);
        let mut rows = [row(u32::MAX, 1, 0), EdgeRow { p1: 1, ..row(0, 1, 0) }];
        assert_eq!(sort_count(&mut rows).unwrap().unique_edges, 2);
        assert_eq!(rows[0].p0, u32::MAX);
    }

    #[test]
    fn incidence_and_contact_rules() {
        let mut rows = [row(4, 1, 1), row(4, -1, 2), row(4, 1, 3), row(4, -1, 4),
                        row(1, 1, 0), row(2, 1, 0), row(2, 1, 0)];
        assert_eq!(sort_count(&mut rows).unwrap(), EdgeCounts {
            boundary_edges: 1, nonmanifold_edges: 1, inconsistent_winding_edges: 1,
            unique_edges: 3, voxel_contact_edges: 1,
        });
        rows[6].contact = -4;
        assert_eq!(sort_count(&mut rows).unwrap().voxel_contact_edges, 0);
    }

    #[test]
    fn invalid_metadata_is_rejected_before_mutation() {
        for (bad, expected) in [(row(1, 0, 0), DIRECTION), (row(1, 1, i32::MIN), CONTACT)] {
            let mut rows = [row(9, 1, 0), bad];
            let before = rows;
            assert_eq!(sort_count(&mut rows).unwrap_err(), expected);
            assert_eq!(rows, before);
        }
    }

    #[test]
    fn ffi_empty_null_length_and_overlap_checks() {
        let mut out = EdgeCounts::default();
        unsafe {
            assert_eq!(bjt_mesh_sort_count_edges(std::ptr::null_mut(), 0, &mut out), OK);
            assert_eq!(bjt_mesh_sort_count_edges(std::ptr::null_mut(), 1, &mut out), NULL_POINTER);
            let mut data = [row(1, 1, 0), row(2, -1, 0)];
            assert_eq!(bjt_mesh_sort_count_edges(data.as_mut_ptr(), usize::MAX, &mut out), LENGTH);
            assert_eq!(bjt_mesh_sort_count_edges(data.as_mut_ptr(), 2, data.as_mut_ptr().cast()), OVERLAP);
        }
    }

    #[test]
    fn pack_abi_capacity_transform_and_exact_axis_normal() {
        let q = [0_i64, 0, 0, 1, 0, 0, 0, 1, 0];
        let transform = GridTransform { xsp: 2.0, ysp: 2.0, zsp: 2.0, xoff: 0.0, yoff: 0.0 };
        let mut output = [0xA5_u8; 50];
        unsafe {
            assert_eq!(bjt_mesh_pack_grid_triangles(q.as_ptr(), 1, &transform, output.as_mut_ptr(), 49), CAPACITY);
            assert_eq!(output, [0xA5; 50]);
            assert_eq!(bjt_mesh_pack_grid_triangles(q.as_ptr(), usize::MAX, &transform, output.as_mut_ptr(), 50), LENGTH);
            let invalid = GridTransform { xsp: f64::NAN, ..transform };
            assert_eq!(bjt_mesh_pack_grid_triangles(q.as_ptr(), 1, &invalid, output.as_mut_ptr(), 50), TRANSFORM);
            assert_eq!(output, [0xA5; 50]);
            assert_eq!(bjt_mesh_pack_grid_triangles(q.as_ptr(), 1, &transform, output.as_mut_ptr(), 50), OK);
        }
        assert_eq!(&output[0..8], &[0; 8]);
        assert_eq!(&output[8..12], &1.0_f32.to_le_bytes());
        assert_eq!(&output[48..50], &[0, 0]);
    }
}
