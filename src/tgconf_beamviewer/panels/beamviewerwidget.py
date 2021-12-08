import base64
from datetime import datetime, timedelta
import json
from functools import wraps, partial

from taurus import Attribute, Device
try:
    from taurus.qt.qtgui.panel import TaurusWidget
except ImportError:
    from taurus.qt.qtgui.container import TaurusWidget
try:
    from taurus.qt import QtGui, QtCore
except ImportError:
    from taurus.external.qt import QtGui, QtCore
from taurus.core.util import CodecFactory
from pyqtgraph.Point import *
import pyqtgraph as pg

import PyTango

from tgconf_beamviewer.panels.util import throttle

pg.setConfigOption('background', (50, 50, 50))
pg.setConfigOption('foreground', 'w')


class BeamViewerImageWidget(TaurusWidget):

    image_trigger = QtCore.pyqtSignal(int)
    roi_trigger = QtCore.pyqtSignal(int, int, int, int)
    vline_trigger = QtCore.pyqtSignal(int)
    hline_trigger = QtCore.pyqtSignal(int)
    ruler_trigger = QtCore.pyqtSignal()
    ruler_calibration_trigger = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)
        self._setup_ui()

        self.codec = CodecFactory().getCodec("VIDEO_IMAGE")
        self.image_trigger.connect(self.update_image_wrapper)

        self.image = None

        self.json_codec = CodecFactory().getCodec('JSON')

        self.attr_image = None
        self.attr_vline = self.attr_hline = None
        self.attr_roi = None
        self.attr_framenumber = None
        self.attr_measurementruler = None
        self.attr_measurementrulerwidth = None
        self.attr_measurementrulerheight = None

        self.use_calibration(True)

    def _setup_ui(self):
        self.layout = QtGui.QVBoxLayout(self)
        self.setLayout(self.layout)

        self.graphics = pg.GraphicsLayoutWidget()
        self.layout.addWidget(self.graphics)

        self.imageplot = self.graphics.addPlot()
        self.imageplot.setAspectLocked()
        self.imageplot.invertY()
        self.imageplot.showGrid(x=True, y=True)
        self.imageplot.getAxis("bottom").setLabel("Pixels")
        self.imageplot.getAxis("left").setLabel("Pixels")

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
        self.verline.sigPositionChanged.connect(self.update_linepos_label)
        self.verline.sigPositionChangeFinished.connect(self.handle_lines_finished)
        self.horline = pg.InfiniteLine(pos=(100, 100), angle=0, movable=True)
        self.horline.sigPositionChanged.connect(self.update_linepos_label)
        self.horline.sigPositionChangeFinished.connect(self.handle_lines_finished)
        self._line_dragged = False
        self.vline_trigger.connect(self.update_vline)
        self.hline_trigger.connect(self.update_hline)

        # Histogram
        self.hist = pg.HistogramLUTItem(image=self.imageitem,
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

        self.ruler_checkbox = QtGui.QCheckBox("Calib")
        self.ruler_checkbox.stateChanged.connect(self.show_ruler)
        bottom_stuff.addWidget(self.ruler_checkbox)

        self.linepos_label = QtGui.QLabel("linepos")
        bottom_stuff.addWidget(self.linepos_label, stretch=2,
                               alignment=QtCore.Qt.AlignCenter)

        #self.handle_lines_changed()
        self.mousepos_label = QtGui.QLabel("mousepos")
        bottom_stuff.addWidget(self.mousepos_label, stretch=2,
                               alignment=QtCore.Qt.AlignCenter)
        self.imageplot.scene().sigMouseMoved.connect(self.handle_mouse_move)

        # measurement ruler
        self.ruler = pg.RectROI((0, 0), (100, 100), movable=False, pen=(0, 255, 0))
        self.ruler.addScaleHandle(pos=(0, 0), center=(1, 1))
        self.ruler.addScaleHandle(pos=(1, 0), center=(0, 1))
        self.ruler.addScaleHandle(pos=(0, 1), center=(1, 0))
        self.ruler.addTranslateHandle(pos=(0.5, 0.5))
        self.ruler.sigRegionChanged.connect(self.handle_ruler_start)
        self.ruler.sigRegionChangeFinished.connect(self.handle_ruler_changed)
        self.ruler_trigger.connect(self.update_ruler)
        self.ruler_calibration_trigger.connect(self.calibrate_axes)
        self._ruler_dragged = False
        self._ruler = None
        self._ruler_calibration = [5.0, 5.0]
        #self.show_ruler()
        self.center = (0., 0.)
        self.scale = (1., 1.)

        self.set_framerate_limit(10)

    def contextMenuEvent(self,event):
        # Note: This is needed in order for the widget to accept right clicks (e.g. menu).
        # Otherwise, taurus eats them.
        event.accept()

    def use_calibration(self, value=True):
        self._use_calibration = value
        self._point_format = "[mm]: %.3f, %.3f" if value else "[px]: %d, %d"

    def setModel(self, bviewer):

        #self.limaccd = Device(str(device))
        #bviewer = self.limaccd.getPluginDeviceNameFromType("beamviewer")

        TaurusWidget.setModel(self, bviewer)
        self.beamviewer = self.getModelObj()

        if self.attr_framenumber:
            # get rid of any old listener
            self.attr_framenumber.removeListener(self.handle_framenumber)
        self.attr_framenumber = self.beamviewer.getAttribute("FrameNumber")
        self.attr_framenumber.addListener(self.handle_framenumber)

        if self.attr_roi:
            self.attr_roi.removeListener(self.handle_roi)
        self.attr_roi = self.beamviewer.getAttribute("ROI")
        self.attr_roi.addListener(self.handle_roi)

        if self.attr_vline:
            self.attr_vline.removeListener(self.handle_vline)
        self.attr_vline = self.beamviewer.getAttribute("verticalLine")
        self.attr_vline.addListener(self.handle_vline)

        if self.attr_hline:
            self.attr_hline.removeListener(self.handle_hline)
        self.attr_hline = self.beamviewer.getAttribute("horizontalLine")
        self.attr_hline.addListener(self.handle_hline)

        if self.attr_measurementruler:
            self.attr_measurementruler.removeListener(self.handle_ruler)
        self.attr_measurementruler = self.beamviewer.getAttribute("measurementRuler")
        self.attr_measurementruler.addListener(self.handle_ruler)

        if self.attr_measurementrulerwidth:
            self.attr_measurementrulerwidth.removeListener(self.handle_ruler_calibration)
        self.attr_measurementrulerwidth = self.beamviewer.getAttribute("measurementRulerWidth")
        self.attr_measurementrulerwidth.addListener(self.handle_ruler_calibration)

        if self.attr_measurementrulerheight:
            self.attr_measurementrulerheight.removeListener(self.handle_ruler_calibration)
        self.attr_measurementrulerheight = self.beamviewer.getAttribute("measurementRulerHeight")
        self.attr_measurementrulerheight.addListener(self.handle_ruler_calibration)

        # read image if possible
        self._update_image()

    def set_framerate_limit(self, fps=None):
        "Limit the image update frequency"
        if fps:
            self.update_interval = 1/float(fps)
            self.update_image = throttle(seconds=self.update_interval)(
                self._update_image)
        else:
            self.update_image = self._update_image

    def update_image_wrapper(self, frame_number):
        self.update_image(frame_number)

    def _update_image(self, frame_number=-1):
        try:
            imagedata = self.beamviewer.GetImage(frame_number)
            type_, self.image = self.codec.decode(imagedata)
            self.imageitem.setImage(self.image.T, autoLevels=False, autoDownsample=True)
        except PyTango.DevFailed:
            pass

    def handle_framenumber(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            # The idea is to inform the widget that a new image is available, and
            # then allow it to choose whether to actually read it, depending on
            # max FPS settings, etc. This seems more efficient than sending the
            # images themselves around as events whether anyone needs them or not.
            frame_number = evt_value.value
            self.image_trigger.emit(frame_number)

    def show_roi(self, show=True):
        if show:
            self.imageplot.addItem(self.roi)
        else:
            self.imageplot.removeItem(self.roi)

    def handle_roi(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT,):
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
        self._roi_dragged = True

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

    def update_linepos_label(self):
        pos = self.verline.value(), self.horline.value()
        x, y = self.convert_coord(pos)
        self.linepos_label.setText("Lines %s" % self._point_format % (x, y))

    def handle_lines_start(self):
        self._line_dragged = True

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
            if not self._line_dragged:
                self.hline_trigger.emit(pos)

    def handle_vline(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            pos = evt_value.value
            if not self._line_dragged:
                self.vline_trigger.emit(pos)

    def show_histogram(self, show=True):
        if show:
            self.graphics.addItem(self.hist, row=0, col=1)
        else:
            self.graphics.removeItem(self.hist)

    def show_ruler(self, show=True):
        if show:
            self.imageplot.addItem(self.ruler)
        else:
            self.imageplot.removeItem(self.ruler)

    def handle_ruler_start(self):
        self._ruler_dragged = True

    def handle_ruler_changed(self):
        print ("handle_ruler_changed")
        self._ruler_dragged = False
        self._ruler = self.ruler.saveState()
        self.calibrate_axes()
        self.update_linepos_label()
        self.beamviewer.write_attribute("measurementRuler", json.dumps(self._ruler))

    def handle_ruler(self, evt_src, evt_type, evt_data):
        if not self._ruler_dragged and evt_type in (PyTango.EventType.PERIODIC_EVENT,
                                                    PyTango.EventType.CHANGE_EVENT):
            self._ruler = json.loads(evt_data.value)
            if "pos" not in self._ruler:
                print ("Initializing ruler for first time")
                x1, y1 = self._ruler[0]
                w, h = self._ruler[1]
                self._ruler = {"angle": 0.0,
                               "pos": [x1, y1],
                               "size": [w, h]}
            self.ruler_trigger.emit()

    def update_ruler(self):
        if not self._ruler_dragged:
            self.ruler.setState(self._ruler)
            self.calibrate_axes()

    def handle_ruler_calibration(self, evt_src, evt_type, evt_data):
        if not self._ruler_dragged and evt_type in (PyTango.EventType.PERIODIC_EVENT,
                                                    PyTango.EventType.CHANGE_EVENT):
            print ("ruler calibration changed", evt_src.name, evt_data.value)
            if evt_src.name.endswith("Width"):
                self._ruler_calibration[0] = evt_data.value
            elif evt_src.name.endswith("Height"):
                self._ruler_calibration[1] = evt_data.value
            self.ruler_calibration_trigger.emit()

    def calibrate_axes(self):
        if self._ruler:
            x1, y1 = self._ruler["pos"]
            w, h = self._ruler["size"]
            rw, rh = self._ruler_calibration

            xscale = rw / w
            yscale = rh / h
            self.scale = (xscale, yscale)

            self.center = (x1 + w/2., y1 + h/2.)

    def convert_coord(self, pos):
        if self._use_calibration:
            x, y = pos
            cx, cy = self.center
            sx, sy = self.scale
            return (x - cx)*sx, (y - cy)*sy
        return pos

    # def _calibrate_axes(self):

    #     #oldpos = self.imageitem.pos()  # get the previous offset

    #     x1, y1 = self._ruler["pos"]
    #     #x1 -= oldpos.x()
    #     #y1 -= oldpos.y()
    #     w, h = self._ruler["size"]
    #     rw, rh = self._ruler_calibration["size"]

    #     # scale the axes
    #     xscale = rw / w
    #     yscale = rh / h
    #     self.imageplot.getAxis("bottom").setScale(xscale)
    #     self.imageplot.getAxis("left").setScale(yscale)

    #     # reposition the image, to get the origin at the right place
    #     xoffset = x1 + w/2.
    #     yoffset = y1 + h/2.
    #     self.imageitem.setPos(-xoffset, -yoffset)

    #     # reposition the ruler
    #     self.ruler.setPos((-w/2., -h/2.), finish=False)

    #     roix, roiy = self._roi[0]
    #     self.roi.setPos((roix-xoffset, roiy-yoffset), finish=False)

    #     # recenter the view
    #     newpos = self.imageitem.pos()
    #     vbox = self.imageplot.getViewBox()
    #     vbox.translateBy(t=((newpos.x() - oldpos.x()), (newpos.y() - oldpos.y())))

    @throttle(seconds=0.1)
    def handle_mouse_move(self, pos):
        if self.imageplot.sceneBoundingRect().contains(pos):
            mouse_point = self.imageplot.vb.mapSceneToView(pos)
            if (0 <= mouse_point.x() < self.imageitem.width() and
                    0 <= mouse_point.y() < self.imageitem.height()):
                x, y = self.convert_coord((mouse_point.x(), mouse_point.y()))
                self.mousepos_label.setText("Mouse %s" % self._point_format % (x, y))

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
