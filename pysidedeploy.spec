[app]
title = Slides DOCX
project_dir = @PROJECT_DIR@
input_file = @PROJECT_DIR@/packaging/gui_entry.py
exec_directory = @PROJECT_DIR@/build/gui
project_file = @PROJECT_DIR@/slides-docx-gui.pyproject
icon = @PROJECT_DIR@/packaging/slides-docx.svg

[python]
python_path = @PYTHON_PATH@
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
extra_args = --quiet --noinclude-qt-translations --include-package=slides_docx --include-package=cv2 --include-package=numpy --include-package=docx --include-package=lxml --include-package=platformdirs --include-package-data=slides_docx.gui

[buildozer]
mode = debug
recipe_dir = 
jars_dir = 
ndk_path = 
sdk_path = 
local_libs = 
arch = 
