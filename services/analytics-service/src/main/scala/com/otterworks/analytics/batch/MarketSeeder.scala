package com.otterworks.analytics.batch

import com.otterworks.analytics.model.{MarketSeries, Product}
import com.otterworks.analytics.repository.MarketRepository
import com.otterworks.analytics.service.MarginService
import org.slf4j.LoggerFactory
import slick.jdbc.PostgresProfile.api.*

import java.nio.charset.StandardCharsets
import java.time.{Instant, LocalDate}
import java.util.Random
import scala.concurrent.duration.*
import scala.concurrent.{Await, ExecutionContext, Future}
import scala.util.Using

/**
 * Idempotent startup seeder for the market/margin tables.
 *
 * Loads the bundled market-series catalog (classpath copies of the CSVs in
 * `testdata/market-series`), inserts the committed baseline history and
 * deterministically extends every series from its last baseline date to
 * "today" with a seeded random walk (seed = series_code.hashCode ^ epochDay),
 * so every environment generates identical values. Existing rows are never
 * overwritten (`ON CONFLICT DO NOTHING`), so manually pulled values win and a
 * re-boot on the same day inserts nothing new.
 */
object MarketSeeder:
  private val logger = LoggerFactory.getLogger(getClass)

  private val ResourceBase = "/seed/market-series"

  /** Fixed per-series daily sigma for the random-walk extension. */
  val DailySigma: Map[String, Double] = Map(
    "SALMON_NOK_KG" -> 0.012,
    "SHRIMP_USD_KG" -> 0.008,
    "SOYBEAN_OIL_USD_KG" -> 0.010,
    "SUGAR_USD_KG" -> 0.011,
    "COTTON_USD_KG" -> 0.009,
    "DREWRY_WCI_USD_FEU" -> 0.015,
    "USD_NOK" -> 0.004
  )

  private[batch] def readResource(name: String): String =
    val stream = Option(getClass.getResourceAsStream(s"$ResourceBase/$name"))
      .getOrElse(throw new IllegalArgumentException(s"Seed resource not found: $ResourceBase/$name"))
    Using.resource(stream)(in => new String(in.readAllBytes(), StandardCharsets.UTF_8))

  private[batch] def csvRows(content: String): List[Array[String]] =
    content.linesIterator
      .map(_.trim)
      .filter(_.nonEmpty)
      .drop(1) // header
      .map(_.split(",", -1).map(_.trim))
      .toList

  def parseSeries(content: String): List[MarketSeries] =
    csvRows(content).map(f => MarketSeries(f(0), f(1), f(2), f(3), f(4)))

  def parseProducts(content: String): List[Product] =
    csvRows(content).map { f =>
      Product(f(0), f(1), f(2), f(3), BigDecimal(f(4)), BigDecimal(f(5)), BigDecimal(f(6)), BigDecimal(f(7)), f(8))
    }

  def parseBaselinePrices(content: String): List[(String, String, BigDecimal)] =
    csvRows(content).map(f => (f(0), f(1), BigDecimal(f(2))))

  /** Deterministic random-walk value for the day after `prev`. */
  def nextWalkValue(seriesCode: String, date: LocalDate, prev: BigDecimal): BigDecimal =
    val sigma = DailySigma.getOrElse(seriesCode, 0.01)
    val rng = new Random(seriesCode.hashCode.toLong ^ date.toEpochDay)
    val z = rng.nextGaussian()
    (prev * BigDecimal(1 + sigma * z)).max(BigDecimal("0.01")).setScale(6, BigDecimal.RoundingMode.HALF_UP)

  /** Extend a series from its last baseline observation up to `today` (exclusive of the baseline date). */
  def walkExtension(
      seriesCode: String,
      lastDate: LocalDate,
      lastValue: BigDecimal,
      today: LocalDate
  ): List[(String, String, BigDecimal)] =
    Iterator
      .iterate((lastDate, lastValue)) { case (d, v) =>
        val next = d.plusDays(1)
        (next, nextWalkValue(seriesCode, next, v))
      }
      .drop(1)
      .takeWhile { case (d, _) => !d.isAfter(today) }
      .map { case (d, v) => (seriesCode, d.toString, v) }
      .toList

  /** Run the seed synchronously at startup; returns the number of price rows inserted. */
  def run(repo: MarketRepository, marginService: MarginService, today: LocalDate = LocalDate.now())(using
      ec: ExecutionContext
  ): Int =
    val startedAt = Instant.now()
    val series = parseSeries(readResource("series.csv"))
    val products = parseProducts(readResource("products.csv"))
    val baseline = parseBaselinePrices(readResource("baseline_prices.csv"))

    val lastBaseline: Map[String, (String, BigDecimal)] =
      baseline.groupBy(_._1).view.mapValues(rows => rows.maxBy(_._2)).mapValues(r => (r._2, r._3)).toMap

    val extension = series.flatMap { s =>
      lastBaseline.get(s.seriesCode).toList.flatMap { case (dateStr, value) =>
        walkExtension(s.seriesCode, LocalDate.parse(dateStr), value, today)
      }
    }

    val seed: Future[Int] = for
      _ <- Future.sequence(series.map(repo.upsertSeries))
      _ <- Future.sequence(products.map(repo.upsertProduct))
      inserted <- {
        val actions = (baseline ++ extension).map { case (code, date, value) =>
          repo.insertPriceIfAbsent(code, date, value)
        }
        Future
          .sequence(actions.grouped(500).map(g => repo.run(DBIO.sequence(g).transactionally)).toList)
          .map(_.flatten.sum)
      }
      marginDate <- repo.latestMarginDate()
      _ <-
        if inserted > 0 || marginDate.isEmpty then marginService.recompute()
        else Future.successful(0)
      _ <-
        if inserted > 0 then
          repo.insertSyncRun("baseline_seed", inserted, startedAt, "succeeded", Some("deterministic synthetic baseline"))
        else Future.successful(0L)
    yield inserted

    val inserted = Await.result(seed, 5.minutes)
    logger.info(
      "Market seed complete: {} series, {} products, {} new price rows",
      series.size, products.size, inserted
    )
    inserted
