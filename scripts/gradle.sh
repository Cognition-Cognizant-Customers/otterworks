#!/usr/bin/env bash
# Invoke Gradle for the current directory, preferring the wrapper.
#
# `.gitignore` excludes `*.jar` repo-wide, which takes `gradle-wrapper.jar` with
# it -- so `./gradlew` exists in a fresh clone but cannot run, and
# notification-service has no wrapper at all. CI sidesteps this with
# `gradle/actions/setup-gradle`, which is why `make test`'s `./gradlew` was
# broken everywhere except CI. This picks whichever one is actually usable.
set -euo pipefail

if [[ -x ./gradlew && -f gradle/wrapper/gradle-wrapper.jar ]]; then
  exec ./gradlew "$@"
fi

if command -v gradle >/dev/null 2>&1; then
  exec gradle "$@"
fi

echo "No usable Gradle: gradle/wrapper/gradle-wrapper.jar is missing (see .gitignore '*.jar')" >&2
echo "and no 'gradle' on PATH. Install Gradle 8.6+ or restore the wrapper jar." >&2
exit 127
