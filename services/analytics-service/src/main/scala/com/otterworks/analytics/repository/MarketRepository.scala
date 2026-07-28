package com.otterworks.analytics.repository

import com.otterworks.analytics.db.AnalyticsDb
import com.otterworks.analytics.model.*
import slick.jdbc.GetResult
import slick.jdbc.PostgresProfile.api.*

import java.sql.Timestamp
import java.time.Instant
import scala.concurrent.{ExecutionContext, Future}

/**
 * Slick plain-SQL access to the market/margin tables (schema `analytics`).
 * All statements are parameterized via the `sql`/`sqlu` interpolators.
 */
class MarketRepository(db: AnalyticsDb)(using ec: ExecutionContext):

  private given GetResult[MarketSeries] = GetResult { r =>
    MarketSeries(r.nextString(), r.nextString(), r.nextString(), r.nextString(), r.nextString())
  }

  private given GetResult[PricePoint] = GetResult { r =>
    PricePoint(r.nextString(), r.nextString(), r.nextBigDecimal(), r.nextString())
  }

  private given GetResult[Product] = GetResult { r =>
    Product(
      r.nextString(), r.nextString(), r.nextString(), r.nextString(),
      r.nextBigDecimal(), r.nextBigDecimal(), r.nextBigDecimal(), r.nextBigDecimal(), r.nextString()
    )
  }

  private given GetResult[MarginRow] = GetResult { r =>
    MarginRow(
      r.nextString(), r.nextString(), r.nextString(), r.nextString(), r.nextBigDecimal(),
      r.nextBigDecimal(), r.nextBigDecimal(), r.nextBigDecimal(), r.nextBigDecimal(), r.nextBigDecimal()
    )
  }

  private given GetResult[MarginSeriesPoint] = GetResult { r =>
    MarginSeriesPoint(r.nextString(), r.nextBigDecimal())
  }

  // ── series & products ─────────────────────────────────────────

  def listSeries(): Future[Seq[MarketSeries]] =
    db.database.run(
      sql"""SELECT series_code, name, unit, currency, category
            FROM market_series ORDER BY series_code""".as[MarketSeries]
    )

  def upsertSeries(s: MarketSeries): Future[Int] =
    db.database.run(
      sqlu"""INSERT INTO market_series (series_code, name, unit, currency, category)
             VALUES (${s.seriesCode}, ${s.name}, ${s.unit}, ${s.currency}, ${s.category})
             ON CONFLICT (series_code) DO UPDATE
               SET name = EXCLUDED.name, unit = EXCLUDED.unit,
                   currency = EXCLUDED.currency, category = EXCLUDED.category"""
    )

  def listProducts(): Future[Seq[Product]] =
    db.database.run(
      sql"""SELECT sku, name, category, commodity_series_code, content_kg,
                   freight_kg, overhead_pct, list_price_usd, supplier
            FROM products ORDER BY sku""".as[Product]
    )

  def upsertProduct(p: Product): Future[Int] =
    db.database.run(
      sqlu"""INSERT INTO products
               (sku, name, category, commodity_series_code, content_kg,
                freight_kg, overhead_pct, list_price_usd, supplier)
             VALUES (${p.sku}, ${p.name}, ${p.category}, ${p.commoditySeriesCode}, ${p.contentKg},
                     ${p.freightKg}, ${p.overheadPct}, ${p.listPriceUsd}, ${p.supplier})
             ON CONFLICT (sku) DO UPDATE
               SET name = EXCLUDED.name, category = EXCLUDED.category,
                   commodity_series_code = EXCLUDED.commodity_series_code,
                   content_kg = EXCLUDED.content_kg, freight_kg = EXCLUDED.freight_kg,
                   overhead_pct = EXCLUDED.overhead_pct, list_price_usd = EXCLUDED.list_price_usd,
                   supplier = EXCLUDED.supplier"""
    )

  // ── prices ────────────────────────────────────────────────────

  def listPrices(seriesCode: String, from: Option[String], to: Option[String]): Future[Seq[PricePoint]] =
    val fromDate = from.getOrElse("0001-01-01")
    val toDate = to.getOrElse("9999-12-31")
    db.database.run(
      sql"""SELECT series_code, to_char(price_date, 'YYYY-MM-DD'), value, source
            FROM market_prices
            WHERE series_code = $seriesCode
              AND price_date >= ${fromDate}::date AND price_date <= ${toDate}::date
            ORDER BY price_date""".as[PricePoint]
    )

  /** All prices for a set of series, oldest first (for margin computation). */
  def pricesForSeries(seriesCodes: Seq[String]): Future[Seq[PricePoint]] =
    if seriesCodes.isEmpty then Future.successful(Seq.empty)
    else
      val codesArray = seriesCodes.mkString(",")
      db.database.run(
        sql"""SELECT series_code, to_char(price_date, 'YYYY-MM-DD'), value, source
              FROM market_prices
              WHERE series_code = ANY(string_to_array($codesArray, ','))
              ORDER BY series_code, price_date""".as[PricePoint]
      )

  /** Insert a synthetic observation, never overwriting an existing row. */
  def insertPriceIfAbsent(seriesCode: String, priceDate: String, value: BigDecimal): DBIO[Int] =
    sqlu"""INSERT INTO market_prices (series_code, price_date, value, source)
           VALUES ($seriesCode, ${priceDate}::date, $value, 'synthetic')
           ON CONFLICT (series_code, price_date) DO NOTHING"""

  /** Upsert a manually pulled observation (manual pull wins over synthetic). */
  def upsertManualPrice(seriesCode: String, priceDate: String, value: BigDecimal): Future[Int] =
    db.database.run(
      sqlu"""INSERT INTO market_prices (series_code, price_date, value, source)
             VALUES ($seriesCode, ${priceDate}::date, $value, 'manual_pull')
             ON CONFLICT (series_code, price_date) DO UPDATE
               SET value = EXCLUDED.value, source = 'manual_pull', created_at = NOW()"""
    )

  def run[T](action: DBIO[T]): Future[T] = db.database.run(action)

  /** Latest date with any margin computed (the dashboard "as of" date). */
  def latestMarginDate(): Future[Option[String]] =
    db.database.run(
      sql"""SELECT to_char(MAX(margin_date), 'YYYY-MM-DD') FROM product_margin_daily""".as[Option[String]].head
    )

  // ── margins ───────────────────────────────────────────────────

  def upsertMargin(m: MarginDaily): DBIO[Int] =
    sqlu"""INSERT INTO product_margin_daily
             (sku, margin_date, commodity_cost_usd, freight_cost_usd,
              overhead_cost_usd, cogs_usd, margin_pct, computed_at)
           VALUES (${m.sku}, ${m.marginDate}::date, ${m.commodityCostUsd}, ${m.freightCostUsd},
                   ${m.overheadCostUsd}, ${m.cogsUsd}, ${m.marginPct}, NOW())
           ON CONFLICT (sku, margin_date) DO UPDATE
             SET commodity_cost_usd = EXCLUDED.commodity_cost_usd,
                 freight_cost_usd = EXCLUDED.freight_cost_usd,
                 overhead_cost_usd = EXCLUDED.overhead_cost_usd,
                 cogs_usd = EXCLUDED.cogs_usd,
                 margin_pct = EXCLUDED.margin_pct,
                 computed_at = NOW()"""

  /** Grid rows: every product joined to its own most recent margin. */
  def marginRowsLatest(): Future[Seq[MarginRow]] =
    db.database.run(
      sql"""SELECT p.sku, p.name, p.category, p.supplier, p.list_price_usd,
                   m.commodity_cost_usd, m.freight_cost_usd, m.overhead_cost_usd,
                   m.cogs_usd, m.margin_pct
            FROM products p
            JOIN LATERAL (
              SELECT commodity_cost_usd, freight_cost_usd, overhead_cost_usd,
                     cogs_usd, margin_pct
              FROM product_margin_daily
              WHERE sku = p.sku
              ORDER BY margin_date DESC
              LIMIT 1
            ) m ON TRUE
            ORDER BY p.sku""".as[MarginRow]
    )

  def marginSeries(
      sku: Option[String],
      category: Option[String],
      from: Option[String],
      to: Option[String]
  ): Future[Seq[MarginSeriesPoint]] =
    val fromDate = from.getOrElse("0001-01-01")
    val toDate = to.getOrElse("9999-12-31")
    (sku, category) match
      case (Some(s), _) =>
        db.database.run(
          sql"""SELECT to_char(margin_date, 'YYYY-MM-DD'), margin_pct
                FROM product_margin_daily
                WHERE sku = $s AND margin_date >= ${fromDate}::date AND margin_date <= ${toDate}::date
                ORDER BY margin_date""".as[MarginSeriesPoint]
        )
      case (None, Some(c)) =>
        db.database.run(
          sql"""SELECT to_char(m.margin_date, 'YYYY-MM-DD'), ROUND(AVG(m.margin_pct), 4)
                FROM product_margin_daily m JOIN products p ON p.sku = m.sku
                WHERE p.category = $c
                  AND m.margin_date >= ${fromDate}::date AND m.margin_date <= ${toDate}::date
                GROUP BY m.margin_date ORDER BY m.margin_date""".as[MarginSeriesPoint]
        )
      case (None, None) =>
        db.database.run(
          sql"""SELECT to_char(margin_date, 'YYYY-MM-DD'), ROUND(AVG(margin_pct), 4)
                FROM product_margin_daily
                WHERE margin_date >= ${fromDate}::date AND margin_date <= ${toDate}::date
                GROUP BY margin_date ORDER BY margin_date""".as[MarginSeriesPoint]
        )

  // ── sync runs ─────────────────────────────────────────────────

  def insertSyncRun(
      runType: String,
      observationsCount: Int,
      startedAt: Instant,
      status: String,
      detail: Option[String]
  ): Future[Long] =
    db.database.run(
      sql"""INSERT INTO sync_runs (run_type, observations_count, started_at, completed_at, status, detail)
            VALUES ($runType, $observationsCount, ${Timestamp.from(startedAt)}, NOW(), $status, $detail)
            RETURNING id""".as[Long].head
    )

  /** Status for the badge/caption: latest run + whether any manual pull exists. */
  def marketStatus(): Future[MarketStatus] =
    val latestRun =
      sql"""SELECT run_type, to_char(completed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                   observations_count
            FROM sync_runs ORDER BY id DESC LIMIT 1""".as[(String, Option[String], Long)].headOption
    val manualExists =
      sql"""SELECT EXISTS(SELECT 1 FROM sync_runs WHERE run_type = 'manual_pull' AND status = 'succeeded')"""
        .as[Boolean].head
    val asOf =
      sql"""SELECT to_char(MAX(margin_date), 'YYYY-MM-DD') FROM product_margin_daily""".as[Option[String]].head
    db.database.run(for
      run <- latestRun
      manual <- manualExists
      asOfDate <- asOf
    yield MarketStatus(
      source = if manual then "manual_pull" else "synthetic",
      lastRunType = run.map(_._1),
      lastCompletedAt = run.flatMap(_._2),
      observationsCount = run.map(_._3).getOrElse(0L),
      asOfDate = asOfDate
    ))
