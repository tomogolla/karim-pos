document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); }));

document.querySelectorAll('[data-customer-picker]').forEach(picker => {
  const searchForm = picker.querySelector('[data-customer-search]');
  const results = picker.querySelector('[data-customer-results]');
  const status = picker.querySelector('[data-customer-status]');
  const more = picker.querySelector('[data-more-customers]');
  const selectForm = picker.querySelector('[data-customer-select]');
  let searchTerm = '', cursor = null, sequence = 0, controller;
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const orderText = customer => {
    const order = customer.lastOrder;
    if (!order) return 'No accessible order history';
    const date = order.processedAt ? new Date(order.processedAt).toLocaleString() : 'Date unavailable';
    const money = order.currentTotalPriceSet?.shopMoney;
    const total = money?.amount != null ? ` · ${money.currencyCode} ${money.amount}` : '';
    return `Latest order: ${date}${total}`;
  };
  async function readResponse(response) {
    if (response.redirected || !(response.headers.get('content-type') || '').includes('application/json')) {
      throw new Error('Your session may have expired. Sign in again, then retry.');
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Shopify is unavailable. Try again.');
    return data;
  }
  async function search(append) {
    const current = ++sequence;
    controller?.abort();
    controller = new AbortController();
    if (!append) { results.replaceChildren(); cursor = null; }
    status.textContent = 'Searching Shopify…';
    more.hidden = true;
    const query = new URLSearchParams({q: searchTerm});
    if (append && cursor) query.set('after', cursor);
    try {
      const data = await readResponse(await fetch(`${picker.dataset.searchUrl}?${query}`, {
        signal: controller.signal, credentials: 'same-origin', cache: 'no-store'
      }));
      if (current !== sequence) return;
      data.customers.forEach(customer => {
        const row = element('div', undefined, 'customer-result');
        const info = element('div');
        info.append(element('strong', customer.displayName));
        info.append(element('p', [customer.email, customer.phone].filter(Boolean).join(' · ') || 'No contact details', 'small'));
        info.append(element('small', orderText(customer), 'muted'));
        const button = element('button', 'Select customer', 'secondary small-button');
        button.type = 'button';
        button.addEventListener('click', () => {
          selectForm.elements.customer_id.value = customer.localId;
          button.disabled = true;
          status.textContent = 'Confirming customer and latest order…';
          selectForm.requestSubmit();
        });
        row.append(info, button);
        results.append(row);
      });
      cursor = data.pageInfo.endCursor;
      more.hidden = !data.pageInfo.hasNextPage;
      status.textContent = results.children.length ? `${results.children.length} matching customers shown. Select the correct profile below.` : 'No matching customers found. Try another search or add a new customer below.';
    } catch (error) {
      if (current !== sequence || error.name === 'AbortError') return;
      status.textContent = error.message;
      if (append && cursor) more.hidden = false;
    }
  }
  searchForm.addEventListener('submit', event => {
    event.preventDefault(); searchTerm = searchForm.elements.customer_query.value.trim();
    if (searchTerm.length < 2) { status.textContent = 'Enter at least two characters.'; return; }
    search(false);
  });
  more.addEventListener('click', () => search(true));
  picker.querySelector('[data-create-customer]')?.addEventListener('submit', event => {
    event.currentTarget.querySelector('button').disabled = true;
  });
  const refresh = picker.querySelector('[data-refresh-customer]');
  refresh?.addEventListener('click', async () => {
    refresh.disabled = true; status.textContent = 'Refreshing customer history…';
    try {
      const customer = await readResponse(await fetch(refresh.dataset.refreshCustomer, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'X-CSRFToken': selectForm.elements.csrfmiddlewaretoken.value}
      }));
      const history = picker.querySelector('[data-customer-history]');
      history.replaceChildren(element('p', orderText(customer)));
      history.append(element('p', `${customer.lastOrder?.id || 'No accessible order'} · Fetched ${new Date(customer.fetchedAt).toLocaleString()}. Order total is not a verified payment amount.`, 'small muted'));
      status.textContent = 'Latest Shopify order details refreshed.';
    } catch (error) { status.textContent = error.message; }
    finally { refresh.disabled = false; }
  });
});
