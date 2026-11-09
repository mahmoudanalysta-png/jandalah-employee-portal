# -*- coding: utf-8 -*-
from odoo import http, fields, _
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from datetime import datetime, date
import json
import logging

_logger = logging.getLogger(__name__)

class JandalahSalesPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        emp = self._get_salesperson_employee()
        is_sales = False
        if emp:
            job_name = (emp.sudo().job_id and emp.sudo().job_id.name or '').strip().lower()
            job_title = (emp.sudo().job_title or '').strip().lower()
            sales_keywords = ['اخصائي مبيعات', 'أخصائي مبيعات', 'sales specialist']
            if any(k in job_name or k in job_title for k in sales_keywords):
                is_sales = True
        values['is_sales_employee'] = is_sales
        return values

    def _get_salesperson_employee(self):
        """Finds current employee and verifies sales privileges."""
        user = request.env.user
        company = request.env.company
        # 1. Search in current company first
        emp = request.env['hr.employee'].sudo().search([
            ('user_id', '=', user.id),
            ('company_id', '=', company.id)
        ], order='id desc', limit=1)
        if not emp:
            emp = request.env['hr.employee'].sudo().search([('user_id', '=', user.id)], order='id desc', limit=1)
        if not emp:
            user_email = (user.email or '').strip()
            if user_email:
                emp = request.env['hr.employee'].sudo().search([
                    ('company_id', '=', company.id),
                    ('work_email', '=ilike', user_email)
                ], order='id desc', limit=1)
                if not emp:
                    emp = request.env['hr.employee'].sudo().search([
                        ('work_email', '=ilike', user_email)
                    ], order='id desc', limit=1)
        return emp

    # PWA Manifest & ServiceWorker for Van Sales Portal
    @http.route('/jandalah/sales/manifest.json', type='http', auth='public', methods=['GET'], csrf=False)
    def sales_pwa_manifest(self, **kw):
        manifest_data = {
            "id": "/my/sales",
            "name": "مبيعات جندله",
            "short_name": "مبيعات جندله",
            "start_url": "/my/sales",
            "scope": "/my/sales",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#198754",
            "description": "بوابة مبيعات وتحصيل سيارات التوزيع وجرد المستودع الميداني - شركة جندله",
            "orientation": "portrait-primary",
            "icons": [
                {
                    "src": "/jandalah_sales_portal/static/src/img/sales-icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable"
                },
                {
                    "src": "/jandalah_sales_portal/static/src/img/sales-icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable"
                }
            ]
        }
        return request.make_response(
            json.dumps(manifest_data, ensure_ascii=False),
            headers=[
                ('Content-Type', 'application/manifest+json; charset=utf-8'),
                ('Cache-Control', 'public, max-age=3600'),
            ]
        )

    @http.route('/jandalah/sales/sw.js', type='http', auth='public', methods=['GET'], csrf=False)
    def sales_pwa_sw(self, **kw):
        sw_code = """
const CACHE_NAME = 'jandalah-sales-v2';
self.addEventListener('install', event => {
    self.skipWaiting();
});
self.addEventListener('activate', event => {
    event.waitUntil(clients.claim());
});
self.addEventListener('fetch', event => {
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
"""
        return request.make_response(
            sw_code,
            headers=[
                ('Content-Type', 'application/javascript; charset=utf-8'),
                ('Cache-Control', 'public, max-age=3600'),
            ]
        )

    def _get_user_company(self):
        """Resolves active company (Al-Qanadil Al-Mushriqa or Material Trading or Jandalah)."""
        return request.env.company

    # 1. Sales Main Dashboard
    @http.route('/my/sales', type='http', auth='user', website=True)
    def sales_dashboard(self, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()
        SaleOrder = request.env['sale.order'].sudo()
        Payment = request.env['account.payment'].sudo()

        today_start = datetime.combine(date.today(), datetime.min.time())
        today_end = datetime.combine(date.today(), datetime.max.time())

        # Domain for current salesperson / company
        order_domain = [('company_id', '=', company.id)]
        if emp and emp.user_id:
            order_domain.append(('user_id', '=', emp.user_id.id))

        today_orders = SaleOrder.search(order_domain + [
            ('date_order', '>=', today_start),
            ('date_order', '<=', today_end),
            ('state', 'in', ['sale', 'done'])
        ])

        today_sales_total = sum(o.amount_total for o in today_orders)
        today_orders_count = len(today_orders)

        # Recent 5 orders
        recent_orders = SaleOrder.search(order_domain, order='date_order desc', limit=5)

        # Van stock items count
        van_items_count = 0
        if emp and emp.van_location_id:
            quants = request.env['stock.quant'].sudo().search([
                ('location_id', '=', emp.van_location_id.id),
                ('quantity', '>', 0)
            ])
            van_items_count = len(quants)

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_dashboard',
            'employee': emp,
            'company': company,
            'today_sales_total': today_sales_total,
            'today_orders_count': today_orders_count,
            'recent_orders': recent_orders,
            'van_items_count': van_items_count,
            'van_location': emp.van_location_id if emp else False,
        })
        return request.render('jandalah_sales_portal.sales_dashboard_view', values)

    # 2. New Sales Order Form
    @http.route('/my/sales/order/new', type='http', auth='user', website=True)
    def sales_order_new(self, error=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()

        # Customers of current company
        customers = request.env['res.partner'].sudo().search([
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
            ('customer_rank', '>', 0)
        ], order='name asc', limit=100)
        if not customers:
            customers = request.env['res.partner'].sudo().search([
                '|', ('company_id', '=', False), ('company_id', '=', company.id)
            ], order='name asc', limit=50)

        # Available products from van stock or saleable products
        products = request.env['product.product'].sudo().search([
            ('sale_ok', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', company.id)
        ], order='name asc', limit=200)

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_new_order',
            'employee': emp,
            'company': company,
            'customers': customers,
            'products': products,
            'error': error,
            'van_location': emp.van_location_id if emp else False,
        })
        return request.render('jandalah_sales_portal.sales_order_new_view', values)

    # 3. Create Sales Order Action
    @http.route('/my/sales/order/create', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def sales_order_create(self, partner_id=None, payment_method='cash', latitude=None, longitude=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()

        if not partner_id:
            return request.redirect('/my/sales/order/new?error=missing_partner')

        partner = request.env['res.partner'].sudo().browse(int(partner_id))
        if not partner.exists():
            return request.redirect('/my/sales/order/new?error=invalid_partner')

        # Check credit limit if payment is credit / on due
        if payment_method == 'credit' and getattr(partner, 'credit_limit', 0) > 0:
            if partner.credit >= partner.credit_limit:
                return request.redirect(f'/my/sales/order/new?error=credit_limit_exceeded&partner_id={partner.id}')

        # Parse order lines from dynamic POST fields: product_id_X, quantity_X, price_X, discount_X
        order_lines = []
        product_ids = request.httprequest.form.getlist('product_id[]')
        quantities = request.httprequest.form.getlist('quantity[]')
        prices = request.httprequest.form.getlist('price[]')
        discounts = request.httprequest.form.getlist('discount[]')

        for i in range(len(product_ids)):
            p_id = int(product_ids[i]) if product_ids[i] else 0
            qty = float(quantities[i]) if i < len(quantities) and quantities[i] else 1.0
            price = float(prices[i]) if i < len(prices) and prices[i] else 0.0
            disc = float(discounts[i]) if i < len(discounts) and discounts[i] else 0.0

            if p_id and qty > 0:
                prod = request.env['product.product'].sudo().browse(p_id)
                unit_price = price if price > 0 else prod.lst_price
                order_lines.append((0, 0, {
                    'product_id': prod.id,
                    'name': prod.display_name,
                    'product_uom_qty': qty,
                    'price_unit': unit_price,
                    'discount': disc,
                }))

        if not order_lines:
            return request.redirect('/my/sales/order/new?error=no_products')

        order_vals = {
            'partner_id': partner.id,
            'company_id': company.id,
            'user_id': emp.user_id.id if emp and emp.user_id else request.env.user.id,
            'payment_method_type': payment_method,
            'van_location_id': emp.van_location_id.id if emp and emp.van_location_id else False,
            'order_line': order_lines,
        }

        if latitude and longitude:
            try:
                order_vals['order_latitude'] = float(latitude)
                order_vals['order_longitude'] = float(longitude)
            except (ValueError, TypeError):
                pass

        try:
            order = request.env['sale.order'].sudo().create(order_vals)
            order.action_confirm()
            order.generate_zatca_qr()
            return request.redirect(f'/my/sales/order/{order.id}?success=created')
        except Exception as e:
            _logger.exception("Error creating sales order from portal: %s", e)
            return request.redirect(f'/my/sales/order/new?error={str(e)}')

    # 4. View Single Order & Invoice with ZATCA QR
    @http.route('/my/sales/order/<int:order_id>', type='http', auth='user', website=True)
    def sales_order_view(self, order_id, success=None, **kw):
        emp = self._get_salesperson_employee()
        order = request.env['sale.order'].sudo().browse(order_id)
        if not order.exists():
            return request.redirect('/my/sales')

        if not order.zatca_qr_code:
            order.generate_zatca_qr()

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_order_detail',
            'employee': emp,
            'order': order,
            'success': success,
        })
        return request.render('jandalah_sales_portal.sales_order_detail_view', values)

    # 5. Orders History List
    @http.route(['/my/sales/orders', '/my/sales/orders/page/<int:page>'], type='http', auth='user', website=True)
    def sales_orders_list(self, page=1, search=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()
        SaleOrder = request.env['sale.order'].sudo()

        domain = [('company_id', '=', company.id)]
        if emp and emp.user_id:
            domain.append(('user_id', '=', emp.user_id.id))

        if search:
            domain.extend(['|', ('name', 'ilike', search), ('partner_id.name', 'ilike', search)])

        total_orders = SaleOrder.search_count(domain)
        items_per_page = 20
        pager = portal_pager(url='/my/sales/orders', total=total_orders, page=page, step=items_per_page)
        offset = (page - 1) * items_per_page
        orders = SaleOrder.search(domain, order='date_order desc', limit=items_per_page, offset=offset)

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_orders_list',
            'employee': emp,
            'orders': orders,
            'pager': pager,
            'search': search,
            'default_url': '/my/sales/orders',
        })
        return request.render('jandalah_sales_portal.sales_orders_list_view', values)

    # 6. Customers Directory with Balances & Credit Limits
    @http.route('/my/sales/customers', type='http', auth='user', website=True)
    def sales_customers(self, search=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()

        domain = ['|', ('company_id', '=', False), ('company_id', '=', company.id)]
        if search:
            domain.extend(['|', '|', ('name', 'ilike', search), ('phone', 'ilike', search), ('mobile', 'ilike', search)])

        customers = request.env['res.partner'].sudo().search(domain, order='name asc', limit=50)

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_customers',
            'employee': emp,
            'customers': customers,
            'search': search,
        })
        return request.render('jandalah_sales_portal.sales_customers_view', values)

    # 7. Add New Customer with GPS
    @http.route('/my/sales/customer/create', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def sales_customer_create(self, name=None, phone=None, credit_limit=0.0, latitude=None, longitude=None, **kw):
        company = self._get_user_company()
        if not name:
            return request.redirect('/my/sales/customers?error=missing_name')

        vals = {
            'name': name.strip(),
            'phone': phone.strip() if phone else False,
            'company_id': company.id,
            'customer_rank': 1,
            'credit_limit': float(credit_limit) if credit_limit else 0.0,
        }
        if latitude and longitude:
            try:
                vals['customer_lat'] = float(latitude)
                vals['customer_lng'] = float(longitude)
            except (ValueError, TypeError):
                pass

        partner = request.env['res.partner'].sudo().create(vals)
        return request.redirect('/my/sales/customers?success=created')

    # 8. Payments & Collection List
    @http.route('/my/sales/payments', type='http', auth='user', website=True)
    def sales_payments(self, success=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()
        Payment = request.env['account.payment'].sudo()

        domain = [('company_id', '=', company.id), ('payment_type', '=', 'inbound')]
        payments = Payment.search(domain, order='date desc', limit=30)

        customers = request.env['res.partner'].sudo().search([
            '|', ('company_id', '=', False), ('company_id', '=', company.id)
        ], order='name asc', limit=100)

        journals = request.env['account.journal'].sudo().search([
            ('company_id', '=', company.id),
            ('type', 'in', ['cash', 'bank'])
        ])

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_payments',
            'employee': emp,
            'payments': payments,
            'customers': customers,
            'journals': journals,
            'success': success,
        })
        return request.render('jandalah_sales_portal.sales_payments_view', values)

    # 9. Register Payment (سند قبض وتحصيل)
    @http.route('/my/sales/payment/create', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def sales_payment_create(self, partner_id=None, amount=0.0, journal_id=None, memo=None, **kw):
        company = self._get_user_company()
        if not partner_id or float(amount or 0) <= 0:
            return request.redirect('/my/sales/payments?error=invalid_data')

        journal = request.env['account.journal'].sudo().browse(int(journal_id)) if journal_id else False
        if not journal or not journal.exists():
            journal = request.env['account.journal'].sudo().search([
                ('company_id', '=', company.id),
                ('type', '=', 'cash')
            ], limit=1)

        pay_vals = {
            'partner_id': int(partner_id),
            'amount': float(amount),
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'company_id': company.id,
            'journal_id': journal.id if journal else False,
            'ref': memo or 'تحصيل مبيعات ميداني',
        }

        try:
            pay = request.env['account.payment'].sudo().create(pay_vals)
            pay.action_post()
            return request.redirect('/my/sales/payments?success=collected')
        except Exception as e:
            _logger.exception("Error registering payment: %s", e)
            return request.redirect(f'/my/sales/payments?error={str(e)}')

    # 10. Van Stock Inquiry (مخزن سيارة المندوب)
    @http.route('/my/sales/stock/van', type='http', auth='user', website=True)
    def sales_stock_van(self, search=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()

        van_loc = emp.van_location_id if emp else False
        quants = []
        if van_loc:
            domain = [('location_id', '=', van_loc.id), ('quantity', '>', 0)]
            if search:
                domain.append(('product_id.name', 'ilike', search))
            quants = request.env['stock.quant'].sudo().search(domain, order='product_id asc')

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_van_stock',
            'employee': emp,
            'van_location': van_loc,
            'quants': quants,
            'search': search,
        })
        return request.render('jandalah_sales_portal.sales_van_stock_view', values)

    # 11. Main Warehouse Stock Inquiry (المستودع الرئيسي)
    @http.route('/my/sales/stock/main', type='http', auth='user', website=True)
    def sales_stock_main(self, search=None, **kw):
        emp = self._get_salesperson_employee()
        company = self._get_user_company()

        # Find main warehouse internal location
        main_wh = request.env['stock.warehouse'].sudo().search([('company_id', '=', company.id)], limit=1)
        lot_stock_id = main_wh.lot_stock_id if main_wh else False

        domain = [('quantity', '>', 0)]
        if lot_stock_id:
            domain.append(('location_id', 'child_of', lot_stock_id.id))
        else:
            domain.append(('location_id.usage', '=', 'internal'))
            domain.append(('company_id', '=', company.id))

        if search:
            domain.append(('product_id.name', 'ilike', search))

        quants = request.env['stock.quant'].sudo().search(domain, order='product_id asc', limit=100)

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'sales_main_stock',
            'employee': emp,
            'company': company,
            'main_warehouse': main_wh,
            'quants': quants,
            'search': search,
        })
        return request.render('jandalah_sales_portal.sales_main_stock_view', values)

    # 12. Dynamic JSON API: Customer Financial Info (Balance & Credit Limit)
    @http.route('/api/sales/customer/info', type='json', auth='user', methods=['POST'], csrf=False)
    def api_customer_info(self, partner_id, **kw):
        if not partner_id:
            return {'status': 'error', 'message': 'partner_id is required'}
        partner = request.env['res.partner'].sudo().browse(int(partner_id))
        if not partner.exists():
            return {'status': 'error', 'message': 'Customer not found'}
        return {
            'status': 'success',
            'data': partner.get_financial_summary(),
        }
