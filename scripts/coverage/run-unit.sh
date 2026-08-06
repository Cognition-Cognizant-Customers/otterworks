#!/usr/bin/env bash
# Run one build unit's test suite with coverage instrumentation and place the
# machine-readable report under coverage-reports/<unit>/.
#
#   scripts/coverage/run-unit.sh <unit>
#
# Exits with the unit's test exit code -- never masks a failure. A unit whose
# toolchain has no coverage instrumentation wired yet still runs its tests;
# summarize.py then reports it as "not instrumented"
# (see docs/TEST-COVERAGE-EXPANSION-SOW.md, WP-00).
set -uo pipefail

unit="${1:?usage: run-unit.sh <unit>}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out_dir="${COVERAGE_OUT_DIR:-$repo_root/coverage-reports}/$unit"
status=0

rm -rf "$out_dir"
mkdir -p "$out_dir"

# Run a command inside a unit directory, remembering the first failure.
run() {
  local dir="$1"
  shift
  ( cd "$repo_root/$dir" && "$@" )
  local rc=$?
  [ "$status" -eq 0 ] && status=$rc
  return $rc
}

# Copy a coverage artifact if the suite produced one; a missing artifact is not
# itself a failure (summarize.py surfaces it as "not instrumented").
collect() {
  cp -r "$repo_root/$1" "$out_dir/" 2>/dev/null || true
}

# notification-service has no gradle wrapper; auth-service does.
gradle_for() {
  if [ -x "$repo_root/$1/gradlew" ]; then echo ./gradlew; else echo gradle; fi
}

case "$unit" in
  api-gateway)
    run services/api-gateway go test -race -covermode=atomic -coverprofile="$out_dir/coverage.out" ./...
    ;;
  auth-service)
    run services/auth-service "$(gradle_for services/auth-service)" \
      test jacocoTestReport --no-daemon
    collect services/auth-service/build/reports/jacoco/test/jacocoTestReport.xml
    ;;
  file-service)
    if cargo llvm-cov --version >/dev/null 2>&1; then
      run services/file-service cargo llvm-cov --lcov --output-path "$out_dir/lcov.info"
    else
      echo "note: cargo-llvm-cov not installed; running plain cargo test (no coverage report)" >&2
      run services/file-service cargo test
    fi
    ;;
  document-service)
    run services/document-service poetry run pytest --cov=app \
      --cov-report="xml:$out_dir/coverage.xml" --cov-report=term-missing
    ;;
  search-service)
    run services/search-service python -m pytest --cov=app \
      --cov-report="xml:$out_dir/coverage.xml" --cov-report=term-missing
    ;;
  collab-service)
    run services/collab-service npx jest --coverage \
      --coverageDirectory="$out_dir" --coverageReporters=lcovonly --coverageReporters=text
    ;;
  notification-service)
    run services/notification-service "$(gradle_for services/notification-service)" \
      test jacocoTestReport --no-daemon
    collect services/notification-service/build/reports/jacoco/test/jacocoTestReport.xml
    ;;
  analytics-service)
    # Coverage instrumentation pending (scoverage) -- owned by WP-12.
    run services/analytics-service sbt test
    ;;
  admin-service)
    # simplecov is already wired in spec/spec_helper.rb. .resultset.json carries
    # real per-line hits; .last_run.json is the percentage-only fallback.
    run services/admin-service bundle exec rspec
    collect services/admin-service/coverage/.resultset.json
    collect services/admin-service/coverage/.last_run.json
    ;;
  audit-service)
    run services/audit-service dotnet test --collect:"XPlat Code Coverage" \
      --results-directory "$out_dir" --logger "console;verbosity=minimal"
    ;;
  report-service)
    # Coverage instrumentation pending (jacoco on the Java 8 pom) -- owned by WP-12.
    run services/report-service mvn test -B
    ;;
  legacy-portal)
    # Coverage instrumentation pending (jacoco) -- owned by WP-12.
    run services/legacy-portal ./mvnw test -B
    ;;
  client-app)
    # coverage.include is required: without it v8 only reports on files a test
    # imported, which reads as ~100% over a handful of lines.
    run frontend/client-app npm test -- --coverage.enabled --coverage.reporter=lcovonly \
      --coverage.reporter=text --coverage.reportsDirectory="$out_dir" \
      --coverage.include='src/**/*.{ts,tsx}' --coverage.exclude='src/**/*.{test,spec}.{ts,tsx}'
    ;;
  admin-dashboard)
    run frontend/admin-dashboard npm test -- --code-coverage
    collect frontend/admin-dashboard/coverage
    ;;
  *)
    echo "unknown unit: $unit" >&2
    exit 2
    ;;
esac

echo "$status" > "$out_dir/status.txt"
exit "$status"
