import time
import base64

import numpy as np
import pyqtgraph as pg
import PyTango
from taurus.core.util import CodecFactory
from taurus.qt.qtgui.panel import TaurusWidget
from taurus.qt import QtGui, QtCore
from taurus import Attribute, Device

from camera_ui import Ui_Form


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

    def checkPointMove(self, handle, pos, modifiers):
        return True


class LimaImageWidget(TaurusWidget):

    trigger = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        TaurusWidget.__init__(self, parent)

        layout = QtGui.QVBoxLayout()
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics)
        self.setLayout(layout)

        self.imageplot = self.graphics.addPlot()
        self.imageplot.setAspectLocked()
        self.imageplot.invertY()
        self.imageplot.showGrid(x=True, y=True)

        self.imageitem = pg.ImageItem()
        self.imageplot.addItem(self.imageitem)

        # ROI (region of interest)
        self.roi = ImageRectROI(0, 0, 1, 1, [20, 20], [20, 20],
                                pen=(0,9), scaleSnap=True, translateSnap=True)
        self._roidata = (20, 40, 20, 40)

        # Create a histogram and connect it to the image
        self.hist = pg.HistogramLUTItem(image=self.imageitem)
        # need to be more intelligent here!
        #hist.setHistogramRange(0, 4000)  # range shown
        self.hist.setLevels(0, 4096)  # range selected
        self.show_histogram(True)

        # video codec
        self.codec = CodecFactory().getCodec('VIDEO_IMAGE')
        self.trigger.connect(self.show_image)

        # Display mouse position if it's over the image
        self.mouse_label = pg.LabelItem(justify='left', anchor=(1000,100))
        self.graphics.addItem(self.mouse_label, row=1, col=0, colspan=2)

        # proxy = pg.SignalProxy(self.imageplot.scene().sigMouseMoved, rateLimit=60,
        #                        slot=self.handle_mouse_move)
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

    # def setModel(self, model):
    #     img_attr = Attribute(model)
    #     img_attr.addListener(self.handle_image_update)

    def handleEvent(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT):
            value = evt_value.value
            _type, image = self.codec.decode(value)
            self.image = image
            self.trigger.emit()

    def handle_roi_update(self, evt_src, evt_type, evt_value):
        if evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT):
            self._roidata = roi = evt_value.value
            self.roi.setPos((roi[0], roi[2]), finish=False)
            self.roi.setSize((roi[1]-roi[0], roi[3]-roi[2]), finish=False)

    def show_image(self):
        self.imageitem.setImage(self.image.T,
                                border=pg.mkPen(color=(200, 200, 255),
                                                style=QtCore.Qt.DotLine), autoLevels=False)


