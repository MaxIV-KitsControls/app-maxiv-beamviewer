import time
import base64

import numpy as np
import pyqtgraph as pg
from pyqtgraph.opengl import GLImageItem
import PyTango
from taurus.core.util import CodecFactory
from taurus.qt.qtgui.panel import TaurusWidget
from taurus.qt import QtGui, QtCore
from taurus import Attribute, Device

from camera_ui import Ui_Form

pg.setConfigOption('background', (50,50,50))
pg.setConfigOption('foreground', 'w')


def gaussian(x, mu, sig):
    return np.exp(-np.power(x - mu, 2.) / (2 * np.power(sig, 2.)))


def decode_base64_array(data):
    dtype, data = data
    return np.fromstring(base64.b64decode(data), dtype=getattr(np, dtype))
    #return np.fromstring(data, dtype=getattr(np, dtype))


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

    """Widget for showing the imag<3e from a Lima camera"""

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
        #self.imageitem = GLImageItem()
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
        # Note: doesn't actually work, because for some reason the sigRegionChangeStarted event
        # is not fired by the ROI...
        self._roi_locked = True

    def handle_roi_change_finished(self):
        self._roi_locked = False

    def handle_roi_update(self, evt_src, evt_type, evt_value):
        if (not self._roi_locked and
            evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT)):
            self._roidata = evt_value.value
            self.roi_trigger.emit()

    def _update_roi(self):
        roi = self._roidata
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
        if self.data and self.center:
            self.plotdata.setData(x=self.data[0], y=self.data[1], fillLevel=0, brush=(100,100,100,100))
            self.centerline.setPos(self.center)

    def handleEvent(self, evt_src, evt_type, evt_value):
        print "plot event", evt_type
        if evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT):
            data = evt_value.value
            axis = self.bpm.ROI
            xmin, xmax = axis[0], axis[0] + len(data)
            self.plotdata.setData(x=np.arange(xmin, xmax), y=data)
            self.centerline.setPos(min(max(self.bpm.BeamCenterX, xmin), xmax))

