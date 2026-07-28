package com.otterworks.analytics.service

import com.otterworks.analytics.batch.MarketSeeder
import com.otterworks.analytics.model.Product
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.time.LocalDate

/** AC-01/AC-03 unit coverage: the locked margin formula and the deterministic walk. */
class MarginServiceSpec extends AnyFlatSpec with Matchers:

  private val salmonProduct = Product(
    sku = "SLM-001",
    name = "Atlantic Salmon Fillet",
    category = "Seafood",
    commoditySeriesCode = "SALMON_NOK_KG",
    contentKg = BigDecimal("1.0"),
    freightKg = BigDecimal("1.2"),
    overheadPct = BigDecimal("15.00"),
    listPriceUsd = BigDecimal("30.00"),
    supplier = "NordicCatch AS"
  )

  "computeMargin" should "apply the locked margin model for a NOK commodity (BDD-03)" in {
    // salmon 100 NOK/kg, USDNOK 10 → 10 USD/kg × 1.0 kg = 10.0
    // freight: 2500 USD/FEU / 25000 kg × 1.2 kg = 0.12
    // overhead: (10 + 0.12) × 15% = 1.518
    // cogs = 11.638 ; margin = (30 − 11.638)/30 × 100 = 61.2067
    val m = MarginService.computeMargin(
      salmonProduct,
      commodityPrice = BigDecimal(100),
      commodityCurrency = "NOK",
      usdNok = BigDecimal(10),
      wciUsdFeu = BigDecimal(2500)
    )
    m.commodityCostUsd shouldBe BigDecimal("10.0000")
    m.freightCostUsd shouldBe BigDecimal("0.1200")
    m.overheadCostUsd shouldBe BigDecimal("1.5180")
    m.cogsUsd shouldBe BigDecimal("11.6380")
    m.marginPct shouldBe BigDecimal("61.2067")
  }

  it should "not convert USD-quoted commodities through FX" in {
    val usdProduct = salmonProduct.copy(commoditySeriesCode = "SUGAR_USD_KG")
    val m = MarginService.computeMargin(
      usdProduct,
      commodityPrice = BigDecimal("0.50"),
      commodityCurrency = "USD",
      usdNok = BigDecimal(10),
      wciUsdFeu = BigDecimal(2500)
    )
    m.commodityCostUsd shouldBe BigDecimal("0.5000")
  }

  "MarketSeeder walk extension" should "be deterministic for the same inputs (BDD-01)" in {
    val a = MarketSeeder.walkExtension(
      "SALMON_NOK_KG", LocalDate.parse("2026-06-30"), BigDecimal(100), LocalDate.parse("2026-07-20"))
    val b = MarketSeeder.walkExtension(
      "SALMON_NOK_KG", LocalDate.parse("2026-06-30"), BigDecimal(100), LocalDate.parse("2026-07-20"))
    a shouldBe b
    a should have size 20
    a.head._2 shouldBe "2026-07-01"
    a.last._2 shouldBe "2026-07-20"
    all(a.map(_._3)) should be > BigDecimal(0)
  }

  it should "extend an already-extended series with identical values" in {
    val full = MarketSeeder.walkExtension(
      "USD_NOK", LocalDate.parse("2026-06-30"), BigDecimal("10.5"), LocalDate.parse("2026-07-10"))
    val firstHalf = MarketSeeder.walkExtension(
      "USD_NOK", LocalDate.parse("2026-06-30"), BigDecimal("10.5"), LocalDate.parse("2026-07-05"))
    full.take(5) shouldBe firstHalf
  }
