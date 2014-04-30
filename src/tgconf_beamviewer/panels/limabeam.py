import base64
from contextlib import contextmanager
import datetime
import json
from math import isnan
import os
import time
try:
    from collections import OrderedDict  # in python 2.7 and up
except ImportError:
    from ordereddict import OrderedDict  # needs to be installed for < 2.7

import numpy as np
import pyqtgraph as pg
import PyTango
from PIL import Image
from taurus.core.util import CodecFactory
from taurus.qt.qtgui.panel import TaurusWidget
from taurus.qt import QtGui, QtCore
from taurus import Attribute, Device

from camera_ui import Ui_Camera

pg.setConfigOption('background', (50, 50, 50))
pg.setConfigOption('foreground', 'w')


def gaussian(x, mu, sig):
    return np.exp(-np.power(x - mu, 2.) / (2 * np.power(sig, 2.)))


def decode_base64_array(data):
    dtype, data = data
    return np.fromstring(base64.b64decode(data), dtype=getattr(np, dtype))


class ImageRectROI(pg.RectROI):

    def __init__(self, xmin, xmax, ymin, ymax, *args, **kwargs):
        pg.RectROI.__init__(self, *args, **kwargs)
        self.xmax = xmax
        self.ymax = ymax
        self.xmin = xmin
        self.ymin = ymin
        self.addScaleHandle([0, 0], [1, 1])

    def set_limits(self, xmin, xmax, ymin, ymax):
        self.xmax = xmax
        self.ymax = ymax
        self.xmin = xmin
        self.ymin = ymin

    def checkPointMove(self, handle, pos, modifiers):
        # Here we should check that the ROI does not stretch
        # outside the image itself, as that does not make sense.
        return True
        #self.xmin <= pos.x() < self.xmax and self.ymin <= pos.y() < self.ymax


