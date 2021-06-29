import sys
import PyTango
try:
    from taurus.qt import QtGui, QtCore
except ImportError:
    from taurus.external.qt import QtGui, QtCore, Qt
try:
    from taurus.qt.qtgui.panel import TaurusWidget
except ImportError:
    from taurus.qt.qtgui.container import TaurusWidget

from taurus.qt.qtgui.button import TaurusCommandButton
from taurus.qt.qtgui.panel import TaurusDevicePanel
from taurus.qt.qtgui.display import TaurusLed
from taurus.qt.qtgui.panel.taurusvalue import \
    TaurusValue, DefaultLabelWidget

from taurus.qt.qtgui.dialog import TaurusMessageBox

State = PyTango.DevState

from taurus.qt.qtgui.display.taurusled import _TaurusLedController


class _YAGLedController(_TaurusLedController):
    LedMap = {State.INSERT   : (True,  "white", False),
              State.EXTRACT  : (True,  "green", False),
              State.FAULT    : (True,  "red",   False),
              None           : (False, "black", True)}
    
    def usePreferedColor(self, widget):
        # never use prefered widget color. Use always the map
        return False


class YAGLed(TaurusLed):
    def _calculate_controller_class(self):
        return _YAGLedController


class YAGScreenTVLabelWidget(DefaultLabelWidget):

    def getFormatedToolTip(self, cache=False):
        return self.taurusValueBuddy().getFormatedToolTip(cache)

    def contextMenuEvent(self, event):
        action_device_panel = Qt.QAction(self)
        action_device_panel.setText('Show Device Panel')
        self.connect(action_device_panel,
                     Qt.SIGNAL('triggered()'),
                     self.taurusValueBuddy().showDevicePanel)

        action_move_in = Qt.QAction(self)
        action_move_in.setText('Move In')
        self.connect(action_move_in,
                     Qt.SIGNAL('triggered()'),
                     self.taurusValueBuddy().moveIn)

        action_move_out = Qt.QAction(self)
        action_move_out.setText('Move Out')
        self.connect(action_move_out,
                     Qt.SIGNAL('triggered()'),
                     self.taurusValueBuddy().moveOut)

        menu = Qt.QMenu(self)
        menu.addAction(action_device_panel)
        menu.addSeparator()
        menu.addAction(action_move_in)
        menu.addAction(action_move_out)
        menu.exec_(event.globalPos())
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.taurusValueBuddy().showDevicePanel()
        event.accept()


class YAGScreenTVReadWidget(TaurusWidget):

    def __init__(self, *args):
        TaurusWidget.__init__(self, *args)
        self.setLayout(QtGui.QHBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        led = YAGLed(self)

        led.setUseParentModel(True)
        led.setModel('/State')
        led.setAlignment(Qt.Qt.AlignHCenter | Qt.Qt.AlignVCenter)
        led.getFormatedToolTip = self.getFormatedToolTip


        self.layout().addWidget(led)

    def getFormatedToolTip(self, cache=False):
        return self.taurusValueBuddy().getFormatedToolTip(cache)


class YAGScreenTVWriteWidget(TaurusWidget):

    def __init__(self, *args):
        TaurusWidget.__init__(self, *args)
        self.setLayout(QtGui.QHBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)

        self.moveInButton = TaurusCommandButton(self)
        self.moveInButton.setUseParentModel(True)
        self.moveInButton.setCommand('MoveIn')
        self.moveInButton.setCustomText('Move In')

        self.moveOutButton = TaurusCommandButton(self)
        self.moveOutButton.setUseParentModel(True)
        self.moveOutButton.setCommand('MoveOut')
        self.moveOutButton.setCustomText('Move Out')

        self.layout().addWidget(self.moveInButton)
        self.layout().addWidget(self.moveOutButton)


class YAGScreenTV(TaurusValue):

    def __init__(self, parent=None):
        TaurusValue.__init__(self, parent)
        self.setLabelWidgetClass(YAGScreenTVLabelWidget)
        self.setReadWidgetClass(YAGScreenTVReadWidget)
        self.setWriteWidgetClass(YAGScreenTVWriteWidget)
        self.setLabelConfig('dev_name')

    def getFormatedToolTip(self, cache):
        device = self.getModelObj()
        tool_tip = [('name', self.getModel())]
        if device:
            try:
                tool_tip.append(('status', device.Status()))
            except:
                pass

        return self.toolTipObjToStr(tool_tip)

    def showDevicePanel(self):
        dialog = TaurusDevicePanel()
        dialog.setModel(self.getModel())
        dialog.show()

    def moveIn(self):
        dev = self.getModelObj()
        if not dev:
            return
        try:
            dev.MoveIn()
        except:
            msgbox = TaurusMessageBox(*sys.exc_info())
            msgbox.exec_()

    def moveOut(self):
        dev = self.getModelObj()
        if not dev:
            return
        try:
            dev.MoveOut()
        except:
            msgbox = TaurusMessageBox(*sys.exc_info())
            msgbox.exec_()


def main():
    from taurus.qt.qtgui.application import TaurusApplication
    from taurus.qt.qtgui.panel import TaurusForm

    app = TaurusApplication(sys.argv)

    form = TaurusForm()
    form.setFormWidget(YAGScreenTV)
    form.setModel(['tmp/test/yag'])
    form.show()

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
