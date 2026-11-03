# -*- coding: utf-8 -*-
from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    customer_lat = fields.Float(string='خط العرض GPS', digits=(10, 7))
    customer_lng = fields.Float(string='خط الطول GPS', digits=(10, 7))

    def get_financial_summary(self):
        """Returns customer balance and credit limit for mobile sales portal."""
        self.ensure_one()
        balance = getattr(self, 'credit', 0.0) or 0.0
        limit = getattr(self, 'credit_limit', 0.0) or 0.0
        available_credit = (limit - balance) if limit > 0 else 9999999.0
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone or self.mobile or '',
            'balance': round(balance, 2),
            'credit_limit': round(limit, 2),
            'available_credit': round(available_credit, 2),
            'is_over_limit': (balance >= limit) if limit > 0 else False,
        }
