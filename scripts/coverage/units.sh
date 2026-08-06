#!/usr/bin/env bash
# Single source of truth for how each build unit produces coverage.
#
# Sourcing this file defines COVERAGE_UNITS: one record per unit, pipe-separated:
#
#   name | workdir | report-format | report-glob-relative-to-workdir | command
#
# `report-format` is one of the parsers implemented in aggregate.py:
#   lcov | cobertura | jacoco | goprofile | simplecov
#
# The command is `eval`d from `workdir` with `$REPO_ROOT` in scope, and must
# leave its report at `report-path`. Nothing here swallows a failure:
# run-coverage.sh records the exit status of every unit and exits non-zero if any
# of them failed.
#
# Units deliberately absent, and why:
#   etl, clients/windows-desktop, demo-platform/dashboard -- no test suite yet
#     (WP-20, WP-22, WP-21 respectively).
#   tests/api, tests/contract, e2e, bdd -- require a composed stack (WP-17, WP-19,
#     WP-15); they are not unit coverage and would make this target undrunnable
#     in a bare checkout.

# shellcheck disable=SC2034
COVERAGE_UNITS=(
  "api-gateway|services/api-gateway|goprofile|coverage.out|go test -coverprofile=coverage.out -covermode=atomic ./..."
  "auth-service|services/auth-service|jacoco|build/reports/jacoco/test/jacocoTestReport.xml|\$REPO_ROOT/scripts/gradle.sh test jacocoTestReport"
  "file-service|services/file-service|lcov|lcov.info|cargo llvm-cov --lcov --output-path lcov.info"
  # The two Python services install their dependencies differently: document-service
  # uses Poetry, search-service a plain `.venv` (see the environment blueprint).
  # A bare `pytest` picks up neither and exits 4 (usage error), which is what the
  # old `make test` did.
  "document-service|services/document-service|cobertura|coverage.xml|poetry run pytest --cov=app --cov-report=xml:coverage.xml --cov-report=term-missing"
  "search-service|services/search-service|cobertura|coverage.xml|\"\$(test -x .venv/bin/python && echo .venv/bin/python || command -v python3)\" -m pytest --cov=app --cov-report=xml:coverage.xml --cov-report=term-missing"
  "collab-service|services/collab-service|lcov|coverage/lcov.info|npm test -- --coverage --coverageReporters=text-summary --coverageReporters=lcov"
  "notification-service|services/notification-service|jacoco|build/reports/jacoco/test/jacocoTestReport.xml|\$REPO_ROOT/scripts/gradle.sh test jacocoTestReport"
  "analytics-service|services/analytics-service|cobertura|target/scala-*/coverage-report/cobertura.xml|sbt clean coverage test coverageReport"
  "admin-service|services/admin-service|simplecov|coverage/.last_run.json|bundle exec rspec"
  # `dotnet test` with no project argument resolves to AuditService.csproj, the web
  # app, which is not a test project -- it exits 0 having run nothing. The 24
  # xUnit tests only run when the test project is named explicitly.
  "audit-service|services/audit-service|cobertura|TestResults/*/coverage.cobertura.xml|rm -rf TestResults && dotnet test tests/AuditService.Tests --collect:'XPlat Code Coverage;Format=cobertura,opencover' --results-directory TestResults"
  "report-service|services/report-service|jacoco|target/site/jacoco/jacoco.xml|mvn -B test jacoco:report"
  "legacy-portal|services/legacy-portal|jacoco|target/site/jacoco/jacoco.xml|./mvnw -B test jacoco:report"
  "client-app|frontend/client-app|lcov|coverage/lcov.info|npm test -- --coverage --coverage.reporter=text-summary --coverage.reporter=lcov"
  # ChromeHeadlessNoSandbox is the launcher karma.conf.js defines for containers;
  # the npm script's plain ChromeHeadless cannot start without a sandbox.
  "admin-dashboard|frontend/admin-dashboard|lcov|coverage/admin-dashboard/lcov.info|npm test -- --watch=false --browsers=ChromeHeadlessNoSandbox --code-coverage"
)

# Extra report files to collect into coverage/<unit>/sonar/, for the two units
# whose aggregate format Sonar cannot import. They go in a subdirectory so the
# unit still has exactly one top-level report for aggregate.py to parse.
#
# `sonar.coverageReportPaths` is the Generic Test Coverage importer, not a
# Cobertura reader: SonarQube reads .NET coverage only as OpenCover/VSCoverage
# and Scala coverage only as scoverage.xml. Both tools emit those next to the
# Cobertura file we aggregate on, so we keep one format for the table and the
# other for Sonar rather than picking a lowest common denominator.
# shellcheck disable=SC2034
declare -A SONAR_EXTRA_REPORTS=(
  [audit-service]="TestResults/*/coverage.opencover.xml"
  [analytics-service]="target/scala-*/scoverage-report/scoverage.xml"
)
