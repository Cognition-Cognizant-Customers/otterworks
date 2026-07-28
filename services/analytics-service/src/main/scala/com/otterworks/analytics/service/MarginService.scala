package com.otterworks.analytics.service

import com.otterworks.analytics.model.*
import com.otterworks.analytics.repository.MarketRepository
import org.slf4j.LoggerFactory
import slick.dbio.DBIO

import java.time.{Instant, LocalDate}
import scala.concurrent.{ExecutionContext, Future}
import scala.math.BigDecimal.RoundingMode
import scala.util.Try

/**
 * Margin computation and market-observation ingestion.
 *
 * Margin model (locked by OTD-15, see testdata/market-series/README.md):
 *   commodity_cost_usd = price(native) × fx_to_usd × content_kg   (NOK: ÷ USD_NOK)
 *   freight_cost_usd   = (DREWRY_WCI_USD_FEU / FEU kg) × freight_kg
 *   cogs_usd           = (commodity + freight) × (1 + overhead_pct/100)
 *   margin_pct         = (list_price − cogs) / list_price × 100
 */
object MarginService:
  val FxSeriesCode = "USD_NOK"
  val FreightSeriesCode = "DREWRY_WCI_USD_FEU"
  val SalmonSeriesCode = "SALMON_NOK_KG"
  val KgPerFeu: BigDecimal = BigDecimal(25000)

  /** Pure margin computation for one product on one date. */
  def computeMargin(
      product: Product,
      commodityPrice: BigDecimal,
      commodityCurrency: String,
      usdNok: BigDecimal,
      wciUsdFeu: BigDecimal
  ): MarginDaily =
    val priceUsd =
      if commodityCurrency == "NOK" then commodityPrice / usdNok else commodityPrice
    val commodityCost = (priceUsd * product.contentKg).setScale(4, RoundingMode.HALF_UP)
    val freightCost = (wciUsdFeu / KgPerFeu * product.freightKg).setScale(4, RoundingMode.HALF_UP)
    val overheadCost =
      ((commodityCost + freightCost) * product.overheadPct / 100).setScale(4, RoundingMode.HALF_UP)
    val cogs = (commodityCost + freightCost + overheadCost).setScale(4, RoundingMode.HALF_UP)
    val marginPct =
      if product.listPriceUsd == 0 then BigDecimal(0)
      else ((product.listPriceUsd - cogs) / product.listPriceUsd * 100).setScale(4, RoundingMode.HALF_UP)
    MarginDaily(product.sku, "", commodityCost, freightCost, overheadCost, cogs, marginPct)

