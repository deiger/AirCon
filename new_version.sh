#!/bin/bash
set -e

git pull

OLD_VERSION=`git describe --abbrev=0`
NEW_VERSION=$1
if [ -z "$2" ]; then
  NEW_VERSION_MSG=v$1
else
  NEW_VERSION_MSG=$2
fi

git tag -a $NEW_VERSION -m $NEW_VERSION_MSG
auto-changelog

for f in aircon/__init__.py hassio/config.json docker-compose.yaml; do
  sed -i "" -e "s/$OLD_VERSION/$NEW_VERSION/" $f
done

git commit -a -m $NEW_VERSION
git tag -d $NEW_VERSION
git tag -a $NEW_VERSION -m $NEW_VERSION_MSG
git push
docker buildx build --platform linux/arm/v7,linux/arm64,linux/amd64,linux/386 -t deiger/aircon:$NEW_VERSION --push .
git push --tags
