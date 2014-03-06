#!/usr/bin/env python

from setuptools import setup

setup(name = "taurusgui-yagviewer",
      version = "0.1.0",
      description = "TaurusGUI for YAG screens",
      author = "Johan Forsberg",
      author_email = "johan.forsberg@maxlab.lu.se",
      license = "GPLv3",
      url = "http://www.maxlab.lu.se",
      package_dir = {'':'src',},
      packages = ['tgconf_yagviewer', 'tgconf_yagviewer.panels'],
      include_package_data=True,
      package_data={'tgconf_yagviewer': ['images/MAXlogo_liten.jpg']},
      scripts = ['scripts/yagviewer']
)
