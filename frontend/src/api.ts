export type Shop = {
  id: number;
  name: string;
  currency_code: string;
};

export type Product = {
  id: number;
  name: string;
  unit_name: string;
  unit_cost: number;
  selling_price: number;
  reorder_level: number;
  lead_time_days: number;
  stock: number;
  active: boolean;
};

export type Customer = {
  id: number;
  name: string | null;
  phone: string;
};

export type Sale = {
  sale_id: number;
  product: string;
  customer_name: string | null;
  quantity: number;
  unit_price: number;
  revenue: number;
  profit: number;
  sold_at: string;
};

export type Debt = {
  debt_id: number;
  customer_id: number;
  customer_name: string | null;
  customer_phone: string;
  product: string;
  quantity: number;
  unit: string;
  due_date: string;
  original_amount: number;
  paid_amount: number;
  amount_due: number;
  status: string;
};

export type AlertItem = {
  id: number;
  kind: string;
  urgency: string;
  message: string;
  action: string | null;
  status: string;
  created_at: string;
};

export type Dashboard = {
  shop: Shop;
  sales: {
    revenue: number;
    profit: number;
    margin_pct: number | null;
    transactions: number;
    units: number;
  };
  inventory: { product_count: number; low_stock_count: number };
  debts: {
    total_outstanding: number;
    overdue_amount: number;
    overdue_count: number;
    due_today_count: number;
    due_soon_count: number;
    debts: Debt[];
  };
};

export type SalesAnalytics = {
  periods: { period: string; revenue: number; profit: number; change_pct: number | null }[];
  start?: string;
  end?: string;
  currency_code?: string;
  summary?: {
    revenue: number;
    profit: number;
    margin_pct: number | null;
    units: number;
    transactions: number;
    avg_sale: number;
    returns_value: number;
  };
  products?: {
    product: string;
    revenue: number;
    units: number;
    profit: number;
    margin_pct: number | null;
    share_pct: number;
    top_80: boolean;
  }[];
  weekdays?: { weekday: string; avg_revenue: number }[];
};

export async function api<T>(
  path: string,
  apiKey: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${apiKey}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new Error("Cannot reach the API. Check that the backend is running.");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export async function registerShop(
  signupToken: string,
  name: string,
  currencyCode: string,
): Promise<{ shop_id: number; name: string; currency_code: string; api_key: string }> {
  let response: Response;
  try {
    response = await fetch("/api/v1/shops", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Signup-Token": signupToken,
      },
      body: JSON.stringify({ name, currency_code: currencyCode }),
    });
  } catch {
    throw new Error("Cannot reach the API. Check that the backend is running.");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `Shop setup failed (${response.status}).`);
  }
  return response.json();
}
