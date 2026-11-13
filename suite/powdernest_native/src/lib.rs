//! Small C ABI: no Python ABI, dependencies, RNG or final feasibility claims.
//! Ordinary IEEE binary64 operations; no fast-math or fused multiply-add.
//! Triangle status 2 delegates roundoff-sensitive cases to the reference.
use std::collections::HashSet;
use std::slice;

type P = [f64; 3];
type P2 = [f64; 2];
const UNCERTAIN: u8 = 2;
fn sub(a: P, b: P) -> P { [a[0]-b[0], a[1]-b[1], a[2]-b[2]] }
fn dot(a: P, b: P) -> f64 { (a[0]*b[0] + a[1]*b[1]) + a[2]*b[2] }
fn norm(a: P) -> f64 { dot(a, a).sqrt() }
fn cross(a: P, b: P) -> P {
    [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
}
fn point(a: &[f64], i: usize) -> P { [a[i*3], a[i*3+1], a[i*3+2]] }
fn project(p: P, drop: usize) -> P2 {
    match drop { 0 => [p[1],p[2]], 1 => [p[0],p[2]], _ => [p[0],p[1]] }
}
fn near(a: f64, b: f64, guard: f64) -> bool { (a-b).abs() <= guard }
fn guard(values: &[f64]) -> f64 {
    256.0*f64::EPSILON*values.iter().fold(1.0_f64, |x,y| x.max(y.abs()))
}

fn clip_with_epsilon(subject: Vec<P2>, tri:[P2;3], eps:f64, round:f64) -> Option<Vec<P2>> {
    // Reject uncertain half-plane comparisons before using the clipping
    // output as proof. Recheck generated vertices at every clipping edge.
    let mut points=subject;
    let u=[tri[1][0]-tri[0][0],tri[1][1]-tri[0][1]];
    let v=[tri[2][0]-tri[0][0],tri[2][1]-tri[0][1]];
    let det=u[0]*v[1]-u[1]*v[0];
    if det.abs() <= 256.0*f64::EPSILON*((u[0]*v[1]).abs()+(u[1]*v[0]).abs()) { return None; }
    let sign=det.signum();
    for edge in 0..3 {
        if points.is_empty() { return Some(points); }
        let first=tri[edge]; let last=tri[(edge+1)%3];
        let dir=[last[0]-first[0],last[1]-first[1]];
        let len=(dir[0]*dir[0]+dir[1]*dir[1]).sqrt();
        if len == 0.0 { return None; }
        let dist=|p:P2| sign*(dir[0]*(p[1]-first[1])-dir[1]*(p[0]-first[0]))/len;
        let mut out=Vec::with_capacity(points.len()+1);
        let mut a=*points.last().unwrap(); let mut da=dist(a);
        for &b in &points {
            let db=dist(b);
            if near(da,-eps,round) || near(db,-eps,round) { return None; }
            if (da>=-eps)!=(db>=-eps) {
                let denominator=da-db;
                if denominator.abs() <= f64::MIN_POSITIVE { return None; }
                let t=(da/denominator).clamp(0.0,1.0);
                out.push([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])]);
            }
            if db>=-eps { out.push(b); }
            a=b; da=db;
        }
        points=out;
    }
    Some(points)
}

fn section(tri:[P;3], d:P, eps:f64) -> Vec<P> {
    let mut out=Vec::with_capacity(6);
    for i in 0..3 { if d[i].abs()<=eps { out.push(tri[i]); } }
    for (i,j) in [(0,1),(1,2),(2,0)] {
        if (d[i]< -eps && d[j]>eps) || (d[j]< -eps && d[i]>eps) {
            let t=d[i]/(d[i]-d[j]); let delta=sub(tri[j],tri[i]);
            out.push([tri[i][0]+t*delta[0],tri[i][1]+t*delta[1],tri[i][2]+t*delta[2]]);
        }
    }
    out
}

