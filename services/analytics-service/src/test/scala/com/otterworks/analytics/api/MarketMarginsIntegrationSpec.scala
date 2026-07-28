package com.otterworks.analytics.api

import akka.http.scaladsl.marshallers.sprayjson.SprayJsonSupport.*
import akka.http.scaladsl.model.{ContentTypes, HttpEntity, StatusCodes}
import akka.http.scaladsl.testkit.ScalatestRouteTest
import com.dimafeng.testcontainers.PostgreSQLContainer
import com.otterworks.analytics.batch.MarketSeeder
import com.otterworks.analytics.config.PostgresConfig
import com.otterworks.analytics.db.AnalyticsDb
import com.otterworks.analytics.model.*
import com.otterworks.analytics.model.MarketJsonProtocol.{*, given}
import com.otterworks.analytics.repository.MarketRepository
import com.otterworks.analytics.service.MarginService
import org.scalatest.BeforeAndAfterAll
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import org.testcontainers.utility.DockerImageName
import slick.jdbc.PostgresProfile.api.*
import spray.json.*

import java.time.LocalDate
import scala.concurrent.duration.*
import scala.concurrent.Await

/**
 * End-to-end coverage of the market/margin feature against a real PostgreSQL
 * (Testcontainers; cancelled when Docker is unavailable):
 *   BDD-01 / AC-01: V2 migration + deterministic idempotent baseline seed
 *   BDD-02 / AC-02: Flyway isolation (dedicated schema + history table) even
 *                   with a conflicting public.flyway_schema_history
 *   BDD-03 / AC-03: GET /margins returns KPIs + rows matching the formula
 *   BDD-04 / AC-04: series/prices/status endpoints
 *   BDD-05 / AC-05: POST observations upserts + recomputes
 *   BDD-06 / AC-06: per-item validation of observations
 */
