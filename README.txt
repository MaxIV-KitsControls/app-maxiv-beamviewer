A Lima camera viewer for use with e.g. YAG screens. The GUI has only been tested with Basler cameras.

Starting the GUI with one camera:

   $ python src/tgconf_beamviewer/panels/limabeam.py lima/limaccd/1

Needs to be pointed to a limaccd instance which is running the "BeamViewer" plugin.

This application uses the "PyQtGraph" module (http://www.pyqtgraph.org/) and so depends on it being installed.

There is also a taurus config file which is for a MAXIV specific GUI controlling the linac screens and cameras.

   $ taurusgui src/tgconf_beamviewer


Known issues:

* The Taurus UI crashes if the camera device is restarted.

* Crash if trying to connect to a device if the camera is unplugged. Seems like the Lima device does not go into FAULT state when this happens.

* There are still some issues with the ROI not being updated correctly.

* In general, it seems like swtching between cameras can cause issues where states are not being handled correctly.


Wishlist:

* Ability to specify a pixel <-> length conversion scale and show it instead of pixels. This should be selectable. I think Lima supports this somehow but the UI needs a way to set it.

* Frame number and FPS measurement.

* Hiding/showing the histogram. The histogram in general needs some love, at least some kind of "autoset" functionality.

* Peak finding may need work.

* A more integrated UI where the user can just select a screen and it is inserted while all other screens are withdrawn, the correct camera is started and displayed, etc.
