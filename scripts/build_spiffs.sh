#!/usr/bin/env bash

# Clean data
rm -rf data/w
mkdir -p data/w
mkdir -p data/p
if ! compgen -G "data/p/"'*.json' > /dev/null; then
  echo "Seeding data/p from profiles/seed (no device pull present)..."
  cp profiles/seed/*.json data/p/
fi

# Build web application
cd web || exit
npm ci
npm run build

cp -R dist/* ../data/w/
gzip ../data/w/assets/*.js
gzip ../data/w/assets/*.css
gzip ../data/w/*.html