class LimaImageWidget(TaurusWidget):

    """Widget for showing the image from a Lima camera"""

    roi_trigger = QtCore.pyqtSignal()
    crosshair_trigger = QtCore.pyqtSignal()
    trigger = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)

        layout = QtGui.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)
        self.setLayout(layout)

        self.imageplot = self.graphics.addPlot(useOpenGL=False)
        self.imageplot.setAspectLocked()
        self.imageplot.invertY()
        self.imageplot.showGrid(x=True, y=True)

        self.imageitem = pg.ImageItem()
        self.imageplot.addItem(self.imageitem)

        # ROI (region of interest)
        self.roi = ImageRectROI(0, 640, 0, 512, [20, 20], [20, 20],
                                pen=(0,9), scaleSnap=True, translateSnap=True)
        self._roidata = (20, 40, 20, 40)
        self._roi_locked = False
        self.roi.sigRegionChangeStarted.connect(self.handle_roi_change_started)  # doesn't work?
        self.roi.sigRegionChangeFinished.connect(self.handle_roi_change_finished)
        self.roi_trigger.connect(self._update_roi)

        self._crosshair = None
        self._crosshair_horline = pg.InfiniteLine(angle=0, movable=False)
        self._crosshair_verline = pg.InfiniteLine(angle=90, movable=False)
        self.show_crosshair(False)
        self.crosshair_trigger.connect(self._show_crosshair)

        # Create a histogram and connect it to the image
        self.hist = pg.HistogramLUTItem(image=self.imageitem)
        # need to be more intelligent here!
        #hist.setHistogramRange(0, 4000)  # range shown
        self.hist.setLevels(0, 4096)  # range selected
        self.show_histogram(True)

        # video codec
        self.codec = CodecFactory().getCodec('VIDEO_IMAGE')
        self.trigger.connect(self._show_image)

        # Display mouse position if it's over the image
        self.mouse_label = pg.LabelItem(justify='left', anchor=(1000,100))
        self.graphics.addItem(self.mouse_label, row=1, col=0, colspan=2)

        # proxy = pg.SignalProxy(self.imageplot.scene().sigMouseMoved,
        #                        rateLimit=60, slot=self.handle_mouse_move)
        self.imageplot.scene().sigMouseMoved.connect(self.handle_mouse_move)

    def handle_mouse_move(self, pos):
        if self.imageplot.sceneBoundingRect().contains(pos):
            mouse_point = self.imageplot.vb.mapSceneToView(pos)
            if (0 <= mouse_point.x() < self.imageitem.width() and
                0 <= mouse_point.y() < self.imageitem.height()):
                self.mouse_label.setText("%d, %d" %
                                         (mouse_point.x(), mouse_point.y()))
            else:
                self.mouse_label.setText("")

    def show_histogram(self, on):
        """Show or hide the image histogram"""
        if on:
            self.graphics.addItem(self.hist, row=0, col=1)
        else:
            self.graphics.removeItem(self.hist)

    def show_roi(self, on):
        """Show or hide the ROI rectangle"""
        if on:
            self.imageplot.addItem(self.roi)
        else:
            self.imageplot.removeItem(self.roi)

    def show_crosshair(self, on):
        self._crosshair_shown = on
        if on:
            self.imageplot.addItem(self._crosshair_horline)
            self.imageplot.addItem(self._crosshair_verline)
        else:
            self.imageplot.removeItem(self._crosshair_horline)
            self.imageplot.removeItem(self._crosshair_verline)

    def set_crosshair(self, crosshair):
        self._crosshair = crosshair
        if self._crosshair_shown:
            self.crosshair_trigger.emit()

    def handleEvent(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT):
            value = evt_value.value
            _type, image = self.codec.decode(value)
            self.image = image
            self.trigger.emit()

    def handle_roi_change_started(self):
        """Make sure nobody updates the ROI while the user is changing it"""
        # Note: doesn't actually work, because for some reason the
        # sigRegionChangeStarted event is not fired by the ROI...
        self._roi_locked = True

    def handle_roi_change_finished(self):
        self._roi_locked = False

    def handle_roi_update(self, evt_src, evt_type, evt_value):
        if (not self._roi_locked and
            evt_type in (PyTango.EventType.PERIODIC_EVENT,
                         PyTango.EventType.CHANGE_EVENT)):
            self._roidata = evt_value.value
            self.roi_trigger.emit()

    def _update_roi(self):
        roi = self._roidata
        if roi[1] == roi[3] == -1:
            roi = 0, self.imageitem.width(), 0, self.imageitem.height()
        self.roi.setPos((roi[0], roi[2]), finish=False)
        self.roi.setSize((roi[1]-roi[0], roi[3]-roi[2]), finish=False)

    def _show_crosshair(self):
        self._crosshair_horline.setPos(int(self._crosshair[1]))
        self._crosshair_verline.setPos(int(self._crosshair[0]))

    def _show_image(self):
        self.imageitem.setImage(self.image.T, autoLevels=False,
                                border=pg.mkPen(color=(200, 200, 255),
                                                style=QtCore.Qt.DotLine))


class ProfilePlotWidget(TaurusWidget):

    """A plot widget specifically for displaying a BPM profile."""

    trigger = QtCore.pyqtSignal()

    def __init__(self, title=None, parent=None, y=False):

        TaurusWidget.__init__(self, parent)

        layout = QtGui.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(graphics)
        self.setLayout(layout)
        self.plot = graphics.addPlot()
        self.plotdata = pg.PlotDataItem(fillLevel=0)
        self.plot.addItem(self.plotdata)
        self.y = y
        if self.y:
            self.centerline = pg.InfiniteLine(angle=0, movable=False)
            self.plotdata.rotate(-90)
            self.plot.invertY()
            self.plot.showGrid(y=True)
            self.plot.hideAxis("bottom")
        else:
            self.centerline = pg.InfiniteLine(angle=90, movable=False)
            self.plot.showGrid(x=True)
            self.plot.hideAxis("left")
        self.plot.addItem(self.centerline)
        if title:
            self.plot.setTitle(title)
        self.data = None
        self.center = None
        self.trigger.connect(self._show_graph)

    def set_data(self, roi, data, center):
        if self.y:
            ymin, ymax = roi[2], roi[2] + len(data)
            self.data = (-np.arange(ymin, ymax), data)
            self.center = min(max(center, ymin), ymax)
        else:
            xmin, xmax = roi[0], roi[0] + len(data)
            self.data = (np.arange(xmin, xmax), data)
            self.center = min(max(center, xmin), xmax)
        self.trigger.emit()

    def _show_graph(self):
        if self.data and self.center and not isnan(self.center):  # why nan?
            self.plotdata.setData(x=self.data[0], y=self.data[1],
                                  fillLevel=0, brush=(100, 100, 100, 100))
            self.centerline.setPos(self.center)

    def handleEvent(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT,
                        PyTango.EventType.CHANGE_EVENT):
            data = evt_value.value
            axis = self.bpm.ROI
            xmin, xmax = axis[0], axis[0] + len(data)
            self.plotdata.setData(x=np.arange(xmin, xmax), y=data)
            self.centerline.setPos(min(max(self.bpm.BeamCenterX, xmin), xmax))