class LimaCameraWidget(TaurusWidget):

    limaccd = None
    trigger = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)

        self.ui = Ui_Form()
        self.ui.setupUi(self)
        self.imagewidget = LimaImageWidget()
        self.ui.cameraImageContainer.addWidget(self.imagewidget)
        self.json_codec = CodecFactory().getCodec('JSON')

        self.trigger.connect(self.update_bpm_values)

    def setModel(self, model):
        while True:
            try:
                self.limaccd = Device(model)
                bviewer = self.limaccd.getPluginDeviceNameFromType("beamviewer")
                TaurusWidget.setModel(self, bviewer)
            except AttributeError:
                print "Trying to connect to %s..." % model
                time.sleep(5)
            break

        self.bviewer = self.getModelObj()
        self.bviewer.Start()

        # Camera image
        self.imagewidget.setModel("%s/VideoImage" % bviewer)

        # Acquisition settings
        self.ui.camera_type_label.setText(self.limaccd.camera_type)
        self.ui.acq_expo_time.setModel("%s/Exposure" % bviewer)
        self.ui.gain_label.setModel("%s/Gain" % bviewer)
        self.ui.acq_status_label.setModel("%s/AcqStatus" % bviewer)
        self.ui.acquire_checkbox.setChecked(self.bviewer.AcqStatus == "Running")
        self.ui.acquire_checkbox.stateChanged.connect(self.handle_acquire_images)
        self.allowed_trigger_modes = self.limaccd.getAttrStringValueList("acq_trigger_mode")
        self.ui.trigger_mode_combobox.addValueNames(zip(self.allowed_trigger_modes, self.allowed_trigger_modes))
        self.ui.trigger_mode_combobox.setCurrentIndex(self.bviewer.TriggerMode)
        self.ui.trigger_mode_combobox.currentIndexChanged.connect(self.handle_trigger_mode)
        print self.limaccd.camera_type, type(self.limaccd.camera_type)
        if self.limaccd.camera_type == "Simulator":
            # This is a tamporary fix: If we're using a simulator, set the depth to
            # Bpp16, since teh codec doesn't seem to support 32 bit (default).
            print "simulator"
            self.bviewer.getAttribute("ImageType").write(8)

        #Image settings
        self.ui.image_width_label.setModel("%s/Width" % bviewer)
        self.ui.image_height_label.setModel("%s/Height" % bviewer)
        self.ui.image_bin_spinbox.setValue(self.bviewer.Binning)
        self.ui.image_bin_spinbox.valueChanged.connect(self.handle_image_bin)
        self.allowed_rotations = sorted(self.limaccd.getAttrStringValueList("image_rotation"))
        self.ui.image_rotation_combobox.addValueNames(
            zip(self.allowed_rotations, self.allowed_rotations))
        self.ui.image_rotation_combobox.setCurrentIndex(self.allowed_rotations.index(self.bviewer.Rotation))
        self.ui.image_rotation_combobox.currentIndexChanged.connect(self.handle_rotation)

        # BPM settings
        self.imagewidget.roi.sigRegionChangeFinished.connect(self.set_bpm_roi)
        self.imagewidget.show_roi(True)
        self.bpm_roi = self.bviewer.getAttribute("ROI")
        self.bpm_roi.addListener(self.imagewidget.handle_roi_update)
        self.ui.auto_roi_checkbox.setModel("%s/AutoROI" % bviewer)
        self.ui.bpm_show_position_checkbox.stateChanged.connect(self.handle_bpm_show_position)

        # BPM Beam profiles
        self.xprof = ProfilePlotWidget("Profile X")
        self.ui.bpm_profile_x_layout.addWidget(self.xprof)

        self.yprof = ProfilePlotWidget("Profile Y", y=True)
        self.ui.bpm_profile_y_layout.addWidget(self.yprof)

        # BPM result event listener
        self.bpm_result = self.bviewer.getAttribute("BPMResult")
        self.bpm_result.addListener(self.handle_bpm_result)

    def handle_acquire_images(self, event):
        if event == 2:   # look up this constant
            self.start_acq()
        if event == 0:
            self.stop_acq()

    def start_acq(self):
        """Tell camera to start acquiring images"""
        try:
            self.bviewer.StartAcquisition()
        except PyTango.DevFailed as e:
            print "Trouble starting: %s" % e

    def stop_acq(self):
        """Tell camera to stop acquiring images"""
        self.bviewer.StopAcquisition()

    def handle_rotation(self, n):
        """Change image_rotation"""
        # TODO: might want to also rotate the ROI
        rotation = self.allowed_rotations[n]
        self.stop_acq()
        self.bviewer.getAttribute("Rotation").write(rotation)
        self.start_acq()

    def handle_trigger_mode(self, n):
        """Change image_trigger_mode"""
        # TODO: it is currently possible to select non-allowed trigger modes,
        # although the device server will not write these values.
        self.stop_acq()
        self.bviewer.getAttribute("TriggerMode").write(n)
        self.start_acq()

    def handle_image_bin(self, binning):
        """Change image binning"""
        # Note: We're setting x and y binning the same
        old_binning = self.bviewer.Binning
        self.stop_acq()  # need to stop acq for this to work
        self.bviewer.getAttribute("Binning").write(binning)
        scale = float(old_binning) / binning
        roi_pos = self.imagewidget.roi.pos()
        roi_size = self.imagewidget.roi.size()
        # Update the image widget's ROI to match the new scaling
        if roi_size.x() != 0 and roi_size.y() != 0:
            self.imagewidget.roi.scale(scale, center=(-roi_pos.x() / roi_size.x(),
                                                      -roi_pos.y() / roi_size.y()))
        self.start_acq()

    def handle_bpm_show_position(self, value):
        self.imagewidget.show_crosshair(value)

    def handle_bpm_result(self, evt_src, evt_type, evt_value):
        """Handle result from the Lima BPM calculations"""
        if (evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT)
            and evt_value):
                self.bpm_result = self.json_codec.decode(evt_value.value)[1]
                self.trigger.emit()

    def update_bpm_values(self):
        """Update GUI with BPM results"""
        fmt = "%.2f"
        self.ui.beam_intensity_label.setText(fmt % self.bpm_result["beam_intensity"])
        self.ui.beam_center_x_label.setText(fmt % self.bpm_result["beam_center_x"])
        self.ui.beam_center_y_label.setText(fmt % self.bpm_result["beam_center_y"])
        self.ui.beam_fwhm_x_label.setText(fmt % self.bpm_result["beam_fwhm_x"])
        self.ui.beam_fwhm_y_label.setText(fmt % self.bpm_result["beam_fwhm_y"])
        self.xprof.set_data(self.imagewidget._roidata,
                            decode_base64_array(self.bpm_result["profile_x"]),
                            self.bpm_result["beam_center_x"])
        self.yprof.set_data(self.imagewidget._roidata,
                            decode_base64_array(self.bpm_result["profile_y"]),
                            self.bpm_result["beam_center_y"])
        self.imagewidget.set_crosshair((self.bpm_result["beam_center_x"], self.bpm_result["beam_center_y"]))

    def set_bpm_roi(self, roi):
        """Send the updated ROI to the BPM device."""
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
