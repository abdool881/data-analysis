import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity, ArrowRight, ArrowUpRight, Bell, Boxes, ChartNoAxesCombined,
  Check, ChevronDown, CircleAlert, CreditCard, LayoutDashboard, LockKeyhole, LogOut,
  Menu, PackagePlus, Plus, RefreshCw, Search, ShoppingBag, ShoppingCart, Sparkles,
  Users, Wallet, X, ChartColumnIncreasing,
} from "lucide-react";
import {
  api, type AlertItem, type Customer, type Dashboard, type Debt, type Product,
  registerShop, type Sale, type SalesAnalytics, type Shop,
} from "./api";

type Page = "Overview" | "Inventory" | "Sales" | "Analytics" | "Customer debts" | "Alerts";
type Modal = "product" | "sale" | "customer" | "restock" | "payment" | null;
type InventoryRow = {
  product_id: number;
  name: string;
  unit_name: string;
  stock: number;
  reorder_level: number;
  below_reorder_level: boolean;
  unit_cost: number;
  selling_price: number;
  stock_cost_value: number;
  potential_sales_value: number;
  potential_gross_profit: number;
};
type InventorySummary = {
  product_count: number;
  products_in_stock: number;
  low_stock_count: number;
  stock_cost_value: number;
  potential_sales_value: number;
  potential_gross_profit: number;
};
type ProductList = { items: Product[] };
type SalesList = { items: Sale[] };
type CustomerList = { items: Customer[] };
type AlertList = { items: AlertItem[] };

const navigation: { name: Page; icon: typeof LayoutDashboard }[] = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Inventory", icon: Boxes },
  { name: "Sales", icon: ShoppingBag },
  { name: "Analytics", icon: ChartColumnIncreasing },
  { name: "Customer debts", icon: CreditCard },
  { name: "Alerts", icon: Bell },
];

