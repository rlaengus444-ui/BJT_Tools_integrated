"""Budgeted display-only 3D preview; original geometry is never modified."""
from __future__ import annotations

from hashlib import sha256
from itertools import product
import math

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

from powdernest.types import matrices_for


DISPLAY_FACE_LIMIT = 18000


def preview_triangles(models, placements, limit=DISPLAY_FACE_LIMIT):
    """Bound repeated-copy display allocation, using full original transforms."""
    if not placements:
        return [], 0, 0
    per_copy = max(1, limit // len(placements))
    cache, output, displayed, original = {}, [], 0, 0
    for index, (pose, matrix) in enumerate(zip(placements, matrices_for(models, placements))):
        model = models[pose.model_id]; count = len(model.mesh.faces); original += count
        budget = min(per_copy, limit-displayed)
        if budget <= 0:
            continue
        key = (pose.model_id, budget)
        if key not in cache:
            faces = model.mesh.faces
            indices = np.linspace(0, count-1, min(count, budget), dtype=int)
            cache[key] = np.asarray(model.mesh.vertices)[np.asarray(faces)[indices]]
        triangles = cache[key]
        world = triangles @ matrix[:3, :3].T + matrix[:3, 3]
        color = tuple((80+c % 150)/255 for c in sha256(pose.model_id.encode()).digest()[:3])
        output.append((world, color)); displayed += len(world)
    return output, displayed, original


class PackingPreview:
    def __init__(self, parent):
        self.figure = Figure(figsize=(7.5, 6), dpi=100, facecolor='#f3f6fb')
        self.axes = self.figure.add_subplot(projection='3d')
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.widget = self.canvas.get_tk_widget()
        self.widget.pack(fill='both', expand=True)
        self.bounds = np.array([[0., 0., 0.], [450., 500., 350.]])
        self.canvas.mpl_connect('scroll_event', self.zoom)
        self.set_scene({}, [], (450, 500, 350))

    def set_scene(self, models, placements, size):
        axes = self.axes; axes.clear()
        upper = np.asarray(size, dtype=float)
        corners = np.array(list(product(*[(0., value) for value in upper])))
        edges = [(a,b) for a in range(8) for b in range(a+1,8)
                 if (a ^ b) in (1,2,4)]
        axes.add_collection3d(Line3DCollection([(corners[a],corners[b]) for a,b in edges], colors='#5e6d84', linewidths=.8))
        low, high = np.zeros(3), upper.copy()
        groups, displayed, original = preview_triangles(models, placements)
        if groups:
            triangles = np.concatenate([item[0] for item in groups])
            colors = np.concatenate([np.tile(color,(len(points),1)) for points,color in groups])
            axes.add_collection3d(Poly3DCollection(triangles, facecolors=colors, edgecolors='none', linewidths=0))
        extents = {}
        for pose in placements:
            key = (pose.model_id,pose.rotation_id)
            if key not in extents:
                bounds = models[pose.model_id].bounds_for(pose.rotation_id)
                extents[key] = bounds[1]-bounds[0]
            low = np.minimum(low,pose.position)
            high = np.maximum(high,np.asarray(pose.position)+extents[key])
        self.bounds = np.array([low,high])
        axes.scatter([0],[0],[0], c='#b32d46', s=14)
        axes.text(0,0,0,'  (0,0,0)', fontsize=7)
        axes.set(xlabel='X (mm)',ylabel='Y (mm)',zlabel='Z (mm)',proj_type='ortho')
        axes.set_facecolor('#ffffff');axes.tick_params(labelsize=7)
        self.figure.subplots_adjust(left=.01,right=.96,bottom=.03,top=.99)
        self.align('fit')
        if original > displayed:
            return f'화면만 간략 표시 · {displayed:,}/{original:,} 삼각형 · 검증·저장은 원본 사용'
        return f'3D 미리보기 · {displayed:,} 삼각형 · 마우스 회전·휠 확대'

    def align(self, view):
        directions={'x+':(0,0),'x-':(0,180),'y+':(0,90),'y-':(0,-90),
                    'z+':(90,-90),'z-':(-90,90),'origin':(math.degrees(math.atan(1/math.sqrt(2))),45)}
        if view in directions:
            self.axes.view_init(elev=directions[view][0],azim=directions[view][1])
        elif view != 'fit':
            raise ValueError('지원하지 않는 보기입니다.')
        center = self.bounds.mean(axis=0)
        span = max(1., float(np.ptp(self.bounds,axis=0).max())) * .55
        if view == 'origin':
            center = np.zeros(3);span = max(1.,float(np.abs(self.bounds).max()))*1.08
        for setter,position in zip((self.axes.set_xlim,self.axes.set_ylim,self.axes.set_zlim),center):
            setter(position-span,position+span)
        self.axes.set_box_aspect((1,1,1));self.canvas.draw_idle()

    def zoom(self, event):
        if event.inaxes is not self.axes:
            return
        scale = .85 if event.step > 0 else 1/.85
        for getter,setter in ((self.axes.get_xlim,self.axes.set_xlim),(self.axes.get_ylim,self.axes.set_ylim),(self.axes.get_zlim,self.axes.set_zlim)):
            a,b=getter();center=(a+b)*.5;span=max(1e-6,min(1e9,(b-a)*.5*scale))
            setter(center-span,center+span)
        self.canvas.draw_idle()
