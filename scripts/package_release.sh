#!/usr/bin/env bash
set -euo pipefail

package_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
package_repo_dir="$(cd "$package_script_dir/.." && pwd)"
cd "$package_repo_dir"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Refusing to package: tracked changes are not committed." >&2
  exit 1
fi

if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "Refusing to package: untracked non-ignored files remain." >&2
  git ls-files --others --exclude-standard >&2
  exit 1
fi

package_version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -n 1)"
package_commit="$(git rev-parse --short=12 HEAD)"
package_name="sparkht-focus-${package_version}-${package_commit}"
package_output_dir="$package_repo_dir/dist"
package_archive="$package_output_dir/${package_name}.zip"

mkdir -p "$package_output_dir"
git archive \
  --format=zip \
  --prefix="${package_name}/" \
  --output="$package_archive" \
  HEAD

unzip -tq "$package_archive" >/dev/null
sha256sum "$package_archive" >"${package_archive}.sha256"
chmod 0644 "$package_archive" "${package_archive}.sha256"

printf 'Created %s\n' "$package_archive"
printf 'Checksum %s\n' "${package_archive}.sha256"