fn triangle_status(a:[P;3], b:[P;3], shared:&[P], na:P, nb:P, eps:f64) -> u8 {
    let all:Vec<f64>=a.iter().chain(b.iter()).flatten().copied().collect();
    let round=guard(&all);
    let da=a.map(|p| dot(sub(p,b[0]),nb));
    let db=b.map(|p| dot(sub(p,a[0]),na));
    if da.iter().chain(db.iter()).any(|d| !d.is_finite() || near(*d,eps,round) || near(*d,-eps,round)) { return UNCERTAIN; }
    if da.iter().all(|d| *d>eps) || da.iter().all(|d| *d < -eps)
        || db.iter().all(|d| *d>eps) || db.iter().all(|d| *d < -eps) { return 0; }
    let axis=cross(na,nb); let length=norm(axis);
    if da.iter().chain(db.iter()).all(|d| d.abs()<=eps) {
        let mut drop=0;
        for i in 1..3 { if na[i].abs()>na[drop].abs() { drop=i; } }
        let polygon=match clip_with_epsilon(a.map(|p|project(p,drop)).to_vec(),b.map(|p|project(p,drop)),eps,round) {
            Some(p)=>p, None=>return UNCERTAIN,
        };
        if polygon.is_empty() { return 0; }
        if shared.is_empty() { return 1; }
        let s=project(shared[0],drop);
        for p in polygon {
            let mut d=[p[0]-s[0],p[1]-s[1]];
            if shared.len()>1 {
                let t=project(shared[1],drop); let seg=[t[0]-s[0],t[1]-s[1]];
                let den=seg[0]*seg[0]+seg[1]*seg[1];
                if den==0.0 { return UNCERTAIN; }
                let k=((d[0]*seg[0]+d[1]*seg[1])/den).clamp(0.0,1.0);
                d=[d[0]-k*seg[0],d[1]-k*seg[1]];
            }
            let distance=(d[0]*d[0]+d[1]*d[1]).sqrt();
            if near(distance,4.0*eps,round*4.0) { return UNCERTAIN; }
            if distance>4.0*eps { return 1; }
        }
        return 0;
    }
    if length<=32.0*f64::EPSILON || near(length,32.0*f64::EPSILON,64.0*f64::EPSILON) { return UNCERTAIN; }
    if shared.len()>=2 { return 0; }
    let sa=section(a,da,eps); let sb=section(b,db,eps);
    if sa.is_empty() || sb.is_empty() { return 0; }
    let axis=axis.map(|v|v/length);
    let pa:Vec<f64>=sa.iter().map(|p|dot(sub(*p,a[0]),axis)).collect();
    let pb:Vec<f64>=sb.iter().map(|p|dot(sub(*p,a[0]),axis)).collect();
    let low=pa.iter().copied().fold(f64::INFINITY,f64::min).max(pb.iter().copied().fold(f64::INFINITY,f64::min));
    let high=pa.iter().copied().fold(f64::NEG_INFINITY,f64::max).min(pb.iter().copied().fold(f64::NEG_INFINITY,f64::max));
    if near(low,high+eps,round*8.0) { return UNCERTAIN; }
    if low>high+eps { return 0; }
    if shared.is_empty() { return 1; }
    let at=dot(sub(shared[0],a[0]),axis);
    let beyond=(low-at).abs().max((high-at).abs());
    if near(beyond,4.0*eps,round*8.0) || beyond>4.0*eps { return UNCERTAIN; }
    0
}

#[no_mangle]
pub extern "C" fn pn_abi_version() -> u32 { 1 }

/// Caller provides validated contiguous arrays and output capacity npairs.
#[no_mangle]
pub unsafe extern "C" fn pn_triangle_pairs(tris:*const f64, normals:*const f64, faces:*const i64,
    nfaces:usize, pairs:*const i64, npairs:usize, eps:f64, output:*mut u8) -> i64 {
    let t=slice::from_raw_parts(tris,nfaces*9); let n=slice::from_raw_parts(normals,nfaces*3);
    let f=slice::from_raw_parts(faces,nfaces*3); let pairs=slice::from_raw_parts(pairs,npairs*2);
    let out=slice::from_raw_parts_mut(output,npairs);
    for k in 0..npairs {
        let i=pairs[k*2]; let j=pairs[k*2+1];
        if i<0 || j<0 || i as usize>=nfaces || j as usize>=nfaces { return -1; }
        let i=i as usize; let j=j as usize;
        let a=[point(t,i*3),point(t,i*3+1),point(t,i*3+2)];
        let b=[point(t,j*3),point(t,j*3+1),point(t,j*3+2)];
        let mut common=Vec::new();
        for v in 0..3 { if f[j*3..j*3+3].contains(&f[i*3+v]) { common.push((f[i*3+v],a[v])); } }
        common.sort_by_key(|p|p.0);
        let shared:Vec<P>=common.iter().map(|p|p.1).collect();
        out[k]=triangle_status(a,b,&shared,point(n,i),point(n,j),eps);
    }
    npairs as i64
}

