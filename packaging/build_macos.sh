#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "This builder requires an Apple Silicon macOS runner." >&2
    exit 1
fi

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_root="${repository}/build/macos"
deploy_root="${build_root}/deploy"
app_bundle="${build_root}/Slides DOCX.app"
dist_dir="${repository}/dist"
ffmpeg_version="9.0.1"
ffmpeg_archive="ffmpeg-${ffmpeg_version}.tar.xz"
ffmpeg_url="https://ffmpeg.org/releases/${ffmpeg_archive}"
ffmpeg_sha256="cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635"
ffmpeg_prefix="${build_root}/ffmpeg-install"
deploy_spec="${build_root}/pysidedeploy.spec"
codesign_identity="${SLIDES_DOCX_CODESIGN_IDENTITY:--}"
export MACOSX_DEPLOYMENT_TARGET=13.0

package_version="$(python3 -c 'from slides_docx import __version__; print(__version__)')"
artifact_version="${SLIDES_DOCX_VERSION:-${package_version}}"
dmg_name="Slides_DOCX-${artifact_version}-macOS-arm64.dmg"

mkdir -p "${build_root}" "${dist_dir}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${build_root}/cache}"
mkdir -p "${XDG_CACHE_HOME}"

build_ffmpeg() {
    if [[ -x "${ffmpeg_prefix}/bin/ffmpeg" \
        && -x "${ffmpeg_prefix}/bin/ffprobe" \
        && -s "${ffmpeg_prefix}/share/licenses/ffmpeg/SOURCE.md" \
        && -s "${ffmpeg_prefix}/share/licenses/ffmpeg/build-configuration.txt" ]]; then
        return
    fi
    curl --fail --location --retry 3 "${ffmpeg_url}" \
        --output "${build_root}/${ffmpeg_archive}"
    printf '%s  %s\n' "${ffmpeg_sha256}" "${build_root}/${ffmpeg_archive}" \
        | shasum -a 256 --check
    rm -rf "${build_root}/ffmpeg-${ffmpeg_version}" "${ffmpeg_prefix}"
    tar -C "${build_root}" -xf "${build_root}/${ffmpeg_archive}"
    pushd "${build_root}/ffmpeg-${ffmpeg_version}" >/dev/null
    ./configure \
        --prefix=/ \
        --arch=arm64 \
        --target-os=darwin \
        --cc=clang \
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
    make -j"$(sysctl -n hw.logicalcpu)" ffmpeg ffprobe
    mkdir -p "${ffmpeg_prefix}/bin" "${ffmpeg_prefix}/share/licenses/ffmpeg"
    cp ffmpeg ffprobe "${ffmpeg_prefix}/bin/"
    cp COPYING.LGPLv2.1 LICENSE.md "${ffmpeg_prefix}/share/licenses/ffmpeg/"
    ./ffmpeg -buildconf \
        > "${ffmpeg_prefix}/share/licenses/ffmpeg/build-configuration.txt" 2>&1
    cat > "${ffmpeg_prefix}/share/licenses/ffmpeg/SOURCE.md" <<EOF
# FFmpeg source

- Version: ${ffmpeg_version}
- Source: ${ffmpeg_url}
- SHA-256: ${ffmpeg_sha256}
- Architecture: arm64; minimum deployment target: macOS ${MACOSX_DEPLOYMENT_TARGET}
- Configuration: see \`build-configuration.txt\`.
EOF
    popd >/dev/null
}

freeze_application() {
    rm -rf "${deploy_root}" "${app_bundle}"
    python3 "${repository}/packaging/macos/generate_icon.py" \
        "${repository}/packaging/slides-docx.svg" \
        "${repository}/packaging/macos/slides-docx.icns"
    python3 "${repository}/packaging/render_deploy_spec.py" \
        "${repository}/packaging/macos/pysidedeploy.spec.in" "${deploy_spec}"
    pushd "${repository}" >/dev/null
    pyside6-deploy -c "${deploy_spec}" --force
    popd >/dev/null

    frozen_app="$(find "${deploy_root}" -type d -name '*.app' -print -quit)"
    if [[ -z "${frozen_app}" ]]; then
        echo "pyside6-deploy did not create a macOS application bundle." >&2
        exit 1
    fi
    cp -a "${frozen_app}" "${app_bundle}"

    # The bundle icon is the ICNS file in Contents/Resources. Package data in
    # Contents/MacOS is treated as nested code by codesign, so the GUI SVG must
    # not remain beside the frozen Python modules.
    rm -f "${app_bundle}/Contents/MacOS/slides_docx/gui/icon.svg"
}

configure_bundle() {
    plist="${app_bundle}/Contents/Info.plist"
    plist_set() {
        key="$1"
        type="$2"
        value="$3"
        /usr/libexec/PlistBuddy -c "Delete :${key}" "${plist}" >/dev/null 2>&1 || true
        /usr/libexec/PlistBuddy -c "Add :${key} ${type} ${value}" "${plist}"
    }
    plist_set CFBundleIdentifier string io.github.dreyvor.slidesdocx
    plist_set CFBundleName string "Slides DOCX"
    plist_set CFBundleDisplayName string "Slides DOCX"
    plist_set CFBundleShortVersionString string "${package_version}"
    plist_set CFBundleVersion string "${package_version}"
    plist_set LSMinimumSystemVersion string 13.0
    plist_set LSApplicationCategoryType string public.app-category.education
    plist_set NSHighResolutionCapable bool true

    resources="${app_bundle}/Contents/Resources"
    mkdir -p "${resources}/bin" "${resources}/licenses/ffmpeg" \
        "${resources}/licenses/python"
    cp "${ffmpeg_prefix}/bin/ffmpeg" "${ffmpeg_prefix}/bin/ffprobe" \
        "${resources}/bin/"
    cp -a "${ffmpeg_prefix}/share/licenses/ffmpeg/." \
        "${resources}/licenses/ffmpeg/"
    cp "${repository}/packaging/THIRD_PARTY_NOTICES.md" \
        "${resources}/licenses/THIRD_PARTY_NOTICES.md"
    python3 "${repository}/packaging/collect_licenses.py" \
        "${resources}/licenses/python"
    python3 -m pip freeze > "${resources}/licenses/python-packages.txt"
    chmod +x "${resources}/bin/ffmpeg" "${resources}/bin/ffprobe"
}

thin_to_arm64() {
    while IFS= read -r -d '' binary; do
        if ! file -b "${binary}" | grep -q 'Mach-O'; then
            continue
        fi
        architectures="$(lipo -archs "${binary}")"
        if [[ " ${architectures} " == *" arm64 "* && "${architectures}" == *" "* ]]; then
            temporary="${binary}.arm64"
            lipo "${binary}" -thin arm64 -output "${temporary}"
            chmod "$(stat -f '%Lp' "${binary}")" "${temporary}"
            mv "${temporary}" "${binary}"
        fi
    done < <(find "${app_bundle}" -type f -print0)
}

sign_bundle() {
    # This stage currently uses identity "-" (ad-hoc signing). A future
    # Developer ID build can inject SLIDES_DOCX_CODESIGN_IDENTITY here without
    # changing the bundle layout.
    sign_options=(--force --sign "${codesign_identity}")
    if [[ "${codesign_identity}" == "-" ]]; then
        sign_options+=(--timestamp=none)
    else
        sign_options+=(--timestamp --options runtime)
    fi
    while IFS= read -r binary; do
        codesign "${sign_options[@]}" "${binary}"
    done < <(
        find "${app_bundle}" -type f -print \
            | while IFS= read -r candidate; do
                file -b "${candidate}" | grep -q 'Mach-O' && printf '%s\n' "${candidate}"
            done \
            | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2-
    )
    while IFS= read -r framework; do
        codesign "${sign_options[@]}" "${framework}"
    done < <(find "${app_bundle}" -type d -name '*.framework' -print | sort -r)
    # Nested Mach-O files and frameworks were signed above. Sign the outer
    # bundle last; --deep is intentionally reserved for verification.
    codesign "${sign_options[@]}" "${app_bundle}"
}

create_dmg() {
    staging="${build_root}/dmg"
    rm -rf "${staging}" "${dist_dir}/${dmg_name}"
    mkdir -p "${staging}"
    cp -a "${app_bundle}" "${staging}/"
    ln -s /Applications "${staging}/Applications"
    hdiutil create -volname "Slides DOCX" -srcfolder "${staging}" \
        -format UDZO -ov "${dist_dir}/${dmg_name}"
}

notarize_dmg() {
    # Unsigned preview builds intentionally skip this stage. A future Developer
    # ID workflow can provide a notarytool keychain profile and retain the same
    # application and DMG layout.
    if [[ -z "${SLIDES_DOCX_NOTARY_PROFILE:-}" ]]; then
        return
    fi
    if [[ "${codesign_identity}" == "-" ]]; then
        echo "Notarization requires a Developer ID signing identity." >&2
        exit 1
    fi
    xcrun notarytool submit "${dist_dir}/${dmg_name}" \
        --keychain-profile "${SLIDES_DOCX_NOTARY_PROFILE}" --wait
    xcrun stapler staple "${dist_dir}/${dmg_name}"
}

write_release_metadata() {
    pushd "${dist_dir}" >/dev/null
    shasum -a 256 "${dmg_name}" > SHA256SUMS-macOS-arm64
    popd >/dev/null
    cp "${repository}/packaging/THIRD_PARTY_NOTICES.md" \
        "${dist_dir}/THIRD_PARTY_NOTICES-macOS-arm64.md"
}

build_ffmpeg
freeze_application
configure_bundle
thin_to_arm64
sign_bundle
python3 "${repository}/packaging/macos/verify_bundle.py" "${app_bundle}"
create_dmg
notarize_dmg
write_release_metadata

printf 'Created %s\n' "${dist_dir}/${dmg_name}"
