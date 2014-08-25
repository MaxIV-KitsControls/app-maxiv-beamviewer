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
from beamviewerwidget import BeamViewerImageWidget

from util import throttle

pg.setConfigOption('background', (50, 50, 50))
pg.setConfigOption('foreground', 'w')


def gaussian(x, mu, sig):
    return np.exp(-np.power(x - mu, 2.) / (2 * np.power(sig, 2.)))


def decode_base64_array(data):
    if data:
        dtype, data = data
        return np.fromstring(base64.b64decode(data), dtype=getattr(np, dtype))
    return np.array([1,2,3])


class ProfilePlotWidget(TaurusWidget):

    """A plot widget specifically for displaying a BPM profile."""

    trigger = QtCore.pyqtSignal()

    def __init__(self, title=None, parent=None, y=False):

        TaurusWidget.__init__(self, parent)

        layout = QtGui.QVBoxLayout(self)
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
    bpm_trigger = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)

        self.ui = Ui_Camera()
        self.ui.setupUi(self)
        self.ui.splitter.setSizes([10000, 1])  # set the splitter weights
        self.imagewidget = BeamViewerImageWidget()
        self.ui.camera_image_widget.layout().addWidget(self.imagewidget)
        self.json_codec = CodecFactory().getCodec('JSON')
        self._save_path = ""
        self._bpm_result = {}

        self.bviewer = None

        self.bpm_trigger.connect(self.update_bpm_values_wrapper)
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
        self._show_beam_position = False
        self.xprof = ProfilePlotWidget("Profile X")
        self.ui.bpm_profile_x_layout.addWidget(self.xprof)
        self.yprof = ProfilePlotWidget("Profile Y", y=True)
        self.ui.bpm_profile_y_layout.addWidget(self.yprof)

        self.ui.max_framerate_spinbox.setValue(10)
        self.ui.max_framerate_spinbox.setMinimum(1)
        self.ui.max_framerate_spinbox.valueChanged.connect(self.imagewidget.set_framerate_limit)
        self.set_framerate_limit(10)
        self.ui.max_framerate_spinbox.valueChanged.connect(self.set_framerate_limit)

    def setModel(self, model):

        # If we're switching cameras, we first stop the previous one
        if self.bviewer:
            self.stop_acq()

        self._devicename = model
        self.limaccd = Device(str(model))
        bviewer = self.bviewer = self.limaccd.getPluginDeviceNameFromType("beamviewer")
        TaurusWidget.setModel(self, bviewer)

        self.bviewer = self.getModelObj()
        self.acq_status = self.bviewer.getAttribute("AcqStatus")

        self.ui.device_label.setText(model)
        self.ui.state_tlabel.setModel("%s/State" % model)
        self.ui.status_tlabel.setModel("%s/Status" % model)

        # Camera image
        self.imagewidget.setModel(model)

        # Acquisition settings
        self.ui.camera_type_label.setText(self.limaccd.camera_type)
        self.ui.camera_model_label.setText(self.limaccd.camera_model)
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

        self.ui.auto_roi_checkbox.setModel("%s/AutoROI" % bviewer)

        # BPM result event listener
        if self.bpm_result:
            # disconnect any previous listener
            self.bpm_result.removeListener(self.handle_bpm_result)
        self.frame_number = self.bviewer.getAttribute("FrameNumber")
        self.frame_number.addListener(self.handle_frame_number)

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

        # Because the image is 12 bit (assuming we're dealing with a Basler camera, there
        # is some logic needed here for the general case) we multiply it by 2^4 in order
        # to get the dynamic range right. This should not be necessary in principle but it
        # should make the images easier to view with arbitrary programs.
        scale_factor = 2*4
        scaled_image = self.imagewidget.image * scale_factor

        # Under PIL 1.1.6 the "mode" flag of asarray is needed, or the image will be garbage.
        # Pillow 2.5.1 does not have this problem, but let's keep the flag to be safe.
        im = Image.fromarray((scaled_image).astype(np.int32), mode="I")

        metadata = self.get_metadata()
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

        # save image as 16 bit grayscale PNG
        im.save(str(filename), "PNG")

        self._save_path, name = os.path.split(filename)
        metadata["image_filename"] = name

        # save metadata as JSON
        metadata_filename = os.path.splitext(filename)[0] + ".json"
        with open(metadata_filename, "w") as f:
            json.dump(metadata, f, indent=4)

    def handle_bpm_show_position(self, value):
        self._show_beam_position = value

    def handle_frame_number(self, evt_src, evt_type, evt_data):
        if (evt_type in (PyTango.EventType.PERIODIC_EVENT,
                         PyTango.EventType.CHANGE_EVENT) and evt_data):
            frame_number = evt_data.value
            self.bpm_trigger.emit(frame_number)

    def set_framerate_limit(self, fps=None):
        "Limit the BPM update frequency"
        if fps:
            self.update_interval = 1/float(fps)
            self.update_bpm_values = throttle(seconds=self.update_interval)(
                self._update_bpm_values)
        else:
            self.update_bpm_values = self._update_bpm_values

    def update_bpm_values_wrapper(self, frame_number):
        self.update_bpm_values(frame_number)

    def _update_bpm_values(self, frame_number):
        """Update GUI with BPM results"""

        self.ui.acq_framenumber_label.setText(str(frame_number))

        bpm_result = self.bviewer.GetBPMResult(frame_number)
        self._bpm_result = self.json_codec.decode(bpm_result)[1]

        fmt = "%.2f"
        self.ui.roi_label.setText(str(self._bpm_result.get("roi")))
        self.ui.beam_intensity_label.setText(
            fmt % self._bpm_result.get("beam_intensity", 0))
        self.ui.beam_center_x_label.setText(
            fmt % self._bpm_result.get("beam_center_x", 0))
        self.ui.beam_center_y_label.setText(
            fmt % self._bpm_result.get("beam_center_y", 0))
        self.ui.beam_fwhm_x_label.setText(
            fmt % self._bpm_result.get("beam_fwhm_x", 0))
        self.ui.beam_fwhm_y_label.setText(
            fmt % self._bpm_result.get("beam_fwhm_y", 0))
        self.xprof.set_data(
            self._bpm_result["roi"],
            decode_base64_array(self._bpm_result.get("profile_x", 0)),
            self._bpm_result.get("beam_center_x", 0))
        self.yprof.set_data(
            self._bpm_result["roi"],
            decode_base64_array(self._bpm_result.get("profile_y", 0)),
            self._bpm_result.get("beam_center_y", 0))
        if self._show_beam_position:
            x = self._bpm_result.get("beam_center_x", 0)
            y = self._bpm_result.get("beam_center_y", 0)
            self.imagewidget.update_vline(x)
            self.imagewidget.update_hline(y)
            self.imagewidget.handle_lines_finished()

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
