
try:
    from taurus.qt import QtGui, QtCore
except ImportError:
    from taurus.external.qt import QtGui, QtCore
from tango import Database


class CameraSelector(QtGui.QComboBox):
    domain = 'lima'
    family = 'beamviewer'
    pos = '__si'
    default = float('inf')

    def __init__(self, parent=None):
        QtGui.QComboBox.__init__(self, parent)
        self._addItems()

    def _addItems(self):
        # Access the database
        db = Database()
        device_dct = {}
        wildcard = "/".join((self.domain, self.family, '*'))
        # Build device to postion dictionary
        for member in db.get_device_member(wildcard):
            device = "/".join((self.domain, self.family, member))
            prop = db.get_device_property(device, self.pos)[self.pos]
            try:
                value = (float(prop[0]) + float(prop[1])) / 2
            except (ValueError, IndexError):
                value = self.default
            device_dct[device] = value
        # Add item in sorted order
        for device in sorted(device_dct, key=device_dct.get):
            self.addItem(device)


def main():
    import sys
    from taurus.qt.qtgui.application import TaurusApplication

    app = TaurusApplication(sys.argv)

    form = CameraSelector()
    form.show()

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
