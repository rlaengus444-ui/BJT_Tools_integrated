"""The 24 elements of SO(3,Z); column vectors, active rotations."""
from itertools import permutations, product
import numpy as np

def cubic_rotations():
    result = []
    for axes in permutations(range(3)):
        for signs in product((-1,1),repeat=3):
            r = np.zeros((3,3),dtype=np.int64)
            for column,axis in enumerate(axes):
                r[axis,column] = signs[column]
            if round(np.linalg.det(r)) == 1:
                result.append(r)
    result.sort(key=lambda r:(0 if np.array_equal(r,np.eye(3)) else 1,tuple(r.flat)))
    for r in result:
        r.setflags(write=False)
    return tuple(result)

ROTATIONS = cubic_rotations()