const dateToday = () => {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
};
const startOfWindow = () => {
  const d = new Date();
  d.setDate(d.getDate() - 29);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const fmtMoney = (value: number | null | undefined, currency = "NGN") =>
  new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format(value ?? 0);
const fmtNumber = (value: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
const fmtDate = (value: string) => new Date(value.length === 10 ? `${value}T00:00:00` : value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
const humanize = (value: string) => value.replaceAll("_", " ");

function App() {
  const [apiKey, setApiKey] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [signupToken, setSignupToken] = useState("");
  const [shopName, setShopName] = useState("");
  const [currencyCode, setCurrencyCode] = useState("NGN");
  const [setupMode, setSetupMode] = useState<"create" | "existing">("create");
  const [shop, setShop] = useState<Shop | null>(null);
  const [page, setPage] = useState<Page>("Overview");
  const [modal, setModal] = useState<Modal>(null);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [selectedDebt, setSelectedDebt] = useState<Debt | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [inventorySummary, setInventorySummary] = useState<InventorySummary | null>(null);
  const [sales, setSales] = useState<Sale[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [analytics, setAnalytics] = useState<SalesAnalytics>({ periods: [] });
  const [analyticsStart, setAnalyticsStart] = useState(startOfWindow);
  const [analyticsEnd, setAnalyticsEnd] = useState(dateToday);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [mobileMenu, setMobileMenu] = useState(false);

  const refresh = useCallback(async (key = apiKey) => {
    if (!key) return;
    setLoading(true);
    setError("");
    try {
      const [shopData, dashboardData, productData, inventoryData, salesData, customerData, alertData, analyticsData] =
        await Promise.all([
          api<Shop>("/v1/shop", key),
          api<Dashboard>("/v1/dashboard", key),
          api<ProductList>("/v1/products?limit=200", key),
          api<{ items: InventoryRow[]; summary: InventorySummary }>("/v1/inventory", key),
          api<SalesList>("/v1/sales?limit=100", key),
          api<CustomerList>("/v1/customers?limit=200", key),
          api<AlertList>("/v1/alerts?state=open&limit=100", key),
          api<SalesAnalytics>(`/v1/analytics/sales?freq=D&start=${startOfWindow()}&end=${dateToday()}`, key),
        ]);
      setShop(shopData);
      setDashboard(dashboardData);
      setProducts(productData.items);
      setInventory(inventoryData.items);
      setInventorySummary(inventoryData.summary);
      setSales(salesData.items);
      setCustomers(customerData.items);
      setAlerts(alertData.items);
      setAnalytics(analyticsData);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Something went wrong while loading the shop.";
      setError(message);
      if (message.includes("Invalid shop API key") || message.includes("shop API key")) {
        setApiKey("");
        setShop(null);
      }
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => {
    if (apiKey) void refresh(apiKey);
  }, [apiKey, refresh]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 3800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const currency = shop?.currency_code ?? "NGN";
  const debtRows = dashboard?.debts.debts ?? [];
  const openDebts = debtRows.filter((debt) => debt.amount_due > 0);
  const filteredProducts = useMemo(() => products.filter((p) => p.name.toLowerCase().includes(query.toLowerCase())), [products, query]);
  const filteredSales = useMemo(() => sales.filter((s) =>
    `${s.product} ${s.customer_name ?? ""}`.toLowerCase().includes(query.toLowerCase())), [sales, query]);
  const filteredDebts = useMemo(() => openDebts.filter((d) =>
    `${d.customer_name ?? ""} ${d.customer_phone} ${d.product}`.toLowerCase().includes(query.toLowerCase())), [openDebts, query]);
  function navigate(nextPage: Page) {
    setPage(nextPage);
    setQuery("");
    setMobileMenu(false);
    if (nextPage === "Analytics") void loadDetailedAnalytics(analyticsStart, analyticsEnd);
  }

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = keyDraft.trim();
    if (!key) return;
    setApiKey(key);
  }

  async function loadDetailedAnalytics(start: string, end: string) {
    if (!apiKey) return;
    if (start > end) {
      setError("The start date must be on or before the end date.");
      return;
    }
    const span = (Date.parse(`${end}T00:00:00`) - Date.parse(`${start}T00:00:00`)) / 86_400_000;
    if (span > 365) {
      setError("Choose a date range of 366 days or less.");
      return;
    }
    setAnalyticsLoading(true);
    setError("");
    try {
      const range = `start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
      const [salesData, productData, weekdayData] = await Promise.all([
        api<SalesAnalytics>(`/v1/analytics/sales?freq=D&${range}`, apiKey),
        api<SalesAnalytics>(`/v1/analytics/products?${range}`, apiKey),
        api<SalesAnalytics>(`/v1/analytics/weekdays?${range}`, apiKey),
      ]);
      setAnalytics({ ...salesData, products: productData.products, weekdays: weekdayData.weekdays });
      setAnalyticsStart(start);
      setAnalyticsEnd(end);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Analytics could not be loaded.");
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function createAndConnect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking(true);
    setError("");
    try {
      const created = await registerShop(signupToken.trim(), shopName.trim(), currencyCode);
      setApiKey(created.api_key);
      setKeyDraft("");
      setSignupToken("");
      setNotice(`Your ${created.name} shop is ready.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Shop setup could not be completed.");
    } finally {
      setWorking(false);
    }
  }

  function disconnect() {
    setApiKey("");
    setKeyDraft("");
    setShop(null);
    setDashboard(null);
    setError("");
    setNotice("");
  }

  async function submitAction(path: string, method: "POST", payload: object, success: string) {
    setWorking(true);
    setError("");
    try {
      await api(path, apiKey, { method, body: JSON.stringify(payload) });
      setModal(null);
      setNotice(success);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The action could not be completed.");
    } finally {
      setWorking(false);
    }
  }

  async function runAlerts() {
    setWorking(true);
    setError("");
    try {
      const result = await api<{ opened: AlertItem[]; resolved: AlertItem[] }>("/v1/alerts/run", apiKey, { method: "POST" });
      setNotice(`${result.opened.length} new alert${result.opened.length === 1 ? "" : "s"} · ${result.resolved.length} resolved`);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Alerts could not be refreshed.");
    } finally {
      setWorking(false);
    }
  }

  async function acknowledge(alertId: number) {
    try {
      await api(`/v1/alerts/${alertId}/acknowledge`, apiKey, { method: "POST" });
      setNotice("Alert marked as acknowledged.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Alert could not be acknowledged.");
    }
  }

  if (!shop) {
    return (
      <main className="connect-screen">
        <div className="connect-decoration" />
        <section className="connect-card">
          <div className="brand-lockup"><span className="brand-mark"><Activity size={21} /></span><span>retail<span className="brand-light">ops</span></span></div>
          <span className="eyebrow">{setupMode === "create" ? "GET YOUR TEST SHOP STARTED" : "YOUR OPERATIONS, IN FOCUS"}</span>
          <h1>{setupMode === "create" ? <>A clearer view<br />starts right here.</> : <>Welcome<br />back.</>}</h1>
          <p className="connect-copy">{setupMode === "create" ? "Set up a test shop and we’ll connect it for you. No copying API keys or opening API docs." : "Connect an existing shop to see sales, stock, customer balances, and alerts."}</p>
          {setupMode === "create" ? (
            <form onSubmit={createAndConnect} className="connect-form">
              <label htmlFor="shop-name">Shop name</label>
              <div className="input-with-icon"><ShoppingBag size={17} /><input id="shop-name" autoComplete="organization" placeholder="e.g. Greenfield Market" value={shopName} onChange={(e) => setShopName(e.target.value)} required maxLength={120} /></div>
              <div className="connect-form-row">
                <div className="connect-form-field"><label htmlFor="shop-currency">Currency</label><select id="shop-currency" value={currencyCode} onChange={(e) => setCurrencyCode(e.target.value)}><option value="NGN">NGN · Nigerian naira</option><option value="USD">USD · US dollar</option><option value="GBP">GBP · British pound</option><option value="EUR">EUR · Euro</option><option value="GHS">GHS · Ghanaian cedi</option><option value="KES">KES · Kenyan shilling</option><option value="ZAR">ZAR · South African rand</option></select></div>
              </div>
              <label htmlFor="signup-token">Test signup token</label>
              <div className="input-with-icon"><LockKeyhole size={17} /><input id="signup-token" type="password" autoComplete="off" placeholder="Enter your configured signup token" value={signupToken} onChange={(e) => setSignupToken(e.target.value)} required /></div>
              <button className="button button-primary button-wide" disabled={working}>{working ? <RefreshCw className="spin" size={17} /> : null}Create shop &amp; open dashboard <ArrowRight size={17} /></button>
            </form>
          ) : (
            <form onSubmit={connect} className="connect-form">
              <label htmlFor="api-key">Shop API key</label>
              <div className="input-with-icon"><LockKeyhole size={17} /><input id="api-key" type="password" autoComplete="off" placeholder="Paste your shop key" value={keyDraft} onChange={(e) => setKeyDraft(e.target.value)} required /></div>
              <button className="button button-primary button-wide" disabled={loading}>{loading ? <RefreshCw className="spin" size={17} /> : null}Connect shop <ArrowRight size={17} /></button>
            </form>
          )}
          {error && <p className="connect-error" role="alert">{error}</p>}
          <button className="setup-switch" onClick={() => { setSetupMode(setupMode === "create" ? "existing" : "create"); setError(""); }}>
            {setupMode === "create" ? "Already have a shop? Connect with its API key" : "Need a test shop? Create one here"}
          </button>
          <p className="privacy-note"><LockKeyhole size={13} /> Credentials stay in this tab only. Use test credentials for this preview.</p>
        </section>
        <aside className="connect-aside">
          <div className="aside-orbit orbit-one" /><div className="aside-orbit orbit-two" />
          <div className="preview-card">
            <div className="preview-heading"><span className="live-dot" /> LIVE STORE SNAPSHOT <span className="preview-menu">•••</span></div>
            <div className="preview-label">Revenue · last 30 days</div>
            <div className="preview-total">$24,860<span className="preview-change"><ArrowUpRight size={15} /> 12.8%</span></div>
            <MiniChart values={[30, 42, 34, 55, 45, 70, 61, 85, 72, 94]} />
            <div className="preview-footer"><span>Inventory health</span><strong>Looking good <span className="live-dot" /></strong></div>
          </div>
          <div className="floating-note"><span className="note-icon"><Bell size={16} /></span><span><b>Stay ahead</b><small>Know what needs attention</small></span><span className="note-count">3</span></div>
          <span className="aside-caption">A little more clarity, every day.</span>
        </aside>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "sidebar-open" : ""}`}>
        <div className="brand-lockup"><span className="brand-mark"><Activity size={20} /></span><span>retail<span className="brand-light">ops</span></span></div>
        <div className="side-label">WORKSPACE</div>
        <div className="workspace-switch"><span className="shop-avatar">{shop.name.slice(0, 1).toUpperCase()}</span><span className="workspace-name"><b>{shop.name}</b><small>{currency} · Retail workspace</small></span><ChevronDown size={15} /></div>
        <div className="side-label nav-label">MENU</div>
        <nav className="side-nav" aria-label="Main navigation">
          {navigation.map(({ name, icon: Icon }) => (
            <button key={name} className={`nav-item ${page === name ? "active" : ""}`} onClick={() => navigate(name)}>
              <Icon size={18} strokeWidth={1.8} /><span>{name}</span>
              {name === "Alerts" && alerts.length > 0 && <span className="nav-badge">{alerts.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="side-tip"><Sparkles size={16} /><b>Your daily pulse</b><p>Small, timely insights help you make confident decisions.</p></div>
          <button className="profile-button" onClick={disconnect}><span className="profile-avatar">{shop.name.slice(0, 1).toUpperCase()}</span><span><b>{shop.name}</b><small>Shop owner</small></span><LogOut size={16} /></button>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button className="mobile-menu-button" onClick={() => setMobileMenu((v) => !v)} aria-label="Toggle menu"><Menu size={20} /></button>
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><b>{page}</b></div>
          <div className="topbar-actions"><span className="today-label">{new Date().toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}</span><button className="icon-button" aria-label="Refresh data" onClick={() => void refresh()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={17} /></button><button className="top-avatar" title="Disconnect shop" onClick={disconnect}>{shop.name.slice(0, 1).toUpperCase()}</button></div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><span className="eyebrow">{page === "Overview" ? "YOUR BUSINESS AT A GLANCE" : "RETAIL OPERATIONS"}</span><h1>{page === "Overview" ? "Good to see you." : page}</h1><p>{page === "Overview" ? "Here’s what’s happening with your shop today." : pageSubtitle(page)}</p></div>
            <div className="heading-actions">
              {page === "Inventory" && <button className="button button-primary" onClick={() => setModal("product")}><Plus size={17} /> Add product</button>}
              {page === "Sales" && <button className="button button-primary" onClick={() => setModal("sale")}><Plus size={17} /> Record sale</button>}
              {page === "Customer debts" && <button className="button button-secondary" onClick={() => setModal("customer")}><Users size={17} /> Add customer</button>}
              {page === "Alerts" && <button className="button button-primary" disabled={working} onClick={() => void runAlerts()}><RefreshCw className={working ? "spin" : ""} size={16} /> Check for alerts</button>}
              {page === "Overview" && <button className="button button-primary" onClick={() => setModal("sale")}><Plus size={17} /> New sale</button>}
            </div>
          </div>

          {error && <div className="error-banner" role="alert"><CircleAlert size={18} /><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error"><X size={16} /></button></div>}
          {loading && !dashboard && <div className="loading-panel"><RefreshCw className="spin" size={22} /> Loading your store data…</div>}
          {dashboard && page === "Overview" && <Overview dashboard={dashboard} inventory={inventory} sales={sales} alerts={alerts} analytics={analytics} currency={currency} setPage={setPage} setModal={setModal} />}
          {dashboard && page === "Analytics" && <AnalyticsPage data={analytics} currency={currency} start={analyticsStart} end={analyticsEnd} loading={analyticsLoading} setStart={setAnalyticsStart} setEnd={setAnalyticsEnd} onApply={() => void loadDetailedAnalytics(analyticsStart, analyticsEnd)} />}
          {dashboard && page === "Inventory" && inventorySummary && <InventoryPage products={filteredProducts} inventory={inventory} summary={inventorySummary} currency={currency} query={query} setQuery={setQuery} onRestock={(product) => { setSelectedProduct(product); setModal("restock"); }} />}
          {dashboard && page === "Sales" && <SalesPage sales={filteredSales} currency={currency} query={query} setQuery={setQuery} />}
          {dashboard && page === "Customer debts" && <DebtsPage debts={filteredDebts} currency={currency} query={query} setQuery={setQuery} onPay={(debt) => { setSelectedDebt(debt); setModal("payment"); }} totals={dashboard.debts} />}
          {dashboard && page === "Alerts" && <AlertsPage alerts={alerts} onAcknowledge={acknowledge} />}

          <footer className="page-footer"><span><span className="live-dot" /> Connected to {shop.name}</span><span>Retail operations · First look</span></footer>
        </div>
      </main>

      {modal && <ActionModal modal={modal} products={products} customers={customers} product={selectedProduct} debt={selectedDebt} currency={currency} working={working} onClose={() => setModal(null)} onSubmit={submitAction} />}
      {notice && <div className="toast" role="status"><span className="toast-check"><Check size={15} /></span>{notice}<button onClick={() => setNotice("")} aria-label="Dismiss"><X size={15} /></button></div>}
    </div>
  );
}

function pageSubtitle(page: Page) {
  return ({
    Overview: "A quick pulse on your store.",
    Inventory: "Keep an eye on what’s on your shelves.",
    Sales: "Every transaction, all in one place.",
    Analytics: "Understand sales trends, product performance, and customer demand.",
    "Customer debts": "Track balances and follow up with confidence.",
    Alerts: "A focused list of things that may need your attention.",
  })[page];
}

function Overview({ dashboard, inventory, sales, alerts, analytics, currency, setPage, setModal }: {
  dashboard: Dashboard; inventory: InventoryRow[]; sales: Sale[]; alerts: AlertItem[]; analytics: SalesAnalytics;
  currency: string; setPage: (page: Page) => void; setModal: (modal: Modal) => void;
}) {
  const days = analytics.periods.slice(-14);
  const chartValues = days.map((item) => item.revenue);
  const maxRevenue = Math.max(...chartValues, 1);
  const debtRows = dashboard.debts.debts.filter((debt) => debt.amount_due > 0).slice(0, 4);
  const stockRows = inventory.filter((item) => item.below_reorder_level).slice(0, 4);
  return <>
    <div className="stats-grid">
      <MetricCard title="Sales revenue" value={fmtMoney(dashboard.sales.revenue, currency)} description="Last 30 days" icon={Wallet} color="green" foot={<><ArrowUpRight size={14} /> Sales recorded</>} />
      <MetricCard title="Gross profit" value={fmtMoney(dashboard.sales.profit, currency)} description={dashboard.sales.margin_pct == null ? "No margin data yet" : `${fmtNumber(dashboard.sales.margin_pct)}% margin`} icon={ChartNoAxesCombined} color="blue" foot={<><Activity size={14} /> On this period</>} />
      <MetricCard title="Inventory items" value={fmtNumber(dashboard.inventory.product_count)} description={`${dashboard.inventory.low_stock_count} need a restock`} icon={Boxes} color="amber" foot={<><PackagePlus size={14} /> Stock overview</>} />
      <MetricCard title="Customer balances" value={fmtMoney(dashboard.debts.total_outstanding, currency)} description={`${dashboard.debts.overdue_count} overdue`} icon={CreditCard} color="violet" foot={<><CircleAlert size={14} /> Receivables</>} />
    </div>

    <div className="overview-grid">
      <section className="panel sales-panel">
        <div className="panel-heading"><div><span className="panel-kicker">SALES PERFORMANCE</span><h2>Revenue over time</h2></div><span className="period-pill"><span className="live-dot" /> Last 14 days <ChevronDown size={14} /></span></div>
        <div className="chart-summary"><strong>{fmtMoney(dashboard.sales.revenue, currency)}</strong><span>in the last 30 days</span></div>
        <div className="chart-wrap">
          <div className="chart-y-labels"><span>{fmtMoney(maxRevenue, currency)}</span><span>{fmtMoney(maxRevenue * .66, currency)}</span><span>{fmtMoney(maxRevenue * .33, currency)}</span><span>{fmtMoney(0, currency)}</span></div>
          <div className="chart-main">
            <svg className="line-chart" viewBox="0 0 700 190" preserveAspectRatio="none" role="img" aria-label="Daily sales revenue chart">
              <defs><linearGradient id="chartFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#3e9b79" stopOpacity=".22" /><stop offset="100%" stopColor="#3e9b79" stopOpacity="0" /></linearGradient></defs>
              {[25, 72, 119, 166].map((y) => <line key={y} x1="0" y1={y} x2="700" y2={y} stroke="#edf0ed" strokeDasharray="4 6" />)}
              {chartValues.length > 1 && <><path d={chartPath(chartValues, maxRevenue, 700, 170) + " L700 190 L0 190 Z"} fill="url(#chartFill)" /><path d={chartPath(chartValues, maxRevenue, 700, 170)} fill="none" stroke="#358b6d" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></>}
              {chartValues.length === 1 && <circle cx="350" cy={184 - (chartValues[0] / maxRevenue) * 160} r="5" fill="#358b6d" />}
            </svg>
            <div className="chart-x-labels"><span>{days[0]?.period ? new Date(days[0].period).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—"}</span><span>7 days ago</span><span>Today</span></div>
          </div>
        </div>
      </section>
      <section className="panel attention-panel">
        <div className="panel-heading"><div><span className="panel-kicker">NEEDS YOUR ATTENTION</span><h2>Store pulse</h2></div><span className={`attention-count ${alerts.length ? "has-alerts" : ""}`}>{alerts.length}</span></div>
        {alerts.length === 0 && stockRows.length === 0 ? <EmptyState icon={Check} title="All clear for now" text="No open alerts or low-stock items." /> :
          <div className="pulse-list">
            {alerts.slice(0, 3).map((alert) => <button className="pulse-item" key={alert.id} onClick={() => setPage("Alerts")}><span className={`pulse-icon ${alert.urgency === "now" ? "pulse-red" : "pulse-amber"}`}><CircleAlert size={17} /></span><span className="pulse-copy"><b>{humanize(alert.kind)}</b><small>{alert.message}</small></span><ArrowRight size={15} /></button>)}
            {stockRows.slice(0, Math.max(0, 3 - Math.min(alerts.length, 3))).map((item) => <button className="pulse-item" key={item.product_id} onClick={() => setPage("Inventory")}><span className="pulse-icon pulse-amber"><Boxes size={17} /></span><span className="pulse-copy"><b>{item.name} is running low</b><small>{fmtNumber(item.stock)} left · reorder at {fmtNumber(item.reorder_level)}</small></span><ArrowRight size={15} /></button>)}
          </div>}
        <button className="text-link" onClick={() => setPage("Alerts")}>View all alerts <ArrowRight size={15} /></button>
      </section>
    </div>

    <div className="lower-grid">
      <section className="panel table-panel">
        <div className="panel-heading"><div><span className="panel-kicker">LATEST ACTIVITY</span><h2>Recent sales</h2></div><button className="text-link" onClick={() => setPage("Sales")}>View all <ArrowRight size={15} /></button></div>
        <SalesTable sales={sales.slice(0, 5)} currency={currency} />
      </section>
      <section className="panel debt-panel">
        <div className="panel-heading"><div><span className="panel-kicker">CUSTOMER CREDIT</span><h2>Due soon</h2></div><button className="text-link" onClick={() => setPage("Customer debts")}>See debts <ArrowRight size={15} /></button></div>
        {debtRows.length ? <div className="mini-debt-list">{debtRows.map((debt) => <div className="mini-debt" key={debt.debt_id}><span className="customer-avatar">{(debt.customer_name || "?").slice(0, 1).toUpperCase()}</span><span className="mini-debt-name"><b>{debt.customer_name || debt.customer_phone}</b><small>{debt.product} · due {fmtDate(debt.due_date)}</small></span><strong>{fmtMoney(debt.amount_due, currency)}</strong></div>)}</div> : <EmptyState icon={CreditCard} title="No open balances" text="Customer credit will show here." />}
      </section>
    </div>
    <div className="quick-actions"><div><span className="panel-kicker">QUICK ACTIONS</span><h2>Keep things moving</h2></div><button className="quick-action" onClick={() => setModal("sale")}><span className="quick-icon q-green"><ShoppingCart size={18} /></span><span><b>Record a sale</b><small>Log a new transaction</small></span><ArrowRight size={16} /></button><button className="quick-action" onClick={() => setModal("product")}><span className="quick-icon q-blue"><PackagePlus size={18} /></span><span><b>Add inventory</b><small>Create a new product</small></span><ArrowRight size={16} /></button><button className="quick-action" onClick={() => setModal("customer")}><span className="quick-icon q-violet"><Users size={18} /></span><span><b>Add a customer</b><small>Save customer details</small></span><ArrowRight size={16} /></button></div>
  </>;
}

function MetricCard({ title, value, description, icon: Icon, color, foot }: { title: string; value: string; description: string; icon: typeof Wallet; color: string; foot: ReactNode }) {
  return <section className="metric-card"><div className="metric-top"><span>{title}</span><span className={`metric-icon ${color}`}><Icon size={18} /></span></div><strong className="metric-value">{value}</strong><span className="metric-description">{description}</span><div className={`metric-foot ${color}`}>{foot}</div></section>;
}

function SalesTable({ sales, currency }: { sales: Sale[]; currency: string }) {
  if (!sales.length) return <EmptyState icon={ShoppingBag} title="No sales just yet" text="Your latest transactions will appear here." />;
  return <div className="table-scroll"><table><thead><tr><th>PRODUCT</th><th>CUSTOMER</th><th>DATE</th><th>AMOUNT</th><th>STATUS</th></tr></thead><tbody>
    {sales.map((sale) => <tr key={sale.sale_id}><td><span className="product-cell"><span className="table-product-icon"><ShoppingBag size={15} /></span><span><b>{sale.product}</b><small>{fmtNumber(sale.quantity)} sold</small></span></span></td><td>{sale.customer_name || <span className="muted">Walk-in</span>}</td><td>{fmtDate(sale.sold_at)}</td><td><b>{fmtMoney(sale.revenue, currency)}</b></td><td><span className="status-pill status-paid"><span /> Completed</span></td></tr>)}
  </tbody></table></div>;
}

function AnalyticsPage({ data, currency, start, end, loading, setStart, setEnd, onApply }: {
  data: SalesAnalytics; currency: string; start: string; end: string; loading: boolean;
  setStart: (value: string) => void; setEnd: (value: string) => void; onApply: () => void;
}) {
  const summary = data.summary;
  const periods = data.periods ?? [];
  const chartValues = periods.map((period) => period.revenue);
  const maxRevenue = Math.max(...chartValues, 1);
  const products = data.products ?? [];
  const weekdays = data.weekdays ?? [];
  const maxWeekday = Math.max(...weekdays.map((day) => day.avg_revenue), 1);

  return <>
    <section className="analytics-filter panel">
      <div><span className="panel-kicker">REPORTING PERIOD</span><p>Choose up to 366 days of sales activity.</p></div>
      <div className="analytics-filter-controls">
        <label>From<input type="date" value={start} max={end || dateToday()} onChange={(event) => setStart(event.target.value)} /></label>
        <label>To<input type="date" value={end} min={start} max={dateToday()} onChange={(event) => setEnd(event.target.value)} /></label>
        <button className="button button-primary" disabled={loading} onClick={onApply}>{loading ? <RefreshCw size={15} className="spin" /> : <ChartNoAxesCombined size={15} />}Update report</button>
      </div>
    </section>

    {loading && !summary ? <div className="loading-panel"><RefreshCw className="spin" size={22} /> Loading analytics…</div> : <>
      <div className="stats-grid analytics-stats">
        <MetricCard title="Revenue" value={fmtMoney(summary?.revenue, currency)} description={`${summary?.transactions ?? 0} completed sales`} icon={Wallet} color="green" foot={<><ArrowUpRight size={14} /> For selected period</>} />
        <MetricCard title="Gross profit" value={fmtMoney(summary?.profit, currency)} description={summary?.margin_pct == null ? "No margin data yet" : `${fmtNumber(summary.margin_pct)}% margin`} icon={ChartNoAxesCombined} color="blue" foot={<><Activity size={14} /> Revenue less cost</>} />
        <MetricCard title="Units sold" value={fmtNumber(summary?.units ?? 0)} description="Net units after returns" icon={ShoppingBag} color="amber" foot={<><Boxes size={14} /> Across all products</>} />
        <MetricCard title="Average sale" value={fmtMoney(summary?.avg_sale, currency)} description={fmtMoney(summary?.returns_value, currency) + " returned"} icon={CreditCard} color="violet" foot={<><CircleAlert size={14} /> Sales quality</>} />
      </div>

      <div className="overview-grid analytics-main-grid">
        <section className="panel analytics-chart-panel">
          <div className="panel-heading"><div><span className="panel-kicker">DAILY PERFORMANCE</span><h2>Revenue and profit</h2></div><span className="period-pill">{data.start ? fmtDate(data.start) : start} – {data.end ? fmtDate(data.end) : end}</span></div>
          <div className="analytics-legend"><span><i className="legend-revenue" /> Revenue</span><span><i className="legend-profit" /> Profit</span></div>
          {periods.length ? <div className="analytics-chart-wrap">
            <div className="chart-y-labels"><span>{fmtMoney(maxRevenue, currency)}</span><span>{fmtMoney(maxRevenue * .66, currency)}</span><span>{fmtMoney(maxRevenue * .33, currency)}</span><span>{fmtMoney(0, currency)}</span></div>
            <div className="chart-main">
              <svg className="line-chart" viewBox="0 0 700 190" preserveAspectRatio="none" role="img" aria-label="Revenue and profit by day">
                {[25, 72, 119, 166].map((y) => <line key={y} x1="0" y1={y} x2="700" y2={y} stroke="#edf0ed" strokeDasharray="4 6" />)}
                {chartValues.length > 1 && <><path d={chartPath(chartValues, maxRevenue, 700, 170)} fill="none" stroke="#358b6d" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /><path d={chartPath(periods.map((point) => Math.max(point.profit, 0)), maxRevenue, 700, 170)} fill="none" stroke="#7ea5c3" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></>}
                {chartValues.length === 1 && <circle cx="350" cy={184 - (chartValues[0] / maxRevenue) * 160} r="5" fill="#358b6d" />}
              </svg>
              <div className="chart-x-labels"><span>{periods[0]?.period ? fmtDate(periods[0].period) : "—"}</span><span>{periods[Math.floor(periods.length / 2)]?.period ? fmtDate(periods[Math.floor(periods.length / 2)].period) : ""}</span><span>{periods.at(-1)?.period ? fmtDate(periods.at(-1)!.period) : "—"}</span></div>
            </div>
          </div> : <EmptyState icon={ChartNoAxesCombined} title="No sales in this period" text="Sales trends will appear here when transactions are recorded." />}
        </section>
        <section className="panel weekday-panel">
          <div className="panel-heading"><div><span className="panel-kicker">CUSTOMER RHYTHM</span><h2>Average by weekday</h2></div></div>
          {weekdays.length ? <div className="weekday-list">{weekdays.map((day) => <div className="weekday-row" key={day.weekday}><span>{day.weekday.slice(0, 3)}</span><div className="weekday-track"><i style={{ width: `${Math.max((day.avg_revenue / maxWeekday) * 100, day.avg_revenue ? 3 : 0)}%` }} /></div><b>{fmtMoney(day.avg_revenue, currency)}</b></div>)}</div> : <EmptyState icon={Activity} title="Not enough activity yet" text="Weekday patterns appear as sales accumulate." />}
        </section>
      </div>

      <section className="panel full-panel analytics-products">
        <div className="panel-heading"><div><span className="panel-kicker">PRODUCT PERFORMANCE</span><h2>Top products <span className="inline-count">{products.length}</span></h2></div></div>
        {products.length ? <div className="table-scroll"><table><thead><tr><th>PRODUCT</th><th>UNITS SOLD</th><th>REVENUE</th><th>GROSS PROFIT</th><th>REVENUE SHARE</th></tr></thead><tbody>
          {products.slice(0, 10).map((product) => <tr key={product.product}><td><span className="product-cell"><span className="table-product-icon table-product-green"><ShoppingBag size={15} /></span><b>{product.product}</b></span></td><td>{fmtNumber(product.units)}</td><td><b>{fmtMoney(product.revenue, currency)}</b></td><td>{fmtMoney(product.profit, currency)}</td><td><span className="share-cell"><span className="share-track"><i style={{ width: `${Math.max(0, Math.min(product.share_pct, 100))}%` }} /></span>{fmtNumber(product.share_pct)}%</span></td></tr>)}
        </tbody></table></div> : <EmptyState icon={Boxes} title="No product sales to rank" text="Once products have sales, their performance will show here." />}
      </section>
    </>}
  </>;
}

function InventoryPage({ products, inventory, summary, currency, query, setQuery, onRestock }: { products: Product[]; inventory: InventoryRow[]; summary: InventorySummary; currency: string; query: string; setQuery: (v: string) => void; onRestock: (p: Product) => void }) {
  const stockById = new Map(inventory.map((line) => [line.product_id, line]));
  return <>
    <div className="stats-grid inventory-stats">
      <MetricCard title="Products tracked" value={fmtNumber(summary.product_count)} description={`${summary.low_stock_count} below reorder level`} icon={Boxes} color="blue" foot={<><PackagePlus size={14} /> Active catalog</>} />
      <MetricCard title="Products in stock" value={fmtNumber(summary.products_in_stock)} description={`${summary.product_count - summary.products_in_stock} currently out of stock`} icon={ShoppingBag} color="green" foot={<><Check size={14} /> Available items</>} />
      <MetricCard title="Stock at cost" value={fmtMoney(summary.stock_cost_value, currency)} description="Current inventory cost value" icon={Wallet} color="amber" foot={<><Boxes size={14} /> On-hand valuation</>} />
      <MetricCard title="Potential sales value" value={fmtMoney(summary.potential_sales_value, currency)} description="If current stock sells at listed prices" icon={ChartNoAxesCombined} color="blue" foot={<><ArrowUpRight size={14} /> Estimated revenue</>} />
      <MetricCard title="Potential gross profit" value={fmtMoney(summary.potential_gross_profit, currency)} description="Potential sales value less stock cost" icon={ChartNoAxesCombined} color="violet" foot={<><Activity size={14} /> Before other expenses</>} />
    </div>
    <section className="panel full-panel inventory-table-panel"><div className="panel-heading"><div><span className="panel-kicker">PRODUCT CATALOG</span><h2>Detailed stock <span className="inline-count">{summary.product_count}</span></h2></div><SearchBox value={query} onChange={setQuery} placeholder="Search products" /></div>
    {!products.length ? <EmptyState icon={Boxes} title="Your inventory starts here" text="Add a product to begin tracking your stock." /> : <div className="table-scroll"><table><thead><tr><th>PRODUCT</th><th>ON HAND</th><th>UNIT COST</th><th>SELLING PRICE</th><th>STOCK AT COST</th><th>POTENTIAL SALES</th><th>POTENTIAL PROFIT</th><th>STATUS</th><th /></tr></thead><tbody>
      {products.map((product) => { const line = stockById.get(product.id); const low = line?.below_reorder_level ?? product.stock <= product.reorder_level; return <tr key={product.id}><td><span className="product-cell"><span className="table-product-icon table-product-green"><Boxes size={15} /></span><span><b>{product.name}</b><small>SKU #{String(product.id).padStart(4, "0")} · per {product.unit_name}</small></span></span></td><td><b>{fmtNumber(line?.stock ?? product.stock)}</b> <span className="muted">{product.unit_name}</span></td><td>{fmtMoney(line?.unit_cost ?? product.unit_cost, currency)}</td><td>{fmtMoney(line?.selling_price ?? product.selling_price, currency)}</td><td>{fmtMoney(line?.stock_cost_value ?? 0, currency)}</td><td>{fmtMoney(line?.potential_sales_value ?? 0, currency)}</td><td><b>{fmtMoney(line?.potential_gross_profit ?? 0, currency)}</b></td><td><span className={`status-pill ${low ? "status-low" : "status-instock"}`}><span />{low ? "Low stock" : "In stock"}</span></td><td><button className="small-button" onClick={() => onRestock(product)}><PackagePlus size={15} /> Restock</button></td></tr>; })}
    </tbody></table></div>}
    <p className="inventory-disclaimer">Values are estimates using each product’s current unit cost and selling price. Potential gross profit assumes all stock sells at listed prices and excludes operating expenses, discounts, and taxes.</p>
  </section>
  </>;
}

function SalesPage({ sales, currency, query, setQuery }: { sales: Sale[]; currency: string; query: string; setQuery: (v: string) => void }) {
  return <section className="panel full-panel"><div className="panel-heading"><div><span className="panel-kicker">TRANSACTION HISTORY</span><h2>Recent sales <span className="inline-count">{sales.length}</span></h2></div><SearchBox value={query} onChange={setQuery} placeholder="Search sales" /></div><SalesTable sales={sales} currency={currency} /></section>;
}

function DebtsPage({ debts, currency, query, setQuery, onPay, totals }: { debts: Debt[]; currency: string; query: string; setQuery: (v: string) => void; onPay: (d: Debt) => void; totals: Dashboard["debts"] }) {
  return <>
    <div className="debt-summary-row"><div className="debt-summary-card debt-summary-main"><span>Total outstanding</span><strong>{fmtMoney(totals.total_outstanding, currency)}</strong><small>Across {debts.length} open debt records</small></div><div className="debt-summary-card"><span>Overdue</span><strong className="text-red">{fmtMoney(totals.overdue_amount, currency)}</strong><small>{totals.overdue_count} customer balances</small></div><div className="debt-summary-card"><span>Due today</span><strong>{totals.due_today_count}</strong><small>Needs follow-up</small></div><div className="debt-summary-card"><span>Due soon</span><strong>{totals.due_soon_count}</strong><small>Within the next 7 days</small></div></div>
    <section className="panel full-panel"><div className="panel-heading"><div><span className="panel-kicker">RECEIVABLES</span><h2>Open balances <span className="inline-count">{debts.length}</span></h2></div><SearchBox value={query} onChange={setQuery} placeholder="Search people or items" /></div>
      {!debts.length ? <EmptyState icon={CreditCard} title="No customer balances" text="Credit sales and due dates will be organized here." /> : <div className="table-scroll"><table><thead><tr><th>CUSTOMER</th><th>ITEM</th><th>DUE DATE</th><th>ORIGINAL</th><th>REMAINING</th><th>STATUS</th><th /></tr></thead><tbody>
        {debts.map((debt) => <tr key={debt.debt_id}><td><span className="product-cell"><span className="customer-avatar">{(debt.customer_name || "?").slice(0, 1).toUpperCase()}</span><span><b>{debt.customer_name || "Unnamed customer"}</b><small>{debt.customer_phone}</small></span></span></td><td>{debt.product}<small className="table-subline">{fmtNumber(debt.quantity)} {debt.unit}</small></td><td>{fmtDate(debt.due_date)}</td><td>{fmtMoney(debt.original_amount, currency)}</td><td><b>{fmtMoney(debt.amount_due, currency)}</b></td><td><span className={`status-pill ${debt.status === "overdue" ? "status-low" : "status-instock"}`}><span />{humanize(debt.status)}</span></td><td><button className="small-button" onClick={() => onPay(debt)}><Wallet size={15} /> Payment</button></td></tr>)}
      </tbody></table></div>}
    </section>
  </>;
}

function AlertsPage({ alerts, onAcknowledge }: { alerts: AlertItem[]; onAcknowledge: (id: number) => void }) {
  if (!alerts.length) return <section className="panel full-panel"><EmptyState icon={Check} title="You’re all caught up" text="Run a check to look for new operational alerts." /></section>;
  return <section className="panel full-panel"><div className="panel-heading"><div><span className="panel-kicker">OPEN ALERTS</span><h2>Things to look at <span className="inline-count">{alerts.length}</span></h2></div><span className="period-pill"><span className="live-dot" /> Active</span></div><div className="alert-list">{alerts.map((alert) => <article className="alert-card" key={alert.id}><span className={`alert-symbol ${alert.urgency === "now" ? "alert-urgent" : "alert-weekly"}`}><CircleAlert size={19} /></span><div className="alert-content"><div className="alert-title-row"><h3>{humanize(alert.kind)}</h3><span className={`status-pill ${alert.urgency === "now" ? "status-low" : "status-instock"}`}><span />{humanize(alert.urgency)}</span></div><p>{alert.message}</p>{alert.action && <div className="alert-action"><ArrowRight size={14} />{alert.action}</div>}<small>Opened {fmtDate(alert.created_at)}</small></div><button className="small-button" onClick={() => onAcknowledge(alert.id)}><Check size={15} /> Acknowledge</button></article>)}</div></section>;
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return <label className="search-box"><Search size={16} /><input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} /><span>/</span></label>;
}

function EmptyState({ icon: Icon, title, text }: { icon: typeof Wallet; title: string; text: string }) {
  return <div className="empty-state"><span className="empty-icon"><Icon size={20} /></span><b>{title}</b><p>{text}</p></div>;
}

function ActionModal({ modal, products, customers, product, debt, currency, working, onClose, onSubmit }: {
  modal: Exclude<Modal, null>; products: Product[]; customers: Customer[]; product: Product | null; debt: Debt | null; currency: string;
  working: boolean; onClose: () => void; onSubmit: (path: string, method: "POST", payload: object, success: string) => Promise<void>;
}) {
  const title = ({ product: "Add product", sale: "Record a sale", customer: "Add customer", restock: "Restock product", payment: "Record a payment" })[modal];
  const description = ({ product: "Add an item to your catalog and set its starting stock.", sale: "Record a cash or credit transaction.", customer: "Save a customer's name and contact details.", restock: "Add units to the available inventory.", payment: "Apply a payment to this customer balance." })[modal];
  const [selectedProductId, setSelectedProductId] = useState(product ? String(product.id) : "");
  const [isCredit, setIsCredit] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    if (modal === "product") {
      void onSubmit("/v1/products", "POST", { name: values.name, unit_cost: values.unit_cost, selling_price: values.selling_price, opening_stock: values.opening_stock || "0", reorder_level: values.reorder_level || "0", unit_name: values.unit_name || "unit" }, "Product added to your inventory.");
    } else if (modal === "sale") {
      const payload: Record<string, string | number> = { product_id: Number(values.product_id), quantity: String(values.quantity) };
      if (values.customer_id) payload.customer_id = Number(values.customer_id);
      if (isCredit) payload.credit_due_date = String(values.credit_due_date);
      if (values.unit_price) payload.unit_price = String(values.unit_price);
      void onSubmit("/v1/sales", "POST", payload, isCredit ? "Credit sale recorded." : "Sale recorded.");
    } else if (modal === "customer") {
      void onSubmit("/v1/customers", "POST", { name: values.name || null, phone: values.phone }, "Customer saved.");
    } else if (modal === "restock" && product) {
      void onSubmit(`/v1/products/${product.id}/restocks`, "POST", { quantity: values.quantity, unit_cost: values.unit_cost, supplier: values.supplier || null }, "Inventory restocked.");
    } else if (modal === "payment" && debt) {
      void onSubmit(`/v1/debts/${debt.debt_id}/payments`, "POST", { amount: values.amount, note: values.note || null }, "Payment recorded.");
    }
  }

  return <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}><section className="action-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="modal-heading"><span className="modal-icon">{modal === "sale" ? <ShoppingCart size={19} /> : modal === "customer" ? <Users size={19} /> : modal === "payment" ? <Wallet size={19} /> : <PackagePlus size={19} />}</span><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button><span className="eyebrow">SHOP MANAGEMENT</span><h2 id="modal-title">{title}</h2><p>{description}</p></div>
    <form className="modal-form" onSubmit={submit}>
      {modal === "product" && <><Field label="Product name"><input name="name" placeholder="e.g. Coffee beans" required maxLength={120} /></Field><div className="form-two"><Field label={`Unit cost (${currency})`}><input name="unit_cost" type="number" min="0" step="0.01" required placeholder="0.00" /></Field><Field label={`Selling price (${currency})`}><input name="selling_price" type="number" min="0" step="0.01" required placeholder="0.00" /></Field></div><div className="form-two"><Field label="Opening stock"><input name="opening_stock" type="number" min="0" step="0.001" defaultValue="0" /></Field><Field label="Reorder level"><input name="reorder_level" type="number" min="0" step="0.001" defaultValue="0" /></Field></div><Field label="Unit"><input name="unit_name" placeholder="piece, kg, litre…" defaultValue="unit" maxLength={30} required /></Field></>}
      {modal === "sale" && <><Field label="Product"><select name="product_id" value={selectedProductId} onChange={(e) => setSelectedProductId(e.target.value)} required><option value="" disabled>Select a product</option>{products.map((p) => <option value={p.id} key={p.id}>{p.name} · {fmtNumber(p.stock)} {p.unit_name} · {fmtMoney(p.selling_price, currency)}</option>)}</select></Field><div className="form-two"><Field label="Quantity"><input name="quantity" type="number" min="0.001" step="0.001" required placeholder="1" /></Field><Field label={`Unit price (${currency})`}><input name="unit_price" type="number" min="0" step="0.01" placeholder="Default price" /></Field></div><Field label={isCredit ? "Customer (required)" : "Customer (optional)"}><select name="customer_id" defaultValue="" required={isCredit}><option value="">Walk-in customer</option>{customers.map((c) => <option value={c.id} key={c.id}>{c.name || c.phone}</option>)}</select></Field><label className="checkbox-row"><input type="checkbox" checked={isCredit} onChange={(e) => setIsCredit(e.target.checked)} /> Record as customer credit</label>{isCredit && <><Field label="Due date"><input name="credit_due_date" type="date" min={dateToday()} required /></Field><p className="form-hint">A customer is required for a credit sale. Select one above.</p></>}</>}
      {modal === "customer" && <><Field label="Customer name"><input name="name" placeholder="Full name" maxLength={120} /></Field><Field label="Phone number"><input name="phone" type="tel" placeholder="Phone number" maxLength={30} required /></Field></>}
      {modal === "restock" && product && <><div className="selected-product"><span className="table-product-icon table-product-green"><Boxes size={16} /></span><span><b>{product.name}</b><small>Current stock · {fmtNumber(product.stock)} {product.unit_name}</small></span></div><Field label={`Quantity (${product.unit_name})`}><input name="quantity" type="number" min="0.001" step="0.001" required placeholder="Quantity received" /></Field><Field label={`Unit cost (${currency})`}><input name="unit_cost" type="number" min="0" step="0.01" required defaultValue={product.unit_cost} /></Field><Field label="Supplier (optional)"><input name="supplier" maxLength={120} placeholder="Supplier name" /></Field></>}
      {modal === "payment" && debt && <><div className="payment-context"><span className="customer-avatar">{(debt.customer_name || "?").slice(0, 1).toUpperCase()}</span><span><b>{debt.customer_name || debt.customer_phone}</b><small>{debt.product} · remaining {fmtMoney(debt.amount_due, currency)}</small></span></div><Field label={`Payment amount (${currency})`}><input name="amount" type="number" min="0.01" max={debt.amount_due} step="0.01" required placeholder="0.00" /></Field><Field label="Note (optional)"><input name="note" maxLength={200} placeholder="Payment method or reference" /></Field></>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={working}>{working && <RefreshCw size={15} className="spin" />}{title}</button></div>
    </form>
  </section></div>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="form-field"><span>{label}</span>{children}</label>;
}

function MiniChart({ values }: { values: number[] }) {
  return <svg className="mini-chart" viewBox="0 0 380 90" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="miniFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#88d5ad" stopOpacity=".3" /><stop offset="100%" stopColor="#88d5ad" stopOpacity="0" /></linearGradient></defs><path d={chartPath(values, Math.max(...values), 380, 70) + " L380 90 L0 90 Z"} fill="url(#miniFill)" /><path d={chartPath(values, Math.max(...values), 380, 70)} fill="none" stroke="#8bd4ac" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function chartPath(values: number[], maximum: number, width: number, height: number) {
  if (!values.length) return "";
  const gap = width / Math.max(values.length - 1, 1);
  return values.map((value, index) => `${index === 0 ? "M" : "L"}${index * gap},${height - (value / maximum) * height + 10}`).join(" ");
}

export default App;
