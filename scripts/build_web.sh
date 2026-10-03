#!/usr/bin/env bash
# Rebuild web/app.js and web/app.css from web/src/.  Only needed after editing
# the UI source - the compiled files are committed, so running the app needs
# Python only.  Requires Node 18+.
set -euo pipefail
cd "$(dirname "$0")/.."
npx --yes esbuild@0.24.0 web/src/app.jsx --outfile=web/app.js --loader:.jsx=jsx \
  --jsx=transform --target=es2019 --minify --legal-comments=none
npx --yes tailwindcss@3.4.17 -c web/src/tailwind.config.js -i web/src/input.css -o web/app.css --minify
# React is vendored so the app needs no CDN (and works offline).
if [ ! -f web/vendor/react.production.min.js ]; then
  tmp=$(mktemp -d)
  (cd "$tmp" && npm pack --silent react@18.3.1 react-dom@18.3.1 >/dev/null && tar xzf react-18.3.1.tgz && mv package react && tar xzf react-dom-18.3.1.tgz)
  cp "$tmp/react/umd/react.production.min.js" web/vendor/
  cp "$tmp/package/umd/react-dom.production.min.js" web/vendor/
  rm -rf "$tmp"
fi
echo "built web/app.js, web/app.css"