class MarketMarginsIntegrationSpec
    extends AnyFlatSpec
    with Matchers
    with ScalatestRouteTest
    with BeforeAndAfterAll:

  private var container: Option[PostgreSQLContainer] = None
  private var db: Option[AnalyticsDb] = None
  private var repo: Option[MarketRepository] = None
  private var marginService: Option[MarginService] = None

  override def beforeAll(): Unit =
    super.beforeAll()
    try
      val c = PostgreSQLContainer(dockerImageNameOverride = DockerImageName.parse("postgres:15-alpine"))
      c.start()
      val cfg = PostgresConfig(c.jdbcUrl, c.username, c.password, maxPoolSize = 4)
      val database = new AnalyticsDb(cfg)
      // BDD-02: simulate another service owning public.flyway_schema_history
      // with a conflicting version-1 checksum before analytics migrates.
      Await.result(
        database.database.run(
          DBIO.seq(
            sqlu"""CREATE TABLE IF NOT EXISTS public.flyway_schema_history (
                     installed_rank INT PRIMARY KEY, version VARCHAR(50), description VARCHAR(200),
                     type VARCHAR(20), script VARCHAR(1000), checksum INT, installed_by VARCHAR(100),
                     installed_on TIMESTAMP DEFAULT NOW(), execution_time INT, success BOOLEAN)""",
            sqlu"""INSERT INTO public.flyway_schema_history
                     (installed_rank, version, description, type, script, checksum, installed_by, execution_time, success)
                   VALUES (1, '1', 'init', 'SQL', 'V1__init.sql', 12345, 'auth-service', 10, true)
                   ON CONFLICT DO NOTHING"""
          )
        ),
        30.seconds
      )
      database.migrate()
      container = Some(c)
      db = Some(database)
      val r = new MarketRepository(database)
      val svc = new MarginService(r)
      repo = Some(r)
      marginService = Some(svc)
    catch
      case ex: Throwable =>
        info(s"Docker/Postgres unavailable, skipping market integration tests: ${ex.getMessage}")
        container = None

  override def afterAll(): Unit =
    db.foreach(_.close())
    container.foreach(_.stop())
    super.afterAll()

  private def requireStack(): (AnalyticsDb, MarketRepository, MarginService) =
    assume(db.isDefined, "Docker not available")
    (db.get, repo.get, marginService.get)

  private def count(database: AnalyticsDb, table: String): Long =
    val q = table match
      case "market_prices" => sql"SELECT COUNT(*) FROM market_prices".as[Long].head
      case "products" => sql"SELECT COUNT(*) FROM products".as[Long].head
      case "market_series" => sql"SELECT COUNT(*) FROM market_series".as[Long].head
      case "product_margin_daily" => sql"SELECT COUNT(*) FROM product_margin_daily".as[Long].head
      case _ => sql"SELECT COUNT(*) FROM sync_runs".as[Long].head
    Await.result(database.database.run(q), 30.seconds)

  "Flyway migration" should "apply into the dedicated analytics schema despite a conflicting public history (AC-02/BDD-02)" in {
    val (database, _, _) = requireStack()
    val schemas = Await.result(
      database.database.run(
        sql"""SELECT table_schema FROM information_schema.tables
              WHERE table_name = 'market_series'""".as[String]
      ),
      30.seconds
    )
    schemas should contain("analytics")
    val history = Await.result(
      database.database.run(
        sql"""SELECT COUNT(*) FROM analytics.flyway_schema_history_analytics WHERE success""".as[Long].head
      ),
      30.seconds
    )
    history should be >= 2L
  }

  "MarketSeeder" should "seed deterministic baseline idempotently (AC-01/BDD-01)" in {
    val (database, r, svc) = requireStack()
    val today = LocalDate.now()
    val insertedFirst = MarketSeeder.run(r, svc, today)
    insertedFirst should be > 0
    count(database, "market_series") shouldBe 7L
    count(database, "products") shouldBe 40L
    val prices = count(database, "market_prices")
    val margins = count(database, "product_margin_daily")
    margins should be > 0L

    // Second boot on the same day: nothing new.
    val insertedSecond = MarketSeeder.run(r, svc, today)
    insertedSecond shouldBe 0
    count(database, "market_prices") shouldBe prices
    count(database, "product_margin_daily") shouldBe margins
  }

  private def routes =
    val (_, r, svc) = requireStack()
    akka.http.scaladsl.server.Directives.concat(
      MarginRoutes(svc, r).routes,
      MarketIngestRoutes(svc, r).routes,
    )

  "GET /api/v1/analytics/margins" should "return KPIs and ~40 formula-consistent rows (AC-03/BDD-03)" in {
    requireStack()
    Get("/api/v1/analytics/margins") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      val body = responseAs[MarginsResponse]
      body.rows should have size 40
      body.source shouldBe "synthetic"
      body.asOfDate should not be empty
      body.kpis.salmonIndex should be > BigDecimal(0)
      body.kpis.freightIndex should be > BigDecimal(0)
      // margin formula spot-check on every row
      for row <- body.rows do
        val expected =
          ((row.listPriceUsd - row.cogsUsd) / row.listPriceUsd * 100).setScale(4, BigDecimal.RoundingMode.HALF_UP)
        row.marginPct shouldBe expected
    }
  }

  "GET series/prices/status endpoints" should "return filtered ordered data (AC-04/BDD-04)" in {
    requireStack()
    Get("/api/v1/analytics/market/series") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      responseAs[JsValue].asJsObject.fields("series").convertTo[List[MarketSeries]] should have size 7
    }
    Get("/api/v1/analytics/market/prices?series_code=SALMON_NOK_KG&from=2025-01-01&to=2025-01-31") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      val prices = responseAs[JsValue].asJsObject.fields("prices").convertTo[List[PricePoint]]
      prices should have size 31
      prices.map(_.priceDate) shouldBe prices.map(_.priceDate).sorted
      all(prices.map(_.priceDate)) should (be >= "2025-01-01" and be <= "2025-01-31")
    }
    Get("/api/v1/analytics/margins/series?sku=SLM-001&from=2025-01-01&to=2025-01-31") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      responseAs[MarginSeriesResponse].points should have size 31
    }
    Get("/api/v1/analytics/market/status") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      val s = responseAs[MarketStatus]
      s.source shouldBe "synthetic"
      s.lastRunType shouldBe Some("baseline_seed")
    }
  }

  "GET /margins/export?format=csv" should "return CSV matching the grid (AC-10)" in {
    requireStack()
    Get("/api/v1/analytics/margins/export?format=csv") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      val csv = responseAs[String]
      csv.linesIterator.next() shouldBe
        "sku,name,category,supplier,list_price_usd,commodity_cost_usd,freight_cost_usd,overhead_cost_usd,cogs_usd,margin_pct"
      csv.linesIterator.size shouldBe 41 // header + 40 SKUs
      csv should include("SLM-001")
    }
  }

  "POST /market/observations" should "upsert manual prices and recompute margins (AC-05/BDD-05)" in {
    val (database, _, _) = requireStack()
    val today = LocalDate.now().toString
    val request = ObservationsRequest(
      observations = List(
        Observation("SALMON_NOK_KG", today, BigDecimal("103.10")),
        Observation("USD_NOK", today, BigDecimal("10.62")),
        Observation("DREWRY_WCI_USD_FEU", today, BigDecimal("3512.00"))
      ),
      sourceNote = Some("integration test manual pull")
    )
    Post("/api/v1/analytics/market/observations", HttpEntity(ContentTypes.`application/json`, request.toJson.compactPrint)) ~> routes ~> check {
      status shouldBe StatusCodes.OK
      val body = responseAs[ObservationsResponse]
      body.accepted shouldBe 3
      body.rejected shouldBe empty
      body.recomputedSkus shouldBe 40 // FX + freight affect every SKU
      body.runId should be > 0L
    }
    val manual = Await.result(
      database.database.run(
        sql"""SELECT COUNT(*) FROM market_prices WHERE source = 'manual_pull'""".as[Long].head
      ),
      30.seconds
    )
    manual shouldBe 3L
    Get("/api/v1/analytics/market/status") ~> routes ~> check {
      responseAs[MarketStatus].source shouldBe "manual_pull"
    }
  }

  it should "reject invalid observations item-by-item (AC-06/BDD-06)" in {
    requireStack()
    val today = LocalDate.now().toString
    val allInvalid = ObservationsRequest(
      observations = List(
        Observation("NOT_A_SERIES", today, BigDecimal(1)),
        Observation("SALMON_NOK_KG", "not-a-date", BigDecimal(1)),
        Observation("SALMON_NOK_KG", today, BigDecimal(-5))
      ),
      sourceNote = None
    )
    Post("/api/v1/analytics/market/observations", HttpEntity(ContentTypes.`application/json`, allInvalid.toJson.compactPrint)) ~> routes ~> check {
      status shouldBe StatusCodes.BadRequest
      val body = responseAs[ObservationsResponse]
      body.accepted shouldBe 0
      body.rejected should have size 3
    }

    val mixed = ObservationsRequest(
      observations = List(
        Observation("SUGAR_USD_KG", today, BigDecimal("0.51")),
        Observation("NOT_A_SERIES", today, BigDecimal(1))
      ),
      sourceNote = None
    )
    Post("/api/v1/analytics/market/observations", HttpEntity(ContentTypes.`application/json`, mixed.toJson.compactPrint)) ~> routes ~> check {
      status shouldBe StatusCodes.OK
      val body = responseAs[ObservationsResponse]
      body.accepted shouldBe 1
      body.rejected should have size 1
      body.rejected.head.seriesCode shouldBe "NOT_A_SERIES"
    }
  }

  "GET /margins after a commodity-only partial recompute" should "still return every SKU (AC-03/BDD-03)" in {
    val (_, repo, svc) = requireStack()
    val tomorrow = LocalDate.now().plusDays(1).toString
    Await.result(repo.upsertManualPrice("SUGAR_USD_KG", tomorrow, BigDecimal("0.52")), 30.seconds)
    Await.result(svc.recompute(Set("SUGAR_USD_KG"), Some(tomorrow)), 30.seconds)
    Get("/api/v1/analytics/margins") ~> routes ~> check {
      status shouldBe StatusCodes.OK
      responseAs[MarginsResponse].rows should have size 40
    }
  }
