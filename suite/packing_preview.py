"""Depth-buffered full-geometry VTK preview in the suite's existing Tk window."""
from __future__ import annotations

from hashlib import sha256
import tkinter as tk

import numpy as np
from PIL import Image, ImageTk

from powdernest.types import matrices_for


class PackingPreview:
    def __init__(self, parent, on_error=None):
        # Explicit backends: VTK wheels do not include native Tk widgets.
        import vtkmodules.vtkRenderingOpenGL2
        import vtkmodules.vtkRenderingFreeType
        from vtkmodules.vtkRenderingCore import vtkRenderer, vtkRenderWindow, vtkWindowToImageFilter

        self.widget = self.canvas = tk.Canvas(parent, background='white', highlightthickness=0)
        self.widget.pack(fill='both', expand=True)
        self.renderer = vtkRenderer(); self.renderer.SetBackground(1., 1., 1.)
        self.window = vtkRenderWindow(); self.window.SetOffScreenRendering(1)
        self.window.SetMultiSamples(0); self.window.AddRenderer(self.renderer)
        self.capture = vtkWindowToImageFilter(); self.capture.SetInput(self.window)
        self.capture.SetInputBufferTypeToRGB(); self.capture.ReadFrontBufferOff()
        self.capture.ShouldRerenderOff()
        self.camera = self.renderer.GetActiveCamera(); self.camera.ParallelProjectionOn()
        self.camera.SetPosition(1., 1., 1.); self.camera.SetViewUp(0., 0., 1.)
        self.bounds = np.array([[0., 0., 0.], [450., 500., 350.]])
        self.model_actors = []; self.model_mappers = {}
        self.image = self._photo = None
        self._image_item = self.canvas.create_image(0, 0, anchor='nw')
        self._render_id = None; self._drag_at = None; self.closed = False
        self.on_error = on_error; self.last_error = None
        self.canvas.bind('<Configure>', lambda event: self._request_render())
        self.canvas.bind('<ButtonPress-1>', self._drag_start)
        self.canvas.bind('<B1-Motion>', self._drag)
        self.canvas.bind('<ButtonRelease-1>', lambda event: setattr(self, '_drag_at', None))
        self.canvas.bind('<MouseWheel>', self.zoom)
        self.canvas.bind('<Button-4>', self.zoom); self.canvas.bind('<Button-5>', self.zoom)
        self.canvas.bind('<Destroy>', self._destroyed, add='+')
        self.set_scene({}, [], (450, 500, 350))

    @staticmethod
    def _mapper(mesh):
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
        from vtkmodules.vtkRenderingCore import vtkPolyDataMapper
        from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
        points = vtkPoints(); points.SetData(numpy_to_vtk(np.asarray(mesh.vertices), deep=True))
        faces = np.asarray(mesh.faces)
        cells = vtkCellArray(); cells.SetCells(len(faces), numpy_to_vtkIdTypeArray(
            np.column_stack((np.full(len(faces), 3), faces)).ravel().astype(np.int64), deep=True))
        surface = vtkPolyData(); surface.SetPoints(points); surface.SetPolys(cells)
        mapper = vtkPolyDataMapper(); mapper.SetInputData(surface)
        return mapper

    def set_scene(self, models, placements, size):
        from vtkmodules.vtkCommonMath import vtkMatrix4x4
        from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkSphereSource
        from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
        from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
        if self.closed:return '3D 미리보기 닫힘'
        self.renderer.RemoveAllViewProps(); self.model_actors = []; self.model_mappers = {}
        upper = np.asarray(size, dtype=float)
        cube = vtkCubeSource(); cube.SetBounds(0., upper[0], 0., upper[1], 0., upper[2])
        mapper = vtkPolyDataMapper(); mapper.SetInputConnection(cube.GetOutputPort())
        self.build_box = vtkActor(); self.build_box.SetMapper(mapper)
        self.build_box.GetProperty().SetRepresentationToWireframe()
        self.build_box.GetProperty().SetColor(.37, .43, .52)
        self.renderer.AddActor(self.build_box)
        self.axes = vtkAxesActor(); self.axes.SetTotalLength(*upper)
        self.axes.SetShaftTypeToLine(); self.axes.SetNormalizedTipLength(.025, .025, .025)
        captions=(self.axes.GetXAxisCaptionActor2D(), self.axes.GetYAxisCaptionActor2D(), self.axes.GetZAxisCaptionActor2D())
        for caption,color in zip(captions, ((.78,.27,.27),(.19,.52,.31),(.21,.45,.74))):
            caption.GetCaptionTextProperty().SetFontFamilyToArial()
            caption.GetCaptionTextProperty().SetColor(*color)
            caption.GetCaptionTextProperty().ShadowOff(); caption.SetHeight(.035)
        self.renderer.AddActor(self.axes)
        sphere = vtkSphereSource(); sphere.SetRadius(float(upper.max()) * .003)
        mapper = vtkPolyDataMapper(); mapper.SetInputConnection(sphere.GetOutputPort())
        origin = vtkActor(); origin.SetMapper(mapper); origin.GetProperty().SetColor(.37, .43, .52)
        self.renderer.AddActor(origin)
        low, high, count = np.zeros(3), upper.copy(), 0
        for pose, matrix in zip(placements, matrices_for(models, placements)):
            model = models[pose.model_id]
            if pose.model_id not in self.model_mappers:
                self.model_mappers[pose.model_id] = self._mapper(model.mesh)
            actor = vtkActor(); actor.SetMapper(self.model_mappers[pose.model_id])
            transform = vtkMatrix4x4()
            for i in range(4):
                for j in range(4):transform.SetElement(i, j, float(matrix[i, j]))
            actor.SetUserMatrix(transform)
            color = tuple((80 + c % 150) / 255 for c in sha256(pose.model_id.encode()).digest()[:3])
            actor.GetProperty().SetColor(*color); actor.GetProperty().SetOpacity(1.)
            self.renderer.AddActor(actor); self.model_actors.append(actor)
            bounds = np.asarray(actor.GetBounds()).reshape((3, 2)).T
            low = np.minimum(low, bounds[0]); high = np.maximum(high, bounds[1])
            count += len(model.mesh.faces)
        self.bounds = np.array([low, high]); self.align('fit')
        return f'원본 표면 전체 표시 · {len(placements):,}개 · {count:,} 삼각형 · 마우스 회전·휠 확대'

    def align(self, view):
        if self.closed:return
        directions = {'x+':(1,0,0), 'x-':(-1,0,0), 'y+':(0,1,0), 'y-':(0,-1,0),
                      'z+':(0,0,1), 'z-':(0,0,-1), 'origin':(1,1,1)}
        if view not in directions and view != 'fit':raise ValueError('지원하지 않는 보기입니다.')
        self._size_window()
        center = self.bounds.mean(axis=0)
        direction = np.asarray(directions[view] if view in directions else self.camera.GetDirectionOfProjection(), dtype=float)
        if view == 'fit':direction = -direction
        self.camera.SetFocalPoint(*center); self.camera.SetPosition(*(center + direction))
        if view != 'fit':
            self.camera.SetViewUp(*((0., 1., 0.) if view.startswith('z') else (0., 0., 1.)))
        self.renderer.ResetCamera(*self.bounds.T.ravel())
        if view == 'origin':
            distance = self.camera.GetDistance()
            self.camera.SetFocalPoint(0., 0., 0.)
            self.camera.SetPosition(*(direction / np.linalg.norm(direction) * distance))
            self.camera.SetParallelScale(float(np.abs(self.bounds).max()) * 1.5)
        self.renderer.ResetCameraClippingRange(); self._request_render()

    def _size_window(self):
        width, height = max(2, self.canvas.winfo_width()), max(2, self.canvas.winfo_height())
        self.window.SetSize(width, height)

    def _request_render(self):
        if not self.closed and self._render_id is None:
            self._render_id = self.canvas.after(20, self._draw)

    def _draw(self):
        self._render_id = None
        if self.closed:return
        try:self.render()
        except Exception as exc:
            self.last_error = str(exc)
            if self.on_error:self.on_error(str(exc))

    def render(self):
        from vtkmodules.util.numpy_support import vtk_to_numpy
        if self.closed:raise RuntimeError('3D 미리보기가 닫혔습니다.')
        self._size_window(); self.renderer.ResetCameraClippingRange(); self.window.Render()
        self.capture.Modified(); self.capture.Update()
        frame = self.capture.GetOutput(); width, height, unused = frame.GetDimensions()
        pixels = vtk_to_numpy(frame.GetPointData().GetScalars()).reshape((height, width, 3))
        self.image = Image.fromarray(np.flipud(pixels).copy())
        self._photo = ImageTk.PhotoImage(self.image, master=self.canvas)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self.last_error = None

    def save_image(self, path):
        self.render(); self.image.save(path)

    def _drag_start(self, event):
        self._drag_at = (event.x, event.y)

    def _drag(self, event):
        if self.closed or self._drag_at is None:return
        x, y = self._drag_at; self._drag_at = (event.x, event.y)
        self.camera.Azimuth((x - event.x) * .4); self.camera.Elevation((event.y - y) * .4)
        self.camera.OrthogonalizeViewUp(); self.renderer.ResetCameraClippingRange(); self._request_render()

    def zoom(self, event):
        if self.closed:return
        step = 1 if getattr(event, 'num', None) == 4 else -1 if getattr(event, 'num', None) == 5 else getattr(event, 'delta', 0)
        if not step:return
        scale = .85 if step > 0 else 1 / .85
        self.camera.SetParallelScale(max(1e-6, min(1e9, self.camera.GetParallelScale() * scale)))
        self.renderer.ResetCameraClippingRange(); self._request_render()

    def _destroyed(self, event):
        if event.widget is self.canvas:self.close()

    def close(self):
        if self.closed:return
        self.closed = True
        if self._render_id is not None:
            try:self.canvas.after_cancel(self._render_id)
            except tk.TclError:pass
            self._render_id = None
        self.capture.SetInput(None); self.renderer.RemoveAllViewProps(); self.window.Finalize()
        self.model_actors = []; self.model_mappers = {}; self._photo = None
