<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { api } from '$lib/api';

  let session = null;
  let data = null;
  let loading = true;
  let working = false;
  let error = '';
  let notice = '';
  let loadedRoute = '';
  let loginForm = { username: '', password: '' };
  let query = '';
  let category = '';
  let stateFilter = '';
  let customerQuery = '';
  let customerResults = [];
  let customerForm = { first_name: '', last_name: '', email: '', phone: '' };
  let payment = { method: 'cash', tendered: '', reference: '', confirmed: false };
  let staffForm = { name: '', role: 'Stylist', active: true };
  let showCustomerCreate = false;
  let payloadOrder = null;

  $: route = $page.url.pathname.replace(/^\/app\/?/, '').replace(/\/$/, '') || 'dashboard';
  $: title = routeTitle(route);
  $: if (session?.authenticated && route !== loadedRoute) loadRoute(route);

  onMount(init);

  async function init() {
    try {
      session = await api.get('/session/');
      if (session.authenticated) await loadRoute(route);
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function routeTitle(value) {
    if (value.startsWith('sales/')) return value.includes('/checkout') ? 'Checkout' : value.includes('/receipt') ? 'Receipt' : 'Create a sale';
    return ({ dashboard: 'Receptionist desk', bills: 'Open bills', orders: 'Orders & Shopify sync',
      catalog: 'Services & catalog', staff: 'Staff directory', reports: 'Daily operations report',
      settings: 'Settings' })[value] || 'Karim POS';
  }

  async function loadRoute(value, force = false) {
    if (!force && loadedRoute === value && data) return;
    loadedRoute = value;
    loading = true;
    error = '';
    payloadOrder = null;
    try {
      const parts = value.split('/');
      if (value === 'dashboard') data = await api.get('/dashboard/');
      else if (value === 'bills') data = await api.get(`/bills/?q=${encodeURIComponent(query)}`);
      else if (value === 'catalog') data = await api.get(`/catalog/?q=${encodeURIComponent(query)}`);
      else if (value === 'staff') data = await api.get('/staff/');
      else if (value === 'orders') data = await api.get(`/orders/?state=${encodeURIComponent(stateFilter)}`);
      else if (value === 'reports') data = await api.get('/reports/');
      else if (value === 'settings') data = await api.get('/settings/');
      else if (parts[0] === 'sales' && parts[1]) {
        if (parts[2] === 'receipt') data = await api.get(`/bills/${parts[1]}/receipt/`);
        else {
          const [bill, catalogData, staffData] = await Promise.all([
            api.get(`/bills/${parts[1]}/`), api.get('/catalog/?active=1'), api.get('/staff/')
          ]);
          data = { bill, ...catalogData, ...staffData };
          if (parts[2] === 'checkout') payment.tendered = bill.total;
        }
      } else data = await api.get('/dashboard/');
    } catch (e) {
      if (e.status === 401) session = { authenticated: false, config: session?.config || {} };
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function act(task, success = '') {
    working = true; error = ''; notice = '';
    try {
      const result = await task();
      if (success) notice = success;
      return result;
    } catch (e) {
      error = e.message;
      return null;
    } finally { working = false; }
  }

  async function signIn(demo = false) {
    const result = await act(() => demo ? api.post('/session/demo/') : api.post('/session/login/', loginForm));
    if (result) {
      session = await api.get('/session/');
      loadedRoute = '';
      await goto('/app/');
    }
  }

  async function signOut() {
    await act(() => api.post('/session/logout/'));
    session = await api.get('/session/'); data = null; loadedRoute = '';
  }

  async function createBill() {
    const bill = await act(() => api.post('/bills/'));
    // Use a full navigation after creating a bill. Django serves every nested
    // Svelte route, so this also recovers cleanly from an older cached shell.
    if (bill) window.location.assign(`/app/sales/${bill.id}`);
  }

  async function saveBill() {
    const b = data.bill;
    const updated = await act(() => api.patch(`/bills/${b.id}/`, {
      first_name: b.firstName, last_name: b.lastName, email: b.email, phone: b.phone, notes: b.notes
    }), 'Sale details saved.');
    if (updated) data.bill = updated;
  }

  async function addProduct(productId) {
    if (await act(() => api.post(`/bills/${data.bill.id}/lines/`, { productId }))) await loadRoute(route, true);
  }

  async function saveLine(line) {
    if (await act(() => api.patch(`/lines/${line.id}/`, line), 'Line item saved.')) await loadRoute(route, true);
  }

  async function removeLine(line) {
    if (await act(() => api.remove(`/lines/${line.id}/`))) await loadRoute(route, true);
  }

  async function searchCustomers() {
    if (customerQuery.trim().length < 2) { error = 'Enter at least 2 characters to search.'; return; }
    const result = await act(() => api.get(`/customers/?q=${encodeURIComponent(customerQuery)}`));
    if (result) customerResults = result.customers;
  }

  async function chooseCustomer(customerId) {
    if (await act(() => api.post(`/bills/${data.bill.id}/customer/select/`, { customerId }), 'Shopify customer selected.')) {
      customerResults = []; customerQuery = ''; await loadRoute(route, true);
    }
  }

  async function clearCustomer() {
    if (await act(() => api.remove(`/bills/${data.bill.id}/customer/`), 'Customer selection removed.')) await loadRoute(route, true);
  }

  async function createCustomer() {
    if (await act(() => api.post(`/bills/${data.bill.id}/customer/`, customerForm), 'Customer created in Shopify and selected.')) {
      showCustomerCreate = false; customerForm = { first_name: '', last_name: '', email: '', phone: '' };
      await loadRoute(route, true);
    }
  }

  async function completeCheckout() {
    const result = await act(() => api.post(`/bills/${data.bill.id}/checkout/`, payment));
    if (result) await goto(`/app/sales/${data.bill.id}/receipt`);
  }

  async function voidBill(id) {
    if (await act(() => api.remove(`/bills/${id}/`), 'Bill voided.')) await loadRoute('bills', true);
  }

  async function importCatalog() {
    const result = await act(() => api.post('/catalog/'));
    if (result) { notice = `${result.count} variants imported from Shopify.`; await loadRoute('catalog', true); }
  }

  async function addStaff() {
    if (await act(() => api.post('/staff/', staffForm), 'Staff member added.')) {
      staffForm = { name: '', role: 'Stylist', active: true }; await loadRoute('staff', true);
    }
  }

  async function retryOrder(id) {
    await act(() => api.post(`/orders/${id}/retry/`));
    await loadRoute('orders', true);
  }

  async function viewPayload(id) {
    payloadOrder = await act(() => api.get(`/orders/${id}/payload/`));
  }

  const cash = (value, currency = session?.config?.currency || 'KES') => `${currency} ${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const when = (value) => value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';
  const filteredProducts = () => (data?.products || []).filter((p) => (!category || p.category === category) && (!query || `${p.title} ${p.sku}`.toLowerCase().includes(query.toLowerCase())));
</script>

<svelte:head><title>{title} · Karim POS</title><meta name="description" content="Karim Salon & Spa point of sale" /></svelte:head>

{#if !session || loading && !session}
  <div class="center-screen"><div class="loader"></div><p>Opening Karim POS…</p></div>
{:else if !session.authenticated}
  <main class="login-page">
    <section class="login-card">
      <div class="brand-lockup"><img class="brand-logo" src="/static/pos/karimlogo.svg" alt="Karim Salon & Spa" /><div><strong>Karim</strong><small>SALON & SPA</small></div></div>
      <div class="login-copy"><span class="eyebrow">RECEPTION WORKSPACE</span><h1>Welcome back.</h1><p>Sign in to manage today’s sales, clients, and Shopify orders.</p></div>
      {#if error}<div class="alert error">{error}</div>{/if}
      <form on:submit|preventDefault={() => signIn(false)}>
        <label>Username<input bind:value={loginForm.username} autocomplete="username" required /></label>
        <label>Password<input type="password" bind:value={loginForm.password} autocomplete="current-password" required /></label>
        <button class="primary wide" disabled={working}>{working ? 'Signing in…' : 'Sign in'}</button>
      </form>
      {#if session.config?.demoMode}<div class="divider"><span>OR</span></div><button class="secondary wide" on:click={() => signIn(true)} disabled={working}>Explore the local demo</button>{/if}
      <p class="login-foot">Private on-premises workspace · Nairobi</p>
    </section>
    <aside class="login-art"><span class="eyebrow">KARIM EXPERIENCE</span><blockquote>“Every visit begins with a warm welcome.”</blockquote><div class="art-orbit">✦</div></aside>
  </main>
{:else}
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand-lockup" href="/app/"><img class="brand-logo" src="/static/pos/karimlogo.svg" alt="Karim Salon & Spa" /><div><strong>Karim</strong><small>SALON & SPA</small></div></a>
      <div class="location"><span class:online={session.config.shopifyConfigured}></span>Hurlingham Plaza</div>
      <nav>
        <span class="nav-label">RECEPTION</span>
        <a class:active={route === 'dashboard'} href="/app/"><i>⌂</i>Overview</a>
        <button on:click={createBill}><i>＋</i>New sale</button>
        <a class:active={route === 'bills'} href="/app/bills"><i>▱</i>Open bills</a>
        <a class:active={route === 'orders'} href="/app/orders"><i>□</i>Orders</a>
        <span class="nav-label">MANAGE</span>
        <a class:active={route === 'catalog'} href="/app/catalog"><i>◇</i>Catalog</a>
        <a class:active={route === 'staff'} href="/app/staff"><i>♙</i>Staff</a>
        <a class:active={route === 'reports'} href="/app/reports"><i>▥</i>Reports</a>
        <a class:active={route === 'settings'} href="/app/settings"><i>⚙</i>Settings</a>
      </nav>
      <div class="sidebar-foot">
        <div class="connection"><span class:online={session.config.shopifyConfigured}></span>Shopify {session.config.shopifyConfigured ? 'connected' : 'not connected'}</div>
        <div class="profile"><span>{session.user.name.charAt(0).toUpperCase()}</span><div><b>{session.user.name}</b><small>Receptionist</small></div></div>
        <button class="text-button" on:click={signOut}>Sign out ↗</button>
      </div>
    </aside>
    <main class="workspace">
      <div class="topbar"><span>Karim POS <b>/</b> {title}</span><span class="mode">{session.config.demoMode ? 'LOCAL DEMO' : 'LOCAL WORKSPACE'}</span></div>
      <div class="page">
        {#if session.config.demoMode}<div class="demo-banner">● Demo workspace <span>Sample catalog and local payments. Demo orders never sync to Shopify.</span></div>{/if}
        {#if error}<div class="alert error"><span>{error}</span><button on:click={() => error = ''}>×</button></div>{/if}
        {#if notice}<div class="alert success"><span>{notice}</span><button on:click={() => notice = ''}>×</button></div>{/if}
        {#if loading}<div class="loading-line"></div>{/if}

        {#if route === 'dashboard' && data}
          <header><div><span class="eyebrow">KARIM SALON & SPA</span><h1>Good day, {session.user.name.split(' ')[0]}.</h1><p>A calm view of today’s reception desk.</p></div><button class="primary" on:click={createBill}>＋ Create new sale</button></header>
          <div class="date-row"><span><i></i>Reception is ready</span><span>{new Date(data.today).toLocaleDateString(undefined, { weekday:'long', day:'numeric', month:'long', year:'numeric' })}</span></div>
          <section class="stats">
            <article><span>SALES TODAY</span><strong>{cash(data.revenue)}</strong><small>Completed payments</small></article>
            <article><span>COMPLETED SALES</span><strong>{String(data.completed).padStart(2,'0')}</strong><small>Customers checked out</small></article>
            <article><span>OPEN BILLS</span><strong>{String(data.openBills).padStart(2,'0')}</strong><small>Ready to resume</small></article>
            <article><span>AWAITING SHOPIFY</span><strong>{String(data.pendingSync).padStart(2,'0')}</strong><small>Orders in the sync queue</small></article>
          </section>
          <div class="dashboard-grid">
            <section class="card"><div class="section-head"><div><h2>Recent activity</h2><p>Your latest bills and completed sales.</p></div><a href="/app/bills">View open bills ↗</a></div>
              <div class="table-wrap"><table><thead><tr><th>ORDER</th><th>CUSTOMER</th><th>CREATED</th><th>AMOUNT</th><th>STATUS</th></tr></thead><tbody>{#each data.recent as bill}<tr><td><a href={`/app/sales/${bill.id}/${bill.status === 'paid' ? 'receipt' : ''}`}><b>{bill.name}</b></a></td><td>{bill.customer}</td><td>{when(bill.createdAt)}</td><td class="money">{cash(bill.status === 'paid' ? bill.totalPaid : bill.total, bill.currency)}</td><td><span class:paid={bill.status === 'paid'} class="badge {bill.status}">{bill.status}</span></td></tr>{/each}</tbody></table></div>
            </section>
            <div class="stack"><section class="feature"><span class="eyebrow">RECEPTION</span><h2>Beautiful service,<br/>thoughtfully organized.</h2><p>Create a bill for services, retail products, or a little of both.</p><button on:click={createBill}>Start a sale →</button><div>✳</div></section>
              <section class="card quick"><h2>Quick actions</h2><a href="/app/staff">View staff directory <span>↗</span></a><a href="/app/catalog">Browse the catalog <span>↗</span></a><a href="/app/orders">Review Shopify queue <span>↗</span></a><a href="/app/reports">Today’s report <span>↗</span></a></section></div>
          </div>

        {:else if route === 'bills' && data}
          <header><div><span class="eyebrow">ACTIVE SALES</span><h1>Open bills</h1><p>Resume a visit when your customer is ready.</p></div><button class="primary" on:click={createBill}>＋ New sale</button></header>
          <form class="searchbar" on:submit|preventDefault={() => loadRoute('bills', true)}><span>⌕</span><input bind:value={query} placeholder="Search customer, email, or phone…"/><button>Search</button></form>
          <section class="card"><div class="table-wrap"><table><thead><tr><th>BILL</th><th>CUSTOMER</th><th>ITEMS</th><th>STARTED</th><th>TOTAL</th><th></th></tr></thead><tbody>{#each data.bills as bill}<tr><td><b>{bill.name}</b></td><td>{bill.customer}<small>{bill.phone || bill.email}</small></td><td>{bill.lines.length}</td><td>{when(bill.createdAt)}</td><td class="money">{cash(bill.total,bill.currency)}</td><td class="actions"><a class="button-small dark" href={`/app/sales/${bill.id}`}>Resume</a><button class="button-small" on:click={() => voidBill(bill.id)}>Void</button></td></tr>{:else}<tr><td colspan="6"><div class="empty"><span>✦</span><h3>No open bills</h3><p>Start a new sale when your next guest arrives.</p></div></td></tr>{/each}</tbody></table></div></section>

        {:else if route.startsWith('sales/') && !route.includes('/checkout') && !route.includes('/receipt') && data?.bill}
          <header><div><span class="eyebrow">{data.bill.name}</span><h1>Create a sale</h1><p>Select products, assign staff, and prepare checkout.</p></div><a class="secondary" href="/app/bills">Save & return</a></header>
          <div class="steps"><b><i>1</i> Customer & items</b><span></span><b><i>2</i> Assign staff</b><span></span><em><i>3</i> Checkout</em></div>
          <div class="sale-grid"><div class="stack">
            <section class="card customer-card"><div class="section-head"><div><h2>Customer</h2><p>Link a Shopify profile or keep this as a walk-in sale.</p></div>{#if data.bill.shopifyCustomer}<button class="text-button danger" on:click={clearCustomer}>Remove</button>{/if}</div>
              {#if data.bill.shopifyCustomer}<div class="selected-customer"><span>{data.bill.customer.charAt(0)}</span><div><b>{data.bill.customer}</b><small>{data.bill.email || data.bill.phone}</small></div>{#if data.bill.shopifyCustomer.lastOrder}<div class="last-order"><small>LAST ORDER</small><b>{when(data.bill.shopifyCustomer.lastOrder.processedAt)}</b><span>{cash(data.bill.shopifyCustomer.lastOrder.currentTotalPriceSet.shopMoney.amount,data.bill.shopifyCustomer.lastOrder.currentTotalPriceSet.shopMoney.currencyCode)}</span></div>{/if}</div>
              {:else}<div class="customer-actions"><form class="searchbar" on:submit|preventDefault={searchCustomers}><span>⌕</span><input bind:value={customerQuery} placeholder="Search Shopify by name, email, or phone…"/><button>Search</button></form><button class="secondary" on:click={() => showCustomerCreate = !showCustomerCreate}>＋ New customer</button></div>
                {#if customerResults.length}<div class="customer-results">{#each customerResults as customer}<button on:click={() => chooseCustomer(customer.localId)}><span>{customer.displayName.charAt(0)}</span><div><b>{customer.displayName}</b><small>{customer.email || customer.phone}</small></div><em>Select →</em></button>{/each}</div>{/if}
                {#if showCustomerCreate}<form class="form-grid inset" on:submit|preventDefault={createCustomer}><label>First name<input bind:value={customerForm.first_name} required/></label><label>Last name<input bind:value={customerForm.last_name}/></label><label>Email<input type="email" bind:value={customerForm.email}/></label><label>Phone<input bind:value={customerForm.phone} placeholder="+254…"/></label><button class="primary">Create in Shopify</button></form>{/if}
              {/if}
              <form class="form-grid details" on:submit|preventDefault={saveBill}><label>First name<input bind:value={data.bill.firstName} disabled={!!data.bill.shopifyCustomer}/></label><label>Last name<input bind:value={data.bill.lastName} disabled={!!data.bill.shopifyCustomer}/></label><label>Phone<input bind:value={data.bill.phone} disabled={!!data.bill.shopifyCustomer}/></label><label>Email<input type="email" bind:value={data.bill.email} disabled={!!data.bill.shopifyCustomer}/></label><label class="full">Visit notes<textarea rows="2" bind:value={data.bill.notes}></textarea></label><button class="secondary">Save details</button></form>
            </section>
            <section><form class="searchbar" on:submit|preventDefault><span>⌕</span><input bind:value={query} placeholder="Search services, products, or SKU…"/></form><div class="chips"><button class:active={!category} on:click={() => category=''}>All items</button>{#each data.categories as item}<button class:active={category===item} on:click={() => category=item}>{item}</button>{/each}</div>
              <div class="product-grid">{#each filteredProducts() as product}<article class="product-card"><div><span class="product-icon">{product.kind === 'service' ? '✂' : '▧'}</span><span class="badge">{product.category}</span></div><h3>{product.title}</h3><p>{product.sku || (product.kind === 'service' ? 'Salon service' : 'Retail product')}{product.tracked ? ` · ${product.inventory ?? 0} in stock` : ''}</p><footer><b>{cash(product.price)}</b><button on:click={() => addProduct(product.id)}>＋ Add</button></footer></article>{:else}<div class="card empty"><h3>No matching items</h3><p>Try another search or catalog category.</p></div>{/each}</div>
            </section>
          </div><aside class="card bill-panel"><div class="section-head"><h2>Current bill</h2><span class="badge">{data.bill.lines.length} items</span></div><p>Assign the responsible staff member to every line.</p>
            {#each data.bill.lines as line}<article class="bill-line"><div><b>{line.title}</b><strong>{cash(line.total)}</strong></div><div class="line-grid"><label>Qty<input type="number" min="1" bind:value={line.quantity}/></label><label>Price<input type="number" min="0" step="0.01" bind:value={line.price}/></label><label>Discount<input type="number" min="0" step="0.01" bind:value={line.discount}/></label><label class="full">Staff<select bind:value={line.staffId}><option value={null}>Choose staff…</option>{#each data.staff.filter(s=>s.active) as person}<option value={person.id}>{person.name} · {person.role}</option>{/each}</select></label></div><footer><button class="secondary compact" on:click={() => saveLine(line)}>Save line</button><button class="text-button danger" on:click={() => removeLine(line)}>Remove</button></footer></article>{:else}<div class="empty"><span>＋</span><h3>Build their perfect visit</h3><p>Add a service or retail product to begin.</p></div>{/each}
            <div class="totals"><div><span>Subtotal</span><b>{cash(data.bill.subtotal)}</b></div><div><span>Discounts</span><b>− {cash(data.bill.discount)}</b></div><div><span>Total</span><strong>{cash(data.bill.total)}</strong></div></div><a class="primary wide" href={`/app/sales/${data.bill.id}/checkout`}>Continue to checkout →</a>
          </aside></div>

        {:else if route.includes('/checkout') && data?.bill}
          <header><div><span class="eyebrow">{data.bill.name}</span><h1>Payments & checkout</h1><p>Confirm payment received and complete this sale.</p></div><a class="secondary" href={`/app/sales/${data.bill.id}`}>← Back to bill</a></header>
          <div class="checkout-grid"><section class="card"><div class="section-head"><div><h2>{data.bill.customer}</h2><p>{data.bill.phone} {data.bill.email}</p></div><span class="badge open">Open bill</span></div><div class="receipt-lines">{#each data.bill.lines as line}<div><span><b>{line.title}</b><small>{line.staffName || 'Staff unassigned'} · {line.quantity} × {cash(Number(line.price)-Number(line.discount))}</small></span><strong>{cash(line.total)}</strong></div>{/each}</div><div class="totals"><div><span>Subtotal</span><b>{cash(data.bill.subtotal)}</b></div><div><span>Discounts</span><b>− {cash(data.bill.discount)}</b></div><div><span>Total due</span><strong>{cash(data.bill.total)}</strong></div></div>{#if data.bill.notes}<div class="note"><b>Visit notes</b><p>{data.bill.notes}</p></div>{/if}</section>
            <section class="card payment-card"><h2>Record payment</h2><p>Collect payment using your existing terminal, M-Pesa, or cash, then confirm receipt.</p><div class="soft-alert">Karim POS records your confirmation. It does not charge the customer.</div><form on:submit|preventDefault={completeCheckout}><label>Payment method<select bind:value={payment.method}><option value="cash">Cash</option><option value="mpesa">M-Pesa</option><option value="card">Card</option></select></label><label>Amount received<input type="number" min="0" step="0.01" bind:value={payment.tendered} required/></label><label>Payment reference<input bind:value={payment.reference} required={payment.method !== 'cash'} placeholder={payment.method === 'cash' ? 'Optional for cash' : 'Required'}/></label><label class="checkbox"><input type="checkbox" bind:checked={payment.confirmed}/> I have confirmed receipt of this payment.</label><button class="primary wide" disabled={!payment.confirmed || working}>Complete payment · {cash(data.bill.total)}</button></form></section></div>

        {:else if route.includes('/receipt') && data}
          <header><div><span class="eyebrow">SALE COMPLETE</span><h1>{data.name}</h1><p>Payment received and order safely queued.</p></div><button class="secondary" on:click={() => window.print()}>Print receipt</button></header>
          <section class="card receipt"><div class="receipt-title"><div class="brand-mark">K</div><h2>Karim Salon & Spa</h2><p>{when(data.paidAt)}</p><span class="badge paid">Paid</span></div><div class="receipt-meta"><div><small>CUSTOMER</small><b>{data.customer}</b><span>{data.phone || data.email || 'Walk-in'}</span></div><div><small>PAYMENT</small><b>{data.payment?.method?.toUpperCase()}</b><span>{data.payment?.reference || 'No reference'}</span></div><div><small>SHOPIFY SYNC</small><b>{data.sync?.stateLabel || 'Not queued'}</b><span>{data.sync?.attempts || 0} attempts</span></div></div><div class="receipt-lines">{#each data.lines as line}<div><span><b>{line.title}</b><small>{line.staffName} · {line.quantity} × {cash(Number(line.price)-Number(line.discount))}</small></span><strong>{cash(line.total)}</strong></div>{/each}</div><div class="totals"><div><span>Subtotal</span><b>{cash(data.subtotal)}</b></div><div><span>Discounts</span><b>− {cash(data.discount)}</b></div><div><span>Total paid</span><strong>{cash(data.totalPaid)}</strong></div>{#if data.payment?.change > 0}<div><span>Change</span><b>{cash(data.payment.change)}</b></div>{/if}</div><div class="receipt-actions"><button class="primary" on:click={createBill}>Start another sale</button><a class="secondary" href="/app/orders">View Shopify queue</a></div></section>

        {:else if route === 'orders' && data}
          <header><div><span class="eyebrow">SHOPIFY</span><h1>Orders & sync</h1><p>Completed sales, delivery status, and retry history.</p></div></header>{#if !data.liveSync}<div class="soft-alert">Live order sync is off. Completed orders are stored locally until the Shopify connection is validated.</div>{/if}
          <div class="chips">{#each ['', 'pending','synced','failed','review'] as item}<button class:active={stateFilter===item} on:click={() => {stateFilter=item; loadRoute('orders',true)}}>{item ? item.replace('review','Needs review') : 'All orders'}</button>{/each}</div>
          <section class="card"><div class="table-wrap"><table><thead><tr><th>ORDER</th><th>CUSTOMER</th><th>PAYMENT</th><th>SHOPIFY SYNC</th><th>ATTEMPTS</th><th></th></tr></thead><tbody>{#each data.orders as order}<tr><td><a href={`/app/sales/${order.bill.id}/receipt`}><b>{order.bill.name}</b></a><small>{when(order.bill.paidAt)}</small></td><td>{order.bill.customer}</td><td class="money">{cash(order.bill.totalPaid,order.bill.currency)}</td><td><span class="badge {order.state}">{order.stateLabel}</span>{#if order.error}<details><summary>Details</summary><p>{order.error}</p></details>{/if}</td><td>{order.attempts}</td><td class="actions"><button class="button-small" on:click={() => viewPayload(order.id)}>Payload</button>{#if ['pending','failed'].includes(order.state)}<button class="button-small dark" on:click={() => retryOrder(order.id)}>Send</button>{/if}</td></tr>{:else}<tr><td colspan="6"><div class="empty"><h3>No orders in this view</h3></div></td></tr>{/each}</tbody></table></div></section>
          {#if payloadOrder}<div class="modal-backdrop" role="presentation" on:click={() => payloadOrder=null}><section class="modal" role="dialog" aria-modal="true" tabindex="0" on:click|stopPropagation on:keydown|stopPropagation><div class="section-head"><div><span class="eyebrow">{payloadOrder.bill}</span><h2>Shopify payload</h2></div><button class="close" on:click={() => payloadOrder=null}>×</button></div><pre>{JSON.stringify(payloadOrder.payload,null,2)}</pre></section></div>{/if}

        {:else if route === 'catalog' && data}
          <header><div><span class="eyebrow">SHOPIFY CATALOG</span><h1>Services & products</h1><p>Browse the Shopify-owned catalog available at reception.</p></div><button class="primary" on:click={importCatalog} disabled={working}>↻ Pull Shopify catalog</button></header><form class="searchbar" on:submit|preventDefault={() => loadRoute('catalog',true)}><span>⌕</span><input bind:value={query} placeholder="Search name or SKU…"/><button>Search</button></form>
          <section class="card"><div class="table-wrap"><table><thead><tr><th>ITEM</th><th>SKU</th><th>TYPE</th><th>PRICE</th><th>INVENTORY</th><th>STATUS</th></tr></thead><tbody>{#each data.products as product}<tr><td><b>{product.title}</b><small>{product.category}</small></td><td>{product.sku || '—'}</td><td>{product.kind}</td><td class="money">{cash(product.price)}</td><td>{product.tracked ? `${product.inventory ?? 0} tracked` : 'Not tracked'}</td><td><span class="badge" class:paid={product.active}>{product.active ? 'Active' : 'Inactive'}</span></td></tr>{/each}</tbody></table></div></section>

        {:else if route === 'staff' && data}
          <header><div><span class="eyebrow">TEAM</span><h1>Staff directory</h1><p>Assignments and today’s attributed sales.</p></div></header><div class="staff-grid">{#each data.staff as person}<article class="card staff-card"><div><span class="avatar">{person.name.charAt(0)}</span><span class="badge" class:paid={person.active}>{person.active ? 'Available' : 'Inactive'}</span></div><h3>{person.name}</h3><p>{person.role}</p><footer><span>{person.openLines} open items</span><b>{cash(person.revenue)}</b></footer></article>{/each}</div><section class="card staff-form"><div><h2>Add staff member</h2><p>Create a staff profile for line-item assignments.</p></div><form on:submit|preventDefault={addStaff}><label>Name<input bind:value={staffForm.name} required/></label><label>Role<input bind:value={staffForm.role} required/></label><label class="checkbox"><input type="checkbox" bind:checked={staffForm.active}/> Active</label><button class="primary">Add staff member</button></form></section>

        {:else if route === 'reports' && data}
          <header><div><span class="eyebrow">TODAY</span><h1>Daily operations report</h1><p>Revenue, category contribution, and audit activity.</p></div><a class="secondary" href="/reports/export/">Export CSV</a></header><section class="stats three"><article><span>GROSS REVENUE</span><strong>{cash(data.revenue)}</strong><small>{data.completed} completed sales</small></article><article><span>COMPLETED SALES</span><strong>{data.completed}</strong><small>Payments recorded today</small></article><article><span>SHOPIFY QUEUE</span><strong>{data.pendingSync}</strong><small>Orders awaiting resolution</small></article></section><div class="report-grid"><section class="card"><h2>Revenue by category</h2>{#each data.categories as item}<div class="report-row"><span>{item.name}</span><b>{cash(item.amount)}</b></div>{:else}<div class="empty"><p>No paid sales today.</p></div>{/each}</section><section class="card"><h2>Recent audit activity</h2>{#each data.events as event}<div class="audit-row"><span></span><div><b>{event.event}</b><small>{event.bill} · {event.actor} · {when(event.createdAt)}</small></div></div>{/each}</section></div>

        {:else if route === 'settings' && data}
          <header><div><span class="eyebrow">CONFIGURATION</span><h1>Settings</h1><p>Connection and local workspace status.</p></div></header><div class="settings-grid"><section class="card"><div class="section-head"><h2>Shopify connection</h2><span class="badge" class:paid={data.shopifyConfigured}>{data.shopifyConfigured ? 'Configured' : 'Not connected'}</span></div><dl><div><dt>Store</dt><dd>{data.store || 'Not configured'}</dd></div><div><dt>Admin API</dt><dd>{data.apiVersion}</dd></div><div><dt>Live order sync</dt><dd>{data.liveSync ? 'Enabled' : 'Disabled'}</dd></div><div><dt>Inventory behavior</dt><dd>{data.inventoryBehaviour}</dd></div></dl></section><section class="card"><h2>Local workspace</h2><dl><div><dt>Currency</dt><dd>{data.currency}</dd></div><div><dt>Mode</dt><dd>{data.demoMode ? 'Demo' : 'Production'}</dd></div></dl><div class="soft-alert">Credentials stay in the server’s local environment file and are never returned to this browser.</div></section></div>
        {/if}
        <footer class="page-foot"><span>Karim POS</span><span>***</span></footer>
      </div>
    </main>
  </div>
{/if}
