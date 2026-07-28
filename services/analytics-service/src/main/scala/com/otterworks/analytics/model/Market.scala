package com.otterworks.analytics.model

import spray.json.*

/** A commodity/freight/FX series from the shared market-series catalog. */
final case class MarketSeries(
    seriesCode: String,
    name: String,
    unit: String,
    currency: String,
    category: String
)

/** A daily observation for a series. */
final case class PricePoint(
    seriesCode: String,
    priceDate: String,
    value: BigDecimal,
    source: String
)

/** A product from the otter-retail catalog. */
final case class Product(
    sku: String,
    name: String,
    category: String,
    commoditySeriesCode: String,
    contentKg: BigDecimal,
    freightKg: BigDecimal,
    overheadPct: BigDecimal,
    listPriceUsd: BigDecimal,
    supplier: String
)

/** Computed margin for one SKU on one date. */
final case class MarginDaily(
    sku: String,
    marginDate: String,
    commodityCostUsd: BigDecimal,
    freightCostUsd: BigDecimal,
    overheadCostUsd: BigDecimal,
    cogsUsd: BigDecimal,
    marginPct: BigDecimal
)

/** Grid row for GET /margins (product ⋈ latest margin). */
final case class MarginRow(
    sku: String,
    name: String,
    category: String,
    supplier: String,
    listPriceUsd: BigDecimal,
    commodityCostUsd: BigDecimal,
    freightCostUsd: BigDecimal,
    overheadCostUsd: BigDecimal,
    cogsUsd: BigDecimal,
    marginPct: BigDecimal
)

/** KPI aggregates for the dashboard header tiles. */
final case class MarginKpis(
    grossMarginPct: BigDecimal,
    avgCogsUsd: BigDecimal,
    salmonIndex: BigDecimal,
    freightIndex: BigDecimal
)

/** Response for GET /api/v1/analytics/margins. */
final case class MarginsResponse(
    asOfDate: String,
    source: String,
    lastSyncAt: Option[String],
    kpis: MarginKpis,
    rows: List[MarginRow]
)

/** One point of a margin time series. */
final case class MarginSeriesPoint(marginDate: String, marginPct: BigDecimal)

/** Response for GET /api/v1/analytics/margins/series. */
final case class MarginSeriesResponse(
    sku: Option[String],
    category: Option[String],
    points: List[MarginSeriesPoint]
)

/** Latest sync/seed run, for the source badge + data-as-of caption. */
final case class MarketStatus(
    source: String,
    lastRunType: Option[String],
    lastCompletedAt: Option[String],
    observationsCount: Long,
    asOfDate: Option[String]
)

/** One observation pushed by the manual Trading Economics puller. */
final case class Observation(seriesCode: String, priceDate: String, value: BigDecimal)

/** Request body for POST /api/v1/analytics/market/observations. */
final case class ObservationsRequest(
    observations: List[Observation],
    sourceNote: Option[String]
)

/** An observation rejected by per-item validation. */
final case class RejectedObservation(seriesCode: String, priceDate: String, reason: String)

/** Response for POST /api/v1/analytics/market/observations. */
final case class ObservationsResponse(
    accepted: Int,
    rejected: List[RejectedObservation],
    recomputedSkus: Int,
    runId: Long
)

/** Spray JSON formats — snake_case on the wire (auto-camelized by the web client). */
object MarketJsonProtocol extends DefaultJsonProtocol:
  given marketSeriesFormat: RootJsonFormat[MarketSeries] =
    jsonFormat(MarketSeries.apply, "series_code", "name", "unit", "currency", "category")

  given pricePointFormat: RootJsonFormat[PricePoint] =
    jsonFormat(PricePoint.apply, "series_code", "price_date", "value", "source")

  given productFormat: RootJsonFormat[Product] =
    jsonFormat(
      Product.apply,
      "sku", "name", "category", "commodity_series_code", "content_kg",
      "freight_kg", "overhead_pct", "list_price_usd", "supplier"
    )

  given marginRowFormat: RootJsonFormat[MarginRow] =
    jsonFormat(
      MarginRow.apply,
      "sku", "name", "category", "supplier", "list_price_usd", "commodity_cost_usd",
      "freight_cost_usd", "overhead_cost_usd", "cogs_usd", "margin_pct"
    )

  given marginKpisFormat: RootJsonFormat[MarginKpis] =
    jsonFormat(MarginKpis.apply, "gross_margin_pct", "avg_cogs_usd", "salmon_index", "freight_index")

  given marginsResponseFormat: RootJsonFormat[MarginsResponse] =
    jsonFormat(MarginsResponse.apply, "as_of_date", "source", "last_sync_at", "kpis", "rows")

  given marginSeriesPointFormat: RootJsonFormat[MarginSeriesPoint] =
    jsonFormat(MarginSeriesPoint.apply, "margin_date", "margin_pct")

  given marginSeriesResponseFormat: RootJsonFormat[MarginSeriesResponse] =
    jsonFormat(MarginSeriesResponse.apply, "sku", "category", "points")

  given marketStatusFormat: RootJsonFormat[MarketStatus] =
    jsonFormat(
      MarketStatus.apply,
      "source", "last_run_type", "last_completed_at", "observations_count", "as_of_date"
    )

  given observationFormat: RootJsonFormat[Observation] =
    jsonFormat(Observation.apply, "series_code", "price_date", "value")

  given observationsRequestFormat: RootJsonFormat[ObservationsRequest] =
    jsonFormat(ObservationsRequest.apply, "observations", "source_note")

  given rejectedObservationFormat: RootJsonFormat[RejectedObservation] =
    jsonFormat(RejectedObservation.apply, "series_code", "price_date", "reason")

  given observationsResponseFormat: RootJsonFormat[ObservationsResponse] =
    jsonFormat(ObservationsResponse.apply, "accepted", "rejected", "recomputed_skus", "run_id")
