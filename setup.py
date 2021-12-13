#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name="taurusgui-beamviewer",
    version="0.13.0",
    description="GUI for viewing YAG screens",
    author="Johan Forsberg",
    author_email="johan.forsberg@maxlab.lu.se",
    license="GPLv3",
    url="http://www.maxlab.lu.se",
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    package_data={'tgconf_beamviewer': ['default.ini', 'images/MAXlogo_liten.jpg']},
    data_files=[('/usr/share/applications', ['maxiv-beamviewer.desktop'])],
    install_requires=['pyqtgraph', 'taurus', 'maxwidgets', 'pillow'],
    scripts=['scripts/ctbeamviewer']
)
