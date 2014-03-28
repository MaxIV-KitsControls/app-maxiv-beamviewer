#!/usr/bin/env python

from setuptools import setup

setup(name = "taurusgui-beamviewer",
      version = "0.5.0",
      description = "TaurusGUI for viewing YAG screens",
      author = "Johan Forsberg",
      author_email = "johan.forsberg@maxlab.lu.se",
      license = "GPLv3",
      url = "http://www.maxlab.lu.se",
      package_dir = {'':'src',},
      packages = ['tgconf_beamviewer', 'tgconf_beamviewer.panels'],
      include_package_data=True,
      package_data={'tgconf_beamviewer': ['images/MAXlogo_liten.jpg']},
      scripts = ['scripts/ctbeamviewer']
)
