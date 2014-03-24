
from taurus.qt import QtCore, QtGui
from taurus import Database


class CameraSelector(QtGui.QComboBox):
    domain = 'lima'
    family = 'limaccd'

    def __init__(self, parent=None):
        QtGui.QComboBox.__init__(self, parent)
        self._addItems()
    
    def _addItems(self):
        db = Database()
        for member in db.getDeviceMemberNames(self.domain, self.family):
            item = '%s/%s/%s' % (self.domain, self.family, member)
            self.addItem(item)


def main():
    import sys
    from taurus.qt.qtgui.application import TaurusApplication

    app = TaurusApplication(sys.argv)

    form = CameraSelector()
    form.show()

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()