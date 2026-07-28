package com.otterworks.analytics.batch

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.nio.file.{Files, Path, Paths}
import java.security.MessageDigest

/**
 * AC-14 / BDD-14: the committed shared contract at `testdata/market-series/`
 * (owned by OTD-15, consumed by OTD-14) and the copies bundled into this
 * service's resources must be checksum-identical. The Docker build context is
 * `services/analytics-service` only, so the files are committed in both
 * places; this spec keeps them in lockstep.
 */
class MarketSeedResourcesSpec extends AnyFlatSpec with Matchers:

  private val files = List("series.csv", "baseline_prices.csv", "products.csv", "README.md")

  /** Walk up from the working directory to find the repo-root testdata copy. */
  private def repoTestdataDir: Option[Path] =
    Iterator
      .iterate(Paths.get(System.getProperty("user.dir")).toAbsolutePath)(_.getParent)
      .takeWhile(_ != null)
      .map(_.resolve("testdata/market-series"))
      .find(Files.isDirectory(_))

  private def sha256(bytes: Array[Byte]): String =
    MessageDigest.getInstance("SHA-256").digest(bytes).map("%02x".format(_)).mkString

  "market-series seed resources" should "be checksum-identical to testdata/market-series" in {
    val testdata = repoTestdataDir
    assume(testdata.isDefined, "repo-root testdata/market-series not found (not a full checkout)")

    for name <- files do
      val committed = Files.readAllBytes(testdata.get.resolve(name))
      val stream = Option(getClass.getResourceAsStream(s"/seed/market-series/$name"))
      withClue(s"bundled resource /seed/market-series/$name missing: ") {
        stream.isDefined shouldBe true
      }
      val bundled = stream.get.readAllBytes()
      withClue(s"$name differs between testdata/market-series and bundled resources: ") {
        sha256(bundled) shouldBe sha256(committed)
      }
  }

  it should "carry the documented CSV schemas" in {
    MarketSeeder.parseSeries(MarketSeeder.readResource("series.csv")) should have size 7
    MarketSeeder.parseProducts(MarketSeeder.readResource("products.csv")) should have size 40
    MarketSeeder.parseBaselinePrices(MarketSeeder.readResource("baseline_prices.csv")).size should be > 4000
  }
