# Third-party notices

The Slides DOCX desktop bundle contains third-party components under their own
licenses. Source distributions and full license texts remain authoritative.

- **FFmpeg 9.0.1** — built without GPL or nonfree components and distributed
  under the GNU Lesser General Public License 2.1 or later. Source:
  <https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz>
- **Qt for Python / PySide6** — GNU Lesser General Public License 3.0, GNU
  General Public License 3.0, or a commercial Qt license. Slides DOCX includes
  and redistributes it under the LGPL 3.0 option; the license text is included
  as `PySide6-LGPL-3.0.txt`.
- **OpenCV**, **NumPy**, **python-docx**, **lxml**, and **platformdirs** — see
  the license files installed alongside each component in the application bundle.

The release workflow records exact Python package versions in
`python-packages.txt` and packages the FFmpeg build configuration and license.