#[no_mangle]
pub unsafe extern "C" fn pn_unique_rows(rows:*const i64, count:usize, output:*mut i64) -> i64 {
    let data=slice::from_raw_parts(rows,count*3); let out=slice::from_raw_parts_mut(output,count);
    let mut set=HashSet::with_capacity(count); let mut n=0;
    for i in 0..count { if set.insert([data[i*3],data[i*3+1],data[i*3+2]]) { out[n]=i as i64; n+=1; } }
    n as i64
}

#[no_mangle]
pub unsafe extern "C" fn pn_mesh_contacts(world:*const f64, nw:usize, own:*const f64, no:usize,
    gap:f64, extent:*const f64, wall:f64, output:*mut f64) -> i64 {
    let world=slice::from_raw_parts(world,nw*3); let own=slice::from_raw_parts(own,no*3);
    let extent=slice::from_raw_parts(extent,3); let count=nw*(8+5*no);
    let out=slice::from_raw_parts_mut(output,count*3); let mut k=0;
    for i in 0..nw {
        let w=point(world,i);
        for (x,y) in [(1.0,1.0),(1.0,-1.0),(-1.0,1.0),(-1.0,-1.0)] {
            for shrink in [false,true] {
                let cx=w[0]+gap*x; let cy=w[1]+gap*y;
                out[k*3]=if shrink {cx-extent[0]} else {cx};
                out[k*3+1]=if shrink {cy-extent[1]} else {cy};
                out[k*3+2]=wall; k+=1;
            }
        }
        for j in 0..no {
            let o=point(own,j); let base=sub(w,o);
            for kind in 0..5 {
                let mut value=base;
                match kind {
                    0=>{value[0]+=gap; value[1]+=gap;},
                    1=>{value[0]-=gap; value[1]-=gap;},
                    2=>{value[0]+=gap; value[1]-=gap;},
                    3=>{value[0]-=gap; value[1]+=gap;},
                    _=>value[2]+=gap,
                }
                out[k*3..k*3+3].copy_from_slice(&value); k+=1;
            }
        }
    }
    count as i64
}

#[no_mangle]
pub unsafe extern "C" fn pn_neighbors(low:*const f64, high:*const f64, lows:*const f64,
    highs:*const f64, required:*const f64, n:usize, tolerance:f64, distances:*mut f64, output:*mut u8) -> i64 {
    let low=point(slice::from_raw_parts(low,3),0); let high=point(slice::from_raw_parts(high,3),0);
    let lo=slice::from_raw_parts(lows,n*3); let hi=slice::from_raw_parts(highs,n*3);
    let req=slice::from_raw_parts(required,n); let distance=slice::from_raw_parts_mut(distances,n);
    let out=slice::from_raw_parts_mut(output,n);
    for i in 0..n {
        let mut separation=[0.0;3];
        for a in 0..3 { separation[a]=(lo[i*3+a]-high[a]).max(low[a]-hi[i*3+a]).max(0.0); }
        let d=norm(separation); distance[i]=d; let threshold=req[i]+tolerance;
        out[i]=if near(d,threshold,guard(&[d,threshold])) {UNCERTAIN} else {u8::from(d<=threshold)};
    }
    n as i64
}

