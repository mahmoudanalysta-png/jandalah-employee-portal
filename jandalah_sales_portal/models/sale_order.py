# -*- coding: utf-8 -*-
import base64
from datetime import datetime
from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    payment_method_type = fields.Selection([
        ('cash', 'نقداً (Cash)'),
        ('card', 'شبكة / مدى (Card)'),
        ('bank', 'تحويل بنكي (Bank Transfer)'),
        ('credit', 'آجل (On Credit)'),
    ], string='طريقة السداد', default='cash')

    van_location_id = fields.Many2one('stock.location', string='موقع مخزن السيارة المنصرف منه')
    order_latitude = fields.Float('موقع البيع GPS (عرض)', digits=(10, 7))
    order_longitude = fields.Float('موقع البيع GPS (طول)', digits=(10, 7))
    zatca_qr_code = fields.Text('رمز الزكاة QR')

    def generate_zatca_qr(self):
        """Generates simplified TLV Base64 ZATCA compliant QR code payload."""
        for order in self:
            seller_name = order.company_id.name or 'شركة جندله'
            vat_num = order.company_id.vat or '300000000000003'
            time_str = order.date_order.strftime('%Y-%m-%dT%H:%M:%SZ') if order.date_order else datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            total_str = f"{order.amount_total:.2f}"
            tax_str = f"{order.amount_tax:.2f}"

            def get_tlv(tag, val):
                val_bytes = str(val).encode('utf-8')
                return bytes([tag, len(val_bytes)]) + val_bytes

            tlv = (
                get_tlv(1, seller_name) +
                get_tlv(2, vat_num) +
                get_tlv(3, time_str) +
                get_tlv(4, total_str) +
                get_tlv(5, tax_str)
            )
            order.zatca_qr_code = base64.b64encode(tlv).decode('utf-8')
