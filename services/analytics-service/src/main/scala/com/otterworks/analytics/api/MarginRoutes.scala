package com.otterworks.analytics.api

import akka.http.scaladsl.marshallers.sprayjson.SprayJsonSupport.*
import akka.http.scaladsl.model.{ContentTypes, HttpEntity}
import akka.http.scaladsl.server.Directives.*
import akka.http.scaladsl.server.Route
import com.otterworks.analytics.model.*
import com.otterworks.analytics.model.MarketJsonProtocol.{*, given}
import com.otterworks.analytics.repository.MarketRepository
import com.otterworks.analytics.service.MarginService

/**
 * Margin query routes:
 *   GET /api/v1/analytics/margins
 *   GET /api/v1/analytics/margins/series?sku=&category=&from=&to=
 *   GET /api/v1/analytics/margins/export?format=csv
 */
class MarginRoutes(marginService: MarginService, repo: MarketRepository):

  val routes: Route = pathPrefix("api" / "v1" / "analytics" / "margins") {
    concat(
      pathEndOrSingleSlash {
        get {
          onSuccess(marginService.marginsDashboard()) { response =>
            complete(response)
          }
        }
      },
      path("series") {
        get {
          parameters("sku".optional, "category".optional, "from".optional, "to".optional) {
            (sku, category, from, to) =>
              onSuccess(repo.marginSeries(sku, category, from, to)) { points =>
                complete(MarginSeriesResponse(sku, category, points.toList))
              }
          }
        }
      },
      path("export") {
        get {
          parameters("format".withDefault("json")) { format =>
            format match
              case "csv" =>
                onSuccess(marginService.marginsDashboard()) { response =>
                  complete(HttpEntity(ContentTypes.`text/plain(UTF-8)`, buildCsvContent(response.rows)))
                }
              case _ =>
                onSuccess(marginService.marginsDashboard()) { response =>
                  complete(response)
                }
          }
        }
      },
    )
  }

  private def buildCsvContent(rows: List[MarginRow]): String =
    val headers = List(
      "sku", "name", "category", "supplier", "list_price_usd",
      "commodity_cost_usd", "freight_cost_usd", "overhead_cost_usd", "cogs_usd", "margin_pct"
    )
    val lines = rows.map { r =>
      List(
        r.sku, r.name, r.category, r.supplier, r.listPriceUsd.toString,
        r.commodityCostUsd.toString, r.freightCostUsd.toString, r.overheadCostUsd.toString,
        r.cogsUsd.toString, r.marginPct.toString
      ).map(escapeCsvField).mkString(",")
    }
    (headers.mkString(",") :: lines).mkString("\n") + "\n"

  private def escapeCsvField(value: String): String =
    if value.contains(",") || value.contains("\"") || value.contains("\n") then
      "\"" + value.replace("\"", "\"\"") + "\""
    else value