#[no_mangle]
pub unsafe extern "C" fn pn_solid_filter(points:*const f64, count:usize, extent:*const f64,
    lows:*const f64, highs:*const f64, required:*const f64, n:usize, tol:f64, output:*mut u8) -> i64 {
    let p=slice::from_raw_parts(points,count*3); let extent=point(slice::from_raw_parts(extent,3),0);
    let lo=slice::from_raw_parts(lows,n*3); let hi=slice::from_raw_parts(highs,n*3);
    let req=slice::from_raw_parts(required,n); let out=slice::from_raw_parts_mut(output,count);
    for i in 0..count {
        let low=point(p,i); let high=[low[0]+extent[0],low[1]+extent[1],low[2]+extent[2]];
        let mut state=1;
        for j in 0..n {
            let other=point(lo,j); let top=point(hi,j); let mut sep=[0.0;3]; let mut overlap=true;
            for a in 0..3 {
                sep[a]=(other[a]-high[a]).max(low[a]-top[a]).max(0.0);
                overlap &= high[a].min(top[a])-low[a].max(other[a])>0.0;
            }
            if overlap { state=0; break; }
            let d=norm(sep); let threshold=req[j]-tol;
            if near(d,threshold,guard(&[d,threshold])) { state=UNCERTAIN; }
            else if d<threshold { state=0; break; }
        }
        out[i]=state;
    }
    count as i64
}

#[no_mangle]
pub unsafe extern "C" fn pn_overlap_cost(lows:*const f64, extents:*const f64, count:usize,
    other_lows:*const f64, other_extents:*const f64, n:usize, gaps:*const f64, output:*mut f64) -> i64 {
    let low=slice::from_raw_parts(lows,count*3); let sizes=slice::from_raw_parts(extents,count*3);
    let other=slice::from_raw_parts(other_lows,n*3); let other_sizes=slice::from_raw_parts(other_extents,n*3);
    let gaps=slice::from_raw_parts(gaps,count*n); let out=slice::from_raw_parts_mut(output,count*n);
    for i in 0..count { for j in 0..n {
        let mut sep=[0.0;3]; let mut overlap=true; let mut depth=f64::INFINITY;
        let gap=gaps[i*n+j];
        for a in 0..3 {
            let l=low[i*3+a]; let h=l+sizes[i*3+a]; let ol=other[j*3+a]; let oh=ol+other_sizes[j*3+a];
            sep[a]=(ol-h).max(l-oh).max(0.0);
            overlap &= h.min(oh)-l.max(ol)>0.0;
            depth=depth.min((h-ol).min(oh-l)+gap);
        }
        let value=if overlap { depth.max(0.0) } else { (gap-norm(sep)).max(0.0) };
        out[i*n+j]=value*value;
    } }
    (count*n) as i64
}

#[no_mangle]
pub unsafe extern "C" fn pn_compaction_contacts(lows:*const f64, extents:*const f64, gaps:*const f64,
    n:usize, axis:usize, own:f64, wall:f64, upper:f64, minimum:f64, output:*mut f64) -> i64 {
    if axis>2 { return -1; }
    let low=slice::from_raw_parts(lows,n*3); let sizes=slice::from_raw_parts(extents,n*3);
    let gaps=slice::from_raw_parts(gaps,n); let out=slice::from_raw_parts_mut(output,n*2+1);
    let mut values=Vec::with_capacity(n*2+1); values.push(wall);
    for i in 0..n { values.push(low[i*3+axis]+sizes[i*3+axis]+gaps[i]); values.push(low[i*3+axis]-own-gaps[i]); }
    values.retain(|v|wall<=*v && *v<upper-minimum);
    values.sort_by(|a,b|a.partial_cmp(b).unwrap()); values.dedup_by(|a,b| *a==*b);
    out[..values.len()].copy_from_slice(&values); values.len() as i64
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn basic_intersection_and_separation() {
        let a=[[0.0,0.0,0.0],[2.0,0.0,0.0],[0.0,2.0,0.0]];
        let separated=a.map(|p|[p[0],p[1],p[2]+1.0]);
        assert_eq!(triangle_status(a,separated,&[],[0.0,0.0,1.0],[0.0,0.0,1.0],1e-10),0);
        assert_eq!(triangle_status(a,a,&[],[0.0,0.0,1.0],[0.0,0.0,1.0],1e-10),1);
    }
    #[test] fn stable_unique_keeps_first_occurrences() {
        let rows=[1,2,3,4,5,6,1,2,3]; let mut out=[-1;3];
        assert_eq!(unsafe {pn_unique_rows(rows.as_ptr(),3,out.as_mut_ptr())},2);
        assert_eq!(&out[..2],&[0,1]);
    }
}
