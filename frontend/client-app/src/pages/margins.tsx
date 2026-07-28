import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  DollarSign,
  Download,
  Percent,
  Ship,
  TrendingUp,
} from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { marginsApi } from "@/lib/api";
import type { MarginRow } from "@/lib/api";

const CHART_FROM_DAYS = 180;

function chartFromDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - CHART_FROM_DAYS);
  return d.toISOString().slice(0, 10);
}

export default function MarginsPage() {
  return (
    <AppShell>
      <ErrorBoundary>
        <MarginsContent />
      </ErrorBoundary>
    </AppShell>
  );
}

type SortKey = keyof Pick<
  MarginRow,
  "sku" | "name" | "category" | "supplier" | "listPriceUsd" | "cogsUsd" | "marginPct"
>;

function MarginsContent() {
  const {
    data: margins,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["margins", "dashboard"],
    queryFn: () => marginsApi.getMargins(),
  });

  const { data: salmonPrices } = useQuery({
    queryKey: ["margins", "salmon-prices"],
    queryFn: () => marginsApi.getMarketPrices("SALMON_NOK_KG", chartFromDate()),
  });

  const { data: freightPrices } = useQuery({
    queryKey: ["margins", "freight-prices"],
    queryFn: () => marginsApi.getMarketPrices("DREWRY_WCI_USD_FEU", chartFromDate()),
  });

  const { data: marginSeries } = useQuery({
    queryKey: ["margins", "series"],
    queryFn: () => marginsApi.getMarginSeries({ from: chartFromDate() }),
  });

  const [filter, setFilter] = useState("");
  const [category, setCategory] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("sku");
  const [sortAsc, setSortAsc] = useState(true);

  const categories = useMemo(
    () => Array.from(new Set(margins?.rows.map((r) => r.category) ?? [])).sort(),
    [margins],
  );

  const visibleRows = useMemo(() => {
    const term = filter.trim().toLowerCase();
    const rows = (margins?.rows ?? []).filter(
      (r) =>
        (category === "all" || r.category === category) &&
        (term === "" ||
          r.sku.toLowerCase().includes(term) ||
          r.name.toLowerCase().includes(term) ||
          r.supplier.toLowerCase().includes(term)),
    );
    const sorted = [...rows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv));
      return sortAsc ? cmp : -cmp;
    });
    return sorted;
  }, [margins, filter, category, sortKey, sortAsc]);

  const commodityChartData = useMemo(() => {
    const byDate = new Map<string, { date: string; salmon?: number; freight?: number }>();
    for (const p of salmonPrices?.prices ?? []) {
      byDate.set(p.priceDate, { ...(byDate.get(p.priceDate) ?? { date: p.priceDate }), salmon: p.value });
    }
    for (const p of freightPrices?.prices ?? []) {
      byDate.set(p.priceDate, { ...(byDate.get(p.priceDate) ?? { date: p.priceDate }), freight: p.value });
    }
    return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [salmonPrices, freightPrices]);

  const marginChartData = useMemo(
    () =>
      (marginSeries?.points ?? []).map((p) => ({
        date: p.marginDate,
        marginPct: p.marginPct,
      })),
    [marginSeries],
  );

  const onSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc((v) => !v);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const onExportCsv = async () => {
    try {
      const csv = await marginsApi.exportCsv();
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `sku-margins-${margins?.asOfDate ?? "export"}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Margins CSV exported");
    } catch {
      toast.error("CSV export failed. Please try again.");
    }
  };

  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto space-y-6" data-testid="margins-loading">
        <div className="h-8 w-64 bg-gray-200 rounded animate-pulse" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 bg-gray-100 border border-gray-200 rounded-xl animate-pulse" />
          ))}
        </div>
        <div className="h-72 bg-gray-100 border border-gray-200 rounded-xl animate-pulse" />
        <div className="h-96 bg-gray-100 border border-gray-200 rounded-xl animate-pulse" />
      </div>
    );
  }

  if (isError || !margins) {
    return (
      <div className="max-w-7xl mx-auto" data-testid="margins-error">
        <div className="bg-white border border-gray-200 rounded-xl p-10 text-center">
          <h1 className="text-lg font-semibold text-gray-900">
            Margins data is unavailable
          </h1>
          <p className="text-sm text-gray-500 mt-2">
            We couldn&apos;t load the margins dashboard. Please try again.
          </p>
          <button
            onClick={() => refetch()}
            className="mt-4 px-4 py-2 bg-otter-600 text-white rounded-lg hover:bg-otter-700 transition text-sm font-medium"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const sourceLive = margins.source !== "synthetic";

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Margins</h1>
          <p className="text-sm text-gray-500 mt-1" data-testid="margins-caption">
            Data as of {margins.asOfDate}
            {margins.lastSyncAt ? ` (${margins.lastSyncAt})` : ""} — Source: Trading
            Economics (manual pull)
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            data-testid="source-badge"
            className={`px-2.5 py-1 rounded-full text-xs font-medium border ${
              sourceLive
                ? "bg-green-50 text-green-700 border-green-200"
                : "bg-gray-100 text-gray-600 border-gray-200"
            }`}
          >
            {sourceLive ? "live" : "synthetic"}
          </span>
          <button
            onClick={onExportCsv}
            className="flex items-center gap-2 px-4 py-2 bg-white text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50 transition text-sm font-medium"
          >
            <Download size={16} />
            Export CSV
          </button>
        </div>
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiTile
          icon={Percent}
          label="Gross Margin %"
          value={`${margins.kpis.grossMarginPct.toFixed(1)}%`}
          color="green"
        />
        <KpiTile
          icon={DollarSign}
          label="COGS / unit"
          value={`$${margins.kpis.avgCogsUsd.toFixed(2)}`}
          color="blue"
        />
        <KpiTile
          icon={TrendingUp}
          label="Salmon Index"
          value={`${margins.kpis.salmonIndex.toFixed(2)} NOK/kg`}
          color="orange"
        />
        <KpiTile
          icon={Ship}
          label="Freight Index"
          value={`$${margins.kpis.freightIndex.toFixed(0)}/FEU`}
          color="purple"
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-5" data-testid="commodity-chart">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            Commodity &amp; freight prices ({CHART_FROM_DAYS}d)
          </h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={commodityChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
              <YAxis yAxisId="salmon" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="freight" orientation="right" tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Line
                yAxisId="salmon"
                type="monotone"
                dataKey="salmon"
                name="Salmon (NOK/kg)"
                stroke="#ea580c"
                dot={false}
                strokeWidth={1.5}
              />
              <Line
                yAxisId="freight"
                type="monotone"
                dataKey="freight"
                name="Freight (USD/FEU)"
                stroke="#7c3aed"
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-5" data-testid="margin-chart">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            Average margin % ({CHART_FROM_DAYS}d)
          </h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={marginChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey="marginPct"
                name="Avg margin %"
                stroke="#16a34a"
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* SKU margins grid */}
      <div className="bg-white rounded-xl border border-gray-200">
        <div className="flex items-center justify-between gap-3 flex-wrap p-4 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-700">
            SKU profitability ({visibleRows.length} of {margins.rows.length})
          </h2>
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Filter by SKU, name, supplier…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm w-64 focus:outline-none focus:ring-2 focus:ring-otter-500"
              data-testid="grid-filter"
            />
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-otter-500"
              data-testid="category-filter"
            >
              <option value="all">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="margins-grid">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-200 bg-gray-50">
                <SortableTh label="SKU" active={sortKey === "sku"} asc={sortAsc} onClick={() => onSort("sku")} />
                <SortableTh label="Product" active={sortKey === "name"} asc={sortAsc} onClick={() => onSort("name")} />
                <SortableTh label="Category" active={sortKey === "category"} asc={sortAsc} onClick={() => onSort("category")} />
                <SortableTh label="Supplier" active={sortKey === "supplier"} asc={sortAsc} onClick={() => onSort("supplier")} />
                <SortableTh label="List Price" active={sortKey === "listPriceUsd"} asc={sortAsc} onClick={() => onSort("listPriceUsd")} right />
                <SortableTh label="COGS" active={sortKey === "cogsUsd"} asc={sortAsc} onClick={() => onSort("cogsUsd")} right />
                <SortableTh label="Margin %" active={sortKey === "marginPct"} asc={sortAsc} onClick={() => onSort("marginPct")} right />
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={row.sku} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-2.5 font-medium text-gray-900">{row.sku}</td>
                  <td className="px-4 py-2.5 text-gray-700">{row.name}</td>
                  <td className="px-4 py-2.5 text-gray-500">{row.category}</td>
                  <td className="px-4 py-2.5 text-gray-500">{row.supplier}</td>
                  <td className="px-4 py-2.5 text-right text-gray-700">
                    ${row.listPriceUsd.toFixed(2)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-700">
                    ${row.cogsUsd.toFixed(2)}
                  </td>
                  <td
                    className={`px-4 py-2.5 text-right font-medium ${
                      row.marginPct < 0
                        ? "text-red-600"
                        : row.marginPct < 20
                          ? "text-orange-600"
                          : "text-green-700"
                    }`}
                  >
                    {row.marginPct.toFixed(1)}%
                  </td>
                </tr>
              ))}
              {visibleRows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                    No SKUs match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function KpiTile({
  icon: Icon,
  label,
  value,
  color,
}: Readonly<{
  icon: typeof Percent;
  label: string;
  value: string;
  color: "blue" | "purple" | "green" | "orange";
}>) {
  const colorClasses = {
    blue: "bg-blue-50 text-blue-600",
    purple: "bg-purple-50 text-purple-600",
    green: "bg-green-50 text-green-600",
    orange: "bg-orange-50 text-orange-600",
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5" data-testid={`kpi-${label}`}>
      <div className="flex items-center gap-3">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${colorClasses[color]}`}>
          <Icon size={20} />
        </div>
        <div>
          <p className="text-2xl font-bold text-gray-900">{value}</p>
          <p className="text-xs text-gray-500">{label}</p>
        </div>
      </div>
    </div>
  );
}

function SortableTh({
  label,
  active,
  asc,
  onClick,
  right,
}: Readonly<{
  label: string;
  active: boolean;
  asc: boolean;
  onClick: () => void;
  right?: boolean;
}>) {
  let SortIcon = ArrowUpDown;
  if (active) SortIcon = asc ? ArrowUp : ArrowDown;
  return (
    <th className={`px-4 py-2.5 font-medium ${right ? "text-right" : ""}`}>
      <button
        onClick={onClick}
        className={`inline-flex items-center gap-1 hover:text-gray-800 ${active ? "text-gray-800" : ""}`}
      >
        {label}
        <SortIcon size={12} />
      </button>
    </th>
  );
}
