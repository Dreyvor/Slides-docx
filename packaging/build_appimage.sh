#!/usr/bin/env bash
set -euo pipefail

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_root="${repository}/build/appimage"
app_dir="${build_root}/Slides_DOCX.AppDir"
dist_dir="${repository}/dist"
ffmpeg_version="9.0.1"
ffmpeg_archive="ffmpeg-${ffmpeg_version}.tar.xz"
ffmpeg_sha256="cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635"
ffmpeg_prefix="${build_root}/ffmpeg-install"

mkdir -p "${build_root}" "${dist_dir}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${build_root}/cache}"
mkdir -p "${XDG_CACHE_HOME}"

if [[ ! -x "${ffmpeg_prefix}/bin/ffmpeg" ]]; then
    curl --fail --location --retry 3 \
        "https://ffmpeg.org/releases/${ffmpeg_archive}" \
        --output "${build_root}/${ffmpeg_archive}"
    echo "${ffmpeg_sha256}  ${build_root}/${ffmpeg_archive}" | sha256sum --check
    rm -rf "${build_root}/ffmpeg-${ffmpeg_version}"
    tar -C "${build_root}" -xf "${build_root}/${ffmpeg_archive}"
    pushd "${build_root}/ffmpeg-${ffmpeg_version}" >/dev/null
    ./configure \
        --prefix="${ffmpeg_prefix}" \
        --disable-shared \
        --enable-static \
        --disable-debug \
        --disable-doc \
        --disable-ffplay \
        --disable-network \
        --disable-autodetect \
        --disable-gpl \
        --disable-nonfree \
        --disable-version3
    make -j"$(nproc)" ffmpeg ffprobe
    mkdir -p "${ffmpeg_prefix}/bin"
    cp ffmpeg ffprobe "${ffmpeg_prefix}/bin/"
    mkdir -p "${ffmpeg_prefix}/share/licenses/ffmpeg"
    cp COPYING.LGPLv2.1 LICENSE.md "${ffmpeg_prefix}/share/licenses/ffmpeg/"
    ./ffmpeg -buildconf > "${ffmpeg_prefix}/share/licenses/ffmpeg/build-configuration.txt" 2>&1
    popd >/dev/null
fi

rm -rf "${repository}/build/gui"
mkdir -p "${repository}/build/gui"
pushd "${repository}" >/dev/null
pyside6-deploy -c pysidedeploy.spec --force
popd >/dev/null

frozen_dir="$(find "${repository}/build/gui" -maxdepth 2 -type d -name '*.dist' -print -quit)"
if [[ -z "${frozen_dir}" ]]; then
    echo "pyside6-deploy did not create a standalone directory" >&2
    exit 1
fi
frozen_executable="$(find "${frozen_dir}" -maxdepth 1 -type f -perm -u+x -name '*.bin' -print -quit)"
if [[ -z "${frozen_executable}" ]]; then
    echo "pyside6-deploy did not create an executable" >&2
    exit 1
fi

rm -rf "${app_dir}"
mkdir -p \
    "${app_dir}/usr/bin" \
    "${app_dir}/usr/lib/slides-docx/app" \
    "${app_dir}/usr/lib/slides-docx/bin" \
    "${app_dir}/usr/share/applications" \
    "${app_dir}/usr/share/icons/hicolor/scalable/apps" \
    "${app_dir}/usr/share/doc/slides-docx"
cp -a "${frozen_dir}/." "${app_dir}/usr/lib/slides-docx/app/"
mv \
    "${app_dir}/usr/lib/slides-docx/app/$(basename "${frozen_executable}")" \
    "${app_dir}/usr/lib/slides-docx/app/slides-docx-gui"
cp "${ffmpeg_prefix}/bin/ffmpeg" "${ffmpeg_prefix}/bin/ffprobe" \
    "${app_dir}/usr/lib/slides-docx/bin/"
cp -a "${ffmpeg_prefix}/share/licenses" "${app_dir}/usr/share/doc/slides-docx/"
cp "${repository}/packaging/THIRD_PARTY_NOTICES.md" \
    "${app_dir}/usr/share/doc/slides-docx/"
python3 "${repository}/packaging/collect_licenses.py" \
    "${app_dir}/usr/share/doc/slides-docx/licenses/python"
python3 -m pip freeze > "${app_dir}/usr/share/doc/slides-docx/python-packages.txt"
cp "${repository}/packaging/linux/AppRun" "${app_dir}/AppRun"
cp "${repository}/packaging/linux/slides-docx.desktop" \
    "${app_dir}/slides-docx.desktop"
cp "${repository}/packaging/slides-docx.svg" "${app_dir}/slides-docx.svg"
cp "${repository}/packaging/slides-docx.svg" \
    "${app_dir}/usr/share/icons/hicolor/scalable/apps/slides-docx.svg"
ln -s ../lib/slides-docx/app/slides-docx-gui "${app_dir}/usr/bin/slides-docx-gui"
chmod +x "${app_dir}/AppRun" "${app_dir}/usr/lib/slides-docx/app/slides-docx-gui" \
    "${app_dir}/usr/lib/slides-docx/bin/ffmpeg" \
    "${app_dir}/usr/lib/slides-docx/bin/ffprobe"

appimagetool="${APPIMAGETOOL:-appimagetool}"
ARCH=x86_64 "${appimagetool}" "${app_dir}" \
    "${dist_dir}/Slides_DOCX-${SLIDES_DOCX_VERSION:-preview}-x86_64.AppImage"
(cd "${dist_dir}" && sha256sum ./*.AppImage > SHA256SUMS)
cp "${repository}/packaging/THIRD_PARTY_NOTICES.md" "${dist_dir}/"