@contextmanager
def acquisition_stopped(camera):
    """A context manager to temporarily stop the camera."""
    was_running = False
    if camera.acq_status.read().value == "Running":
        was_running = True
        camera.stop_acq()
    yield
    if was_running:
        camera.start_acq()


class LimaCameraWidget(TaurusWidget):

    limaccd = None
    trigger = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)

        self.ui = Ui_Camera()
        self.ui.setupUi(self)
        self.ui.splitter.setSizes([10000, 1])  # set the splitter weights
        self.imagewidget = LimaImageWidget()
        self.ui.camera_image_widget.layout().addWidget(self.imagewidget)
        self.json_codec = CodecFactory().getCodec('JSON')
        self._save_path = ""

        self.bviewer = None

        self.trigger.connect(self.update_bpm_values)
        self.bpm_roi = None
        self.bpm_result = None
        self.acq_status = None

        self.ui.start_acquisition_button.clicked.connect(self.start_acq)
        self.ui.stop_acquisition_button.clicked.connect(self.stop_acq)
        self.ui.trigger_mode_combobox.currentIndexChanged.connect(
            self.handle_trigger_mode)

        self.ui.image_bin_spinbox.valueChanged.connect(self.handle_image_bin)
        self.ui.image_save_button.clicked.connect(self.handle_save)
        self.ui.image_rotation_combobox.currentIndexChanged.connect(
            self.handle_rotation)

        self.imagewidget.roi.sigRegionChangeFinished.connect(self.set_bpm_roi)
        self.ui.bpm_show_position_checkbox.stateChanged.connect(
            self.handle_bpm_show_position)
        self.xprof = ProfilePlotWidget("Profile X")
        self.ui.bpm_profile_x_layout.addWidget(self.xprof)
        self.yprof = ProfilePlotWidget("Profile Y", y=True)
        self.ui.bpm_profile_y_layout.addWidget(self.yprof)

    def setModel(self, model):

        # If we're switching cameras, we first stop the previous one
        if self.bviewer:
            self.stop_acq()

        self._devicename = model
        self.limaccd = Device(str(model))
        bviewer = self.limaccd.getPluginDeviceNameFromType("beamviewer")
        TaurusWidget.setModel(self, bviewer)

        self.bviewer = self.getModelObj()
        self.acq_status = self.bviewer.getAttribute("AcqStatus")

        # Camera image
        self.imagewidget.setModel("%s/VideoImage" % bviewer)

        # Acquisition settings
        self.ui.camera_type_label.setText(self.limaccd.camera_type)
        self.ui.acq_expo_time.setModel("%s/Exposure" % bviewer)
        self.ui.gain_label.setModel("%s/Gain" % bviewer)
        self.ui.acq_status_label.setModel("%s/AcqStatus" % bviewer)

        #self.allowed_trigger_modes = self.limaccd.getAttrStringValueList("acq_trigger_mode")
        self.allowed_trigger_modes = ["INTERNAL_TRIGGER", "EXTERNAL_TRIGGER"]
        self.ui.trigger_mode_combobox.blockSignals(True)
        self.ui.trigger_mode_combobox.setValueNames(zip(self.allowed_trigger_modes, self.allowed_trigger_modes))
        self.ui.trigger_mode_combobox.setCurrentIndex(0 if self.bviewer.TriggerMode == 0 else 1)
        self.ui.trigger_mode_combobox.blockSignals(False)

        if self.limaccd.camera_type == "Simulator":
            # This is a tamporary fix: If we're using a simulator, set the depth to
            # Bpp16, since teh codec doesn't seem to support 32 bit (default).
            self.bviewer.getAttribute("ImageType").write(8)

        # Image settings
        self.ui.image_width_label.setModel("%s/Width" % bviewer)
        self.ui.image_height_label.setModel("%s/Height" % bviewer)
        self.ui.image_bin_spinbox.blockSignals(True)
        self.ui.image_bin_spinbox.setValue(self.bviewer.Binning)
        self.ui.image_bin_spinbox.blockSignals(False)

        self.allowed_rotations = sorted(self.limaccd.getAttrStringValueList("image_rotation"))
        self.ui.image_rotation_combobox.blockSignals(True)
        self.ui.image_rotation_combobox.setValueNames(
            zip(self.allowed_rotations, self.allowed_rotations))
        self.ui.image_rotation_combobox.setCurrentIndex(self.allowed_rotations.index(self.bviewer.Rotation))
        self.ui.image_rotation_combobox.blockSignals(False)

        # BPM settings
        self.imagewidget.show_roi(True)
        if self.bpm_roi:
            # disconnect any previous listener
            self.bpm_roi.removeListener(self.imagewidget.handle_roi_update)
        self.bpm_roi = self.bviewer.getAttribute("ROI")
        self.bpm_roi.addListener(self.imagewidget.handle_roi_update)

        # TODO: Need to update the ROI when changing camera somehow
        # self.imagewidget._roidata = self.bpm_roi.read()
        # self.imagewidget._update_roi()

        self.ui.auto_roi_checkbox.setModel("%s/AutoROI" % bviewer)

        # BPM result event listener
        if self.bpm_result:
            # disconnect any previous listener
            self.bpm_result.removeListener(self.handle_bpm_result)
        self.bpm_result = self.bviewer.getAttribute("BPMResult")
        self.bpm_result.addListener(self.handle_bpm_result)

    def get_metadata(self):
        "Collect various data about the latest image"

        metadata = OrderedDict()
        timestamp = self._bpm_result.get("timestamp")
        if timestamp is None:
            timestamp = time.time()
        metadata["date"] = datetime.datetime.fromtimestamp(timestamp)\
                                            .strftime('%Y-%m-%d %H:%M:%S')
        metadata["camera_device"] = self._devicename
        metadata["camera_type"] = self.limaccd.camera_type

        metadata["width"] = {"value": self.bviewer.Width, "unit": "pixels"}
        metadata["height"] = {"value": self.bviewer.Height, "unit": "pixels"}
        metadata["acquisition_time"] = {"value": self.bviewer.Exposure,
                                        "unit": "ms"}
        try:
            rotation = int(self.bviewer.Rotation)
        except ValueError:
            rotation = 0
        metadata["rotation"] = {"value": rotation, "unit": "degrees"}
        metadata["gain"] = {"value": self.bviewer.Gain}
        metadata["binning"] = {"value": self.bviewer.Binning}
        metadata["trigger_mode"] = {"value": self.bviewer.TriggerMode}
        metadata["bpm_result"] = self._bpm_result

        return metadata

    def start_acq(self):
        """Tell camera to start acquiring images"""
        try:
            self.bviewer.Start()
            self.bviewer.StartAcquisition()
        except PyTango.DevFailed as e:
            print "Trouble starting: %s" % e

    def stop_acq(self):
        """Tell camera to stop acquiring images"""
        self.bviewer.StopAcquisition()
        self.bviewer.Stop()

    def handle_rotation(self, n):
        """Change image_rotation"""
        # TODO: might want to also rotate the ROI to follow the image
        rotation = self.allowed_rotations[n]
        with acquisition_stopped(self):
            self.bviewer.getAttribute("Rotation").write(rotation)

    def handle_trigger_mode(self, n):
        """Change image_trigger_mode"""
        mode = 0 if n == 0 else 2  # Internal = 0, External = 2
        with acquisition_stopped(self):
            self.bviewer.getAttribute("TriggerMode").write(mode)

    def handle_image_bin(self, binning):
        """Change image binning"""
        # Note: We're always setting x and y binning the same
        old_binning = self.bviewer.Binning
        with acquisition_stopped(self):
            self.bviewer.getAttribute("Binning").write(binning)
            scale = float(old_binning) / binning
            roi_pos = self.imagewidget.roi.pos()
            roi_size = self.imagewidget.roi.size()
            # Update the image widget's ROI to match the new scaling
        if roi_size.x() != 0 and roi_size.y() != 0:
            self.imagewidget.roi.scale(
                scale, center=(-roi_pos.x() / roi_size.x(),
                               -roi_pos.y() / roi_size.y()))

    def handle_save(self):

        "Save the current image to disk, along with its metadata"

        metadata = self.get_metadata()
        im = Image.fromarray((self.imagewidget.image * 2**4).astype(np.int32))  # 12 bits

        camera = metadata["camera_device"].split("/")[-1]
        if "date" in metadata:
            date = metadata["date"].replace(" ", "_")
        else:
            date = "unknown"
        default = os.path.join(self._save_path,
                               "image_%s_%s.png" % (camera, date))

        filename = str(QtGui.QFileDialog.getSaveFileName(
            self, "Save Image", default, "Image files (*.png)"))
        if not filename.lower().endswith(".png"):
            filename += ".png"

        im.save(str(filename), "PNG")
        self._save_path, name = os.path.split(filename)
        metadata["image_filename"] = name

        metadata_filename = os.path.splitext(filename)[0] + ".json"
        with open(metadata_filename, "w") as f:
            json.dump(metadata, f, indent=4)

    def handle_bpm_show_position(self, value):
        self.imagewidget.show_crosshair(value)

    def handle_bpm_result(self, evt_src, evt_type, evt_value):
        """Handle result from the Lima BPM calculations"""
        if (evt_type in (PyTango.EventType.PERIODIC_EVENT,
                         PyTango.EventType.CHANGE_EVENT) and evt_value):
                self._bpm_result = self.json_codec.decode(evt_value.value)[1]
                self.trigger.emit()

    def update_bpm_values(self):
        """Update GUI with BPM results"""
        fmt = "%.2f"
        self.ui.beam_intensity_label.setText(fmt % self._bpm_result["beam_intensity"])
        self.ui.beam_center_x_label.setText(fmt % self._bpm_result["beam_center_x"])
        self.ui.beam_center_y_label.setText(fmt % self._bpm_result["beam_center_y"])
        self.ui.beam_fwhm_x_label.setText(fmt % self._bpm_result["beam_fwhm_x"])
        self.ui.beam_fwhm_y_label.setText(fmt % self._bpm_result["beam_fwhm_y"])
        self.xprof.set_data(self.imagewidget._roidata,
                            decode_base64_array(self._bpm_result["profile_x"]),
                            self._bpm_result["beam_center_x"])
        self.yprof.set_data(self.imagewidget._roidata,
                            decode_base64_array(self._bpm_result["profile_y"]),
                            self._bpm_result["beam_center_y"])
        self.imagewidget.set_crosshair((self._bpm_result["beam_center_x"], self._bpm_result["beam_center_y"]))

    def set_bpm_roi(self, roi):
        """Send the updated ROI to the BPM device."""
        if self.bpm_roi:
            state = roi.getState()
            pos = state["pos"]
            size = state["size"]
            x, y, w, h = (int(round(a)) for a in (max(0, pos.x()), max(0, pos.y()),
                                                  min(self.bviewer.Width - pos.x(), size.x()),
                                                  min(self.bviewer.Height - pos.y(), size.y())))
            self.bpm_roi.write([x, x+w, y, y+h])
            self._roidata = (x, x+h, y, y+w)
            self.ui.roi_label.setText("x: %d, y: %d, w: %d, h: %d" % (x, y, w, h))


def main():
    import sys
    from taurus.qt.qtgui.application import TaurusApplication

    app = TaurusApplication(sys.argv)
    cam = LimaCameraWidget()
    cam.setModel(sys.argv[1])
    cam.show()
    app.exec_()

if __name__ == "__main__":
    main()
