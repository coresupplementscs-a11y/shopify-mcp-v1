// Meta tracker - Shopify custom pixel.
// Paste into Shopify admin > Settings > Customer events > Add custom pixel.
// Set ENDPOINT to your Railway tracker URL + /collect before saving.
const ENDPOINT = "https://YOUR-TRACKER.up.railway.app/collect";

const NINETY_DAYS = 90 * 24 * 3600;

function cookieDomain(hostname) {
  if (!hostname || hostname.endsWith(".myshopify.com")) return "";
  return "; domain=." + hostname.replace(/^www\./, "");
}

async function readCookie(name) {
  try { return (await browser.cookie.get(name)) || ""; } catch (e) { return ""; }
}

async function writeCookie(name, value, hostname) {
  try {
    await browser.cookie.set(
      `${name}=${value}; max-age=${NINETY_DAYS}; path=/${cookieDomain(hostname)}; SameSite=Lax`
    );
  } catch (e) { /* cookie writes can be blocked; the value is still sent this time */ }
}

// Meta's first-party browser id (_fbp) and click id (_fbc, built from ?fbclid=).
async function metaIds(location) {
  const hostname = (location && location.hostname) || "";
  let fbp = await readCookie("_fbp");
  if (!fbp) {
    fbp = `fb.1.${Date.now()}.${Math.floor(Math.random() * 1e10)}`;
    await writeCookie("_fbp", fbp, hostname);
  }
  let fbc = await readCookie("_fbc");
  let fbclid = "";
  try { fbclid = new URL(location.href).searchParams.get("fbclid") || ""; } catch (e) {}
  if (fbclid && !fbc.endsWith("." + fbclid)) {
    fbc = `fb.1.${Date.now()}.${fbclid}`;
    await writeCookie("_fbc", fbc, hostname);
  }
  return { fbp, fbc };
}

function money(m) { return m && m.amount != null ? Number(m.amount) : undefined; }

function item(variant, quantity) {
  if (!variant) return null;
  return {
    product_id: variant.product && variant.product.id,
    variant_id: variant.id,
    sku: variant.sku,
    price: money(variant.price),
    quantity: quantity || 1,
  };
}

function checkoutInfo(c) {
  if (!c) return undefined;
  const addr = c.billingAddress || c.shippingAddress || {};
  return {
    token: c.token,
    email: c.email,
    phone: c.phone || addr.phone,
    first_name: addr.firstName,
    last_name: addr.lastName,
    order_id: c.order && c.order.id,
  };
}

function customData(name, data) {
  switch (name) {
    case "product_viewed": {
      const v = data.productVariant;
      return { value: money(v && v.price), currency: v && v.price && v.price.currencyCode,
               content_name: v && v.product && v.product.title, items: [item(v, 1)] };
    }
    case "product_added_to_cart": {
      const line = data.cartLine;
      return { value: money(line && line.cost && line.cost.totalAmount),
               currency: line && line.cost && line.cost.totalAmount && line.cost.totalAmount.currencyCode,
               items: [item(line && line.merchandise, line && line.quantity)] };
    }
    case "checkout_started":
    case "payment_info_submitted": {
      const c = data.checkout || {};
      const items = (c.lineItems || []).map(l => item(l.variant, l.quantity));
      return { value: money(c.totalPrice), currency: c.currencyCode, items,
               num_items: items.reduce((n, i) => n + ((i && i.quantity) || 0), 0) };
    }
    case "search_submitted":
      return { search_string: data.searchResult && data.searchResult.query };
    default:
      return undefined;
  }
}

async function track(event) {
  try {
    const location = event.context && event.context.document && event.context.document.location;
    const { fbp, fbc } = await metaIds(location);
    const customer = (init.data && init.data.customer) || {};
    const data = event.data || {};
    // Shopify's clientId is a stable first-party visitor id; the _fbp cookie we
    // write ourselves can be evicted by Safari within days, so it must not be
    // the key that ties a browser to its order.
    const payload = {
      name: event.name,
      id: event.id,
      ts: Date.parse(event.timestamp) || Date.now(),
      url: location && location.href,
      cid: event.clientId || fbp,
      fbp,
      fbc,
      customer: { id: customer.id, email: customer.email, phone: customer.phone,
                  first_name: customer.firstName, last_name: customer.lastName },
      checkout: checkoutInfo(data.checkout),
      custom: customData(event.name, data),
    };
    await fetch(ENDPOINT, {
      method: "POST",
      mode: "no-cors",
      keepalive: true,
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify(payload),
    });
  } catch (e) { /* never break the storefront */ }
}

// One literal call per event: Shopify's pixel editor only recognises
// subscriptions written this way.
analytics.subscribe("page_viewed", track);
analytics.subscribe("product_viewed", track);
analytics.subscribe("search_submitted", track);
analytics.subscribe("product_added_to_cart", track);
analytics.subscribe("checkout_started", track);
analytics.subscribe("checkout_contact_info_submitted", track);
analytics.subscribe("checkout_address_info_submitted", track);
analytics.subscribe("checkout_shipping_info_submitted", track);
analytics.subscribe("payment_info_submitted", track);
analytics.subscribe("checkout_completed", track);
