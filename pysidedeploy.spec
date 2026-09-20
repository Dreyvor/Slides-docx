[app]
title = Slides DOCX
project_dir = .
input_file = packaging/gui_entry.py
exec_directory = build/gui
project_file = slides-docx-gui.pyproject
icon = packaging/slides-docx.svg

[python]
python_path = /tmp/slides-docx-gui-test/bin/python
packages = nuitka,ordered_set,zstandard
android_packages = 

[qt]
qml_files = 
excluded_qml_plugins = 
modules = Core,DBus,Gui,Widgets
plugins = platforminputcontexts,platforms,imageformats

[android]
wheel_pyside = 
wheel_shiboken = 
plugins = 

[nuitka]
mode = standalone
extra_args = --quiet --noinclude-qt-translations --include-package=slides_docx --include-package=cv2 --include-package=numpy --include-package=docx --include-package=platformdirs --include-package-data=slides_docx.gui

[buildozer]
mode = debug
recipe_dir = 
jars_dir = 
ndk_path = 
sdk_path = 
local_libs = 
arch = 

