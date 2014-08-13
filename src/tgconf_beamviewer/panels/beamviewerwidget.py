from datetime import datetime, timedelta
import json
from functools import wraps, partial

from taurus.core import TaurusAttribute
from taurus.qt.qtgui.panel import TaurusWidget
from taurus.qt import QtGui, QtCore
from taurus.core.util import CodecFactory
from pyqtgraph.widgets.RawImageWidget import RawImageGLWidget, RawImageWidget
from pyqtgraph.Point import *
import pyqtgraph as pg

import PyTango

pg.setConfigOption('background', (50, 50, 50))
pg.setConfigOption('foreground', 'w')


class throttle(object):
    """
    Decorator that prevents a function from being called more than once every
    time period.

    To create a function that cannot be called more than once a minute:

        @throttle(minutes=1)
        def my_fun():
            pass
    """
    def __init__(self, seconds=0, minutes=0, hours=0):
        self.throttle_period = timedelta(
            seconds=seconds, minutes=minutes, hours=hours
        )
        self.time_of_last_call = datetime.min

    def __call__(self, fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            now = datetime.now()
            time_since_last_call = now - self.time_of_last_call

            if time_since_last_call > self.throttle_period:
                self.time_of_last_call = now
                return fn(*args, **kwargs)

        return wrapper


class FastHistogramLUTItem(pg.HistogramLUTItem):

    "A not-really-that-fast Histogram"

    def setImageItem(self, img):
        self.imageItem = img
        img.sigImageChanged.connect(self.imageChanged)
        img.setLookupTable(self.getLookupTable)  ## send function pointer, not the result
        #self.gradientChanged()
        self.regionChanged()
        self.imageChanged(autoLevel=False)
        #self.vb.autoRange()


class BeamViewerImageWidget(TaurusWidget):

    image_trigger = QtCore.pyqtSignal()
    roi_trigger = QtCore.pyqtSignal(int, int, int, int)
    vline_trigger = QtCore.pyqtSignal(int)
    hline_trigger = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)
        self._setup_ui()

        self.codec = CodecFactory().getCodec("VIDEO_IMAGE")
        self.image_trigger.connect(self.update_image)

        self.image = None

        self.json_codec = CodecFactory().getCodec('JSON')

        self.attr_image = None
        self.attr_vline = self.attr_hline = None
        self.attr_roi = None

    def _setup_ui(self):
        self.layout = QtGui.QVBoxLayout(self)
        self.setLayout(self.layout)

        self.graphics = pg.GraphicsLayoutWidget()
        self.layout.addWidget(self.graphics)

        self.imageplot = self.graphics.addPlot()
        self.imageplot.setAspectLocked()
        self.imageplot.invertY()
        self.imageplot.showGrid(x=True, y=True)

        self.imageitem = pg.ImageItem()
        self.imageplot.addItem(self.imageitem)

        # Lima rectangle ROI
        self.roi = pg.RectROI((0, 0), (100, 100),
                              movable=False, pen=(0, 9),
                              scaleSnap=True, translateSnap=True)
        self.roi.addTranslateHandle(pos=(0, 0))
        #self.imageplot.addItem(self.roi)
        self.roi_trigger.connect(self.update_roi)
        self.roi.sigRegionChanged.connect(self.handle_roi_start)
        self.roi.sigRegionChangeFinished.connect(self.handle_roi_finish)
        self._roi_dragged = False

        # Vertical and horizontal lines for user point selection
        # A.k.a crosshair
        self.verline = pg.InfiniteLine(pos=(100, 100), movable=True)
        self.verline.sigPositionChanged.connect(self.handle_lines_changed)
        self.verline.sigPositionChangeFinished.connect(self.handle_lines_finished)
        self.horline = pg.InfiniteLine(pos=(100, 100), angle=0, movable=True)
        self.horline.sigPositionChanged.connect(self.handle_lines_changed)
        self.horline.sigPositionChangeFinished.connect(self.handle_lines_finished)
        self._line_dragged = False
        self.vline_trigger.connect(self.update_vline)
        self.hline_trigger.connect(self.update_hline)

        # Histogram
        self.hist = FastHistogramLUTItem(image=self.imageitem,
                                         fillHistogram=False)
        # self.graphics.addItem(self.hist, row=0, col=1)
        bottom_stuff = QtGui.QHBoxLayout()
        self.layout.addLayout(bottom_stuff)

        self.hist_checkbox = QtGui.QCheckBox("Histogram")
        self.hist_checkbox.stateChanged.connect(self.show_histogram)
        bottom_stuff.addWidget(self.hist_checkbox)

        self.roi_checkbox = QtGui.QCheckBox("ROI")
        self.roi_checkbox.stateChanged.connect(self.show_roi)
        bottom_stuff.addWidget(self.roi_checkbox)

        self.lines_checkbox = QtGui.QCheckBox("Lines")
        self.lines_checkbox.stateChanged.connect(self.show_lines)
        bottom_stuff.addWidget(self.lines_checkbox)

        self.linepos_label = QtGui.QLabel("linepos")
        bottom_stuff.addWidget(self.linepos_label)
        #self.handle_lines_changed()
        self.mousepos_label = QtGui.QLabel("mousepos")
        bottom_stuff.addWidget(self.mousepos_label)
        self.imageplot.scene().sigMouseMoved.connect(self.handle_mouse_move)

        self.set_framerate_limit()

    def setModel(self, device):

        TaurusWidget.setModel(self, device)
        beamviewer = self.getModelObj()

        if self.attr_image:
            # get rid of any old listener
            self.attr_image.removeListener(self.handle_image)
        self.attr_image = beamviewer.getAttribute("VideoImage")
        self.attr_image.addListener(self.handle_image)

        if self.attr_roi:
            self.attr_roi.removeListener(self.handle_roi)
        self.attr_roi = beamviewer.getAttribute("ROI")
        self.attr_roi.addListener(self.handle_roi)

        if self.attr_vline:
            self.attr_vline.removeListener(self.handle_vline)
        self.attr_vline = beamviewer.getAttribute("verticalLine")
        self.attr_vline.addListener(self.handle_vline)

        if self.attr_hline:
            self.attr_hline.removeListener(self.handle_hline)
        self.attr_hline = beamviewer.getAttribute("horizontalLine")
        self.attr_hline.addListener(self.handle_hline)

    def set_framerate_limit(self, fps=None):
        "Limit the image update frequency"
        if fps:
            self.update_interval = 1/float(fps)
            self.update_image = throttle(seconds=self.update_interval)(
                self._update_image)
        else:
            self.update_image = self._update_image

    def handle_image(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            type_, self.image = self.codec.decode(evt_value.value)
            self.image_trigger.emit()

    def _update_image(self):
        self.imageitem.setImage(self.image.T, autoLevels=False,
                                useScale=None, lut=None, autoDownsample=False)

    def show_roi(self, show=True):
        if show:
            self.imageplot.addItem(self.roi)
        else:
            self.imageplot.removeItem(self.roi)

    def handle_roi(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            roi = evt_value.value
            roi = json.loads(roi)
            self.roi_trigger.emit(*roi)

    def update_roi(self, xmin, xmax, ymin, ymax):
        if not self._roi_dragged:
            # in order to not cause circular updates, we prevent sending
            # "finish" events when setting the ROI here
            self.roi.setPos((xmin, ymin), update=False)
            self.roi.setSize((xmax-xmin, ymax-ymin), finish=False)

    def handle_roi_start(self):
        #self._roi_dragged = True
        pass

    def handle_roi_finish(self):
        x, y = self.roi.pos()
        w, h = self.roi.size()
        self.getModelObj().getAttribute("ROI").write(
            [int(x), int(x+w), int(y), int(y+h)])
        self._roi_dragged = False

    def show_lines(self, show=True):
        if show:
            self.imageplot.addItem(self.horline)
            self.imageplot.addItem(self.verline)
        else:
            self.imageplot.removeItem(self.horline)
            self.imageplot.removeItem(self.verline)

    # lots of repetition here; refactor!

    def handle_lines_changed(self):
        x, y = self.verline.value(), self.horline.value()
        self.linepos_label.setText("Lines: x %d, y %d" % (x, y))

    def handle_lines_start(self):
        pass
        #self._line_dragged = True

    def handle_lines_finished(self):
        self.attr_vline.write(self.verline.value())
        self.attr_hline.write(self.horline.value())
        self._line_dragged = False

    def update_vline(self, value):
        self.verline.setValue(value)

    def update_hline(self, value):
        self.horline.setValue(value)

    def handle_hline(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            pos = evt_value.value
            self.hline_trigger.emit(pos)

    def handle_vline(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            pos = evt_value.value
            self.vline_trigger.emit(pos)

    def show_histogram(self, show=True):
        if show:
            self.graphics.addItem(self.hist, row=0, col=1)
        else:
            self.graphics.removeItem(self.hist)

    @throttle(seconds=0.1)
    def handle_mouse_move(self, pos):
        if self.imageplot.sceneBoundingRect().contains(pos):
            mouse_point = self.imageplot.vb.mapSceneToView(pos)
            if (0 <= mouse_point.x() < self.imageitem.width() and
                0 <= mouse_point.y() < self.imageitem.height()):
                self.mousepos_label.setText("Mouse: x %d, y %d" %
                                            (mouse_point.x(), mouse_point.y()))
            else:
                self.mousepos_label.setText("Mouse: -")


def main():
    import sys
    from taurus.qt.qtgui.application import TaurusApplication

    app = TaurusApplication(sys.argv)
    cam = BeamViewerImageWidget()
    cam.setModel(sys.argv[1])
    cam.show()
    app.exec_()


if __name__ == "__main__":
    main()
