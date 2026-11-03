# -*- coding: utf-8 -*-
from odoo import models, fields

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    van_location_id = fields.Many2one(
        'stock.location',
        string='مستودع/موقع سيارة التوزيع (Van Stock)',
        domain=[('usage', '=', 'internal')],
        help='موقع المخزون المخصص لسيارة هذا المندوب في جولات التوزيع الميداني'
    )