class MarginService(repo: MarketRepository)(using ec: ExecutionContext):
  import MarginService.*

  private val logger = LoggerFactory.getLogger(getClass)

  /** Most recent value at or before `date` in a date-sorted (date, value) vector. */
  private def latestAtOrBefore(sorted: Vector[(String, BigDecimal)], date: String): Option[BigDecimal] =
    sorted.takeWhile(_._1 <= date).lastOption.map(_._2)

  /**
   * Recompute product_margin_daily. When `affectedSeries` is empty all SKUs and
   * dates are recomputed; otherwise only SKUs driven by the affected series
   * (FX/freight changes affect every SKU) from `fromDate` onward.
   * Returns the number of SKUs recomputed.
   */
  def recompute(affectedSeries: Set[String] = Set.empty, fromDate: Option[String] = None): Future[Int] =
    for
      seriesMeta <- repo.listSeries()
      products <- repo.listProducts()
      allCodes = seriesMeta.map(_.seriesCode)
      prices <- repo.pricesForSeries(allCodes)
      count <- {
        val currencyByCode = seriesMeta.map(s => s.seriesCode -> s.currency).toMap
        val byCode: Map[String, Vector[(String, BigDecimal)]] =
          prices.groupBy(_.seriesCode).view.mapValues(_.map(p => (p.priceDate, p.value)).toVector.sortBy(_._1)).toMap
        val fx = byCode.getOrElse(FxSeriesCode, Vector.empty)
        val freight = byCode.getOrElse(FreightSeriesCode, Vector.empty)

        val globalChange =
          affectedSeries.isEmpty || affectedSeries.contains(FxSeriesCode) || affectedSeries.contains(FreightSeriesCode)
        val targetProducts =
          if globalChange then products
          else products.filter(p => affectedSeries.contains(p.commoditySeriesCode))

        val upserts = targetProducts.flatMap { product =>
          val commodity = byCode.getOrElse(product.commoditySeriesCode, Vector.empty)
          val currency = currencyByCode.getOrElse(product.commoditySeriesCode, "USD")
          commodity
            .filter { case (d, _) => fromDate.forall(d >= _) }
            .flatMap { case (date, price) =>
              for
                usdNok <- latestAtOrBefore(fx, date)
                wci <- latestAtOrBefore(freight, date)
              yield computeMargin(product, price, currency, usdNok, wci).copy(marginDate = date)
            }
            .map(repo.upsertMargin)
        }
        repo.run(DBIO.sequence(upserts.grouped(500).map(g => DBIO.seq(g.toSeq*)).toSeq)).map(_ => targetProducts.size)
      }
    yield count

  /** Validate and ingest manually pulled observations, then recompute margins. */
  def ingest(request: ObservationsRequest): Future[Either[List[RejectedObservation], ObservationsResponse]] =
    val startedAt = Instant.now()
    val today = LocalDate.now()
    repo.listSeries().flatMap { seriesMeta =>
      val knownCodes = seriesMeta.map(_.seriesCode).toSet

      def validate(o: Observation): Option[RejectedObservation] =
        if !knownCodes.contains(o.seriesCode) then
          Some(RejectedObservation(o.seriesCode, o.priceDate, s"unknown series_code '${o.seriesCode}'"))
        else
          Try(LocalDate.parse(o.priceDate)).toOption match
            case None => Some(RejectedObservation(o.seriesCode, o.priceDate, "invalid price_date (expected YYYY-MM-DD)"))
            case Some(d) if d.isAfter(today) =>
              Some(RejectedObservation(o.seriesCode, o.priceDate, "price_date is in the future"))
            case Some(_) if o.value <= 0 =>
              Some(RejectedObservation(o.seriesCode, o.priceDate, "value must be positive"))
            case Some(_) => None

      val results = request.observations.map(o => (o, validate(o)))
      val rejected = results.collect { case (_, Some(r)) => r }
      val valid = results.collect { case (o, None) => o }

      if valid.isEmpty then Future.successful(Left(rejected))
      else
        for
          _ <- Future.sequence(valid.map(o => repo.upsertManualPrice(o.seriesCode, o.priceDate, o.value)))
          recomputed <- recompute(valid.map(_.seriesCode).toSet, Some(valid.map(_.priceDate).min))
          runId <- repo.insertSyncRun(
            "manual_pull", valid.size, startedAt, "succeeded",
            request.sourceNote.orElse(Some("manual pull"))
          )
        yield
          logger.info("Ingested {} market observations (run {}), recomputed {} SKUs", valid.size, runId, recomputed)
          Right(ObservationsResponse(valid.size, rejected, recomputed, runId))
    }

  /** Data for GET /margins: KPI aggregates + grid rows on the latest date. */
  def marginsDashboard(): Future[MarginsResponse] =
    for
      rows <- repo.marginRowsLatest()
      status <- repo.marketStatus()
      salmon <- repo.listPrices(SalmonSeriesCode, None, None).map(_.lastOption.map(_.value))
      freight <- repo.listPrices(FreightSeriesCode, None, None).map(_.lastOption.map(_.value))
    yield
      val n = math.max(rows.size, 1)
      val kpis = MarginKpis(
        grossMarginPct = (rows.map(_.marginPct).sum / n).setScale(2, RoundingMode.HALF_UP),
        avgCogsUsd = (rows.map(_.cogsUsd).sum / n).setScale(2, RoundingMode.HALF_UP),
        salmonIndex = salmon.getOrElse(BigDecimal(0)).setScale(2, RoundingMode.HALF_UP),
        freightIndex = freight.getOrElse(BigDecimal(0)).setScale(2, RoundingMode.HALF_UP)
      )
      MarginsResponse(
        asOfDate = status.asOfDate.getOrElse(""),
        source = status.source,
        lastSyncAt = status.lastCompletedAt,
        kpis = kpis,
        rows = rows.toList
      )
