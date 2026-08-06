addSbtPlugin("com.eed3si9n" % "sbt-assembly" % "2.1.5")

// Coverage instrumentation (WP-12). Enabled on demand:
//   sbt coverage test coverageReport
// writes target/scala-3.4.0/coverage-report/cobertura.xml and
// target/scala-3.4.0/scoverage-report/scoverage.xml. A plain `sbt test` is
// uninstrumented and unaffected.
addSbtPlugin("org.scoverage" % "sbt-scoverage" % "2.0.12")
