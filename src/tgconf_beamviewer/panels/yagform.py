
from taurus.qt.qtgui.panel import TaurusForm
from yagscreentv import YAGScreenTV

class YAGForm(TaurusForm):
    
    def __init__(self, *args, **kwargs):
        TaurusForm.__init__(self, *args, **kwargs)
        
        self.setFormWidget(YAGScreenTV)
        self.setModifiableByUser(True)
        self.setWithButtons(False)
    
def main():
    import sys
    from taurus.qt.qtgui.application import TaurusApplication

    app = TaurusApplication(sys.argv)

    form = YAGForm()
    form.setModel(['tmp/test/yag'])
    form.show()

    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