class ProfilePlotWidget(TaurusWidget):

    trigger = QtCore.pyqtSignal()

    def __init__(self, parent=None, y=False):

        TaurusWidget.__init__(self, parent)

        layout = QtGui.QVBoxLayout()
        graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(graphics)
        self.setLayout(layout)
        self.plot = graphics.addPlot()
        self.plotdata = pg.PlotDataItem()
        self.plot.addItem(self.plotdata)
        self.y = y
        if self.y:
            self.centerline = pg.InfiniteLine(angle=0, movable=False)
            self.plot.invertY()
            self.plot.showGrid(y=True)
        else:
            self.centerline = pg.InfiniteLine(angle=90, movable=False)
            self.plot.showGrid(x=True)
        self.plot.addItem(self.centerline)
        #TaurusWidget.setModel(self, model)
        #self.bpm = Device(bpm)
        #self.plot.setTitle(attr)
        self.data = None
        self.center = None
        self.trigger.connect(self.show_graph)

    def set_data(self, roi, data, center):
        if self.y:
            ymin, ymax = roi[2], roi[2] + len(data)
            y = np.arange(ymin, ymax)
            self.data = (data, y)
            self.center = min(max(center, ymin), ymax)
            # self.plotdata.setData(x=data, y=y)
            # self.centerline.setPos(min(max(center, ymin), ymax))
        else:
            xmin, xmax = roi[0], roi[0] + len(data)
            self.data = (np.arange(xmin, xmax), data)
            self.center = min(max(center, xmin), xmax)
            # self.plotdata.setData(x=np.arange(xmin, xmax), y=data)
            # self.centerline.setPos(min(max(center, xmin), xmax))
        self.trigger.emit()

    def show_graph(self):
        if self.data and self.center:
            self.plotdata.setData(x=self.data[0], y=self.data[1])
            self.centerline.setPos(self.center)

    def handleEvent(self, evt_src, evt_type, evt_value):
        print "plot event", evt_type
        if evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT):
            data = evt_value.value
            axis = self.bpm.ROI
            if self.y:
                ymin, ymax = axis[2], axis[2] + len(data)
                y = np.arange(ymax, ymin, -1)
                self.plotdata.setData(x=data, y=y)
                self.centerline.setPos(min(max(self.bpm.BeamCenterY, ymin), ymax))
            else:
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
        self.limaccd = Device(model)
        yag = self.limaccd.getPluginDeviceNameFromType("yag")
        TaurusWidget.setModel(self, yag)

        self.yag = self.getModelObj()
        self.yag.Start()

        # Camera image
        self.imagewidget.setModel("%s/VideoImage" % yag)

        # Acquisition settings
        #self.ui.camera_type_label.setText(self.limaccd.camera_type)
        self.ui.acq_expo_time.setModel("%s/Exposure" % yag)
        self.ui.gain_label.setModel("%s/Gain" % yag)
        self.ui.acq_status_label.setModel("%s/AcqStatus" % yag)
        self.ui.acquire_checkbox.setChecked(self.yag.AcqStatus == "Running")
        self.ui.acquire_checkbox.stateChanged.connect(self.handle_acquire_images)
        # self.allowed_trigger_modes = self.limaccd.getAttrStringValueList("TriggerMode")
        # self.ui.trigger_mode_combobox.addValueNames(zip(self.allowed_trigger_modes,
        #                                                 self.allowed_trigger_modes))
        # self.ui.trigger_mode_combobox.setModel("%s/TriggerMode" % model)

        #Image settings
        self.ui.image_width_label.setModel("%s/Width" % yag)
        self.ui.image_height_label.setModel("%s/Height" % yag)
        self.ui.image_bin_spinbox.setValue(self.yag.Binning)
        self.ui.image_bin_spinbox.valueChanged.connect(self.handle_image_bin)
        self.allowed_rotations = sorted(self.limaccd.getAttrStringValueList("image_rotation"))
        self.ui.image_rotation_combobox.addValueNames(
            zip(self.allowed_rotations, self.allowed_rotations))
        self.ui.image_rotation_combobox.setCurrentIndex(self.allowed_rotations.index(self.yag.Rotation))
        self.ui.image_rotation_combobox.currentIndexChanged.connect(self.handle_rotation)

        # BPM settings
        self.ui.roi_label.setModel("%s/ROI" % yag)
        self.imagewidget.roi.sigRegionChangeFinished.connect(self.set_bpm_roi)
        self.imagewidget.show_roi(True)
        self.bpm_roi = Attribute(yag, "ROI")
        self.bpm_roi.addListener(self.imagewidget.handle_roi_update)
        self.ui.auto_roi_checkbox.setModel("%s/AutoROI" % yag)

        # BPM Beam profiles
        self.xprof = ProfilePlotWidget()
        self.ui.bpm_layout.addWidget(self.xprof)

        self.yprof = ProfilePlotWidget(y=True)
        self.ui.bpm_layout.addWidget(self.yprof)

        # BPM result event listener
        self.bpm_result = self.getModelObj().getAttribute("BPMResult")
        self.bpm_result.addListener(self.handle_bpm_result)

    def handle_acquire_images(self, event):
        if event == 2:   # look up this constant
            self.start_acq()
        if event == 0:
            self.stop_acq()

    def start_acq(self):
        """Tell camera to start acquiring images"""
        try:
            self.yag.StartAcquisition()
        except PyTango.DevFailed as e:
            print "Trouble starting: %s" % e

    def stop_acq(self):
        """Tell camera to stop acquiring images"""
        self.yag.StopAcquisition()

    def handle_rotation(self, n):
        """Change image_rotation"""
        rotation = self.allowed_rotations[n]
        self.stop_acq()
        self.yag.getAttribute("Rotation").write(rotation)
        self.start_acq()

    def handle_image_bin(self, binning):
        """Change image binning"""
        # Note: We're setting x and y binning the same
        old_binning = self.yag.Binning
        self.stop_acq()  # need to stop acq for this to work
        self.yag.getAttribute("Binning").write(binning)
        scale = float(old_binning) / binning
        roi_pos = self.imagewidget.roi.pos()
        roi_size = self.imagewidget.roi.size()
        # Update the image widget's ROI to match the new scaling
        self.imagewidget.roi.scale(scale, center=[-roi_pos.x() / roi_size.x(),
                                                  -roi_pos.y() / roi_size.y()])
        self.start_acq()

    def handle_bpm_result(self, evt_src, evt_type, evt_value):
        """Handle result from the Lima BPM calculations"""
        if evt_type in (PyTango.EventType.PERIODIC_EVENT, PyTango.EventType.CHANGE_EVENT):
            self.bpm_result = self.json_codec.decode(evt_value.value)[1]
            self.trigger.emit()

    def update_bpm_values(self):
        """Update GUI with BPM results"""
        fmt = "%.2f"
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

    def set_bpm_roi(self, roi):
        """Send the updated ROI to the BPM device."""
        state = roi.getState()
        pos = state["pos"]
        size = state["size"]
        x, y, w, h = (int(round(a)) for a in (pos.x(), pos.y(), size.x(), size.y()))
        self.bpm_roi.write([x, x+w, y, y+h])
        self._roidata = (x, x+h, y, y+w)


if __name__ == "__main__":
    import sys
    from taurus.qt.qtgui.application import TaurusApplication

    app = TaurusApplication(sys.argv)
    cam = LimaCameraWidget()
    cam.setModel(sys.argv[1])
    cam.show()
    app.exec_()
