# -*- coding: utf-8 -*-
from odoo import models, fields, api

class JandalahLocationRequest(models.Model):
    _name = 'jandalah.location.request'
    _description = 'طلبات العمل من موقع مؤقت / بديل'
    _order = 'date_from desc, id desc'

    employee_id = fields.Many2one('hr.employee', string='الموظف', required=True, ondelete='cascade')
    date_from = fields.Date(string='من تاريخ', default=fields.Date.today, required=True)
    date_to = fields.Date(string='إلى تاريخ', default=fields.Date.today, required=True)
    temp_location_name = fields.Char(string='اسم الموقع المؤقت (المدينة / الحي)', required=True)
    reason = fields.Text(string='سبب طلب العمل من موقع بديل', required=True)
    
    temp_latitude = fields.Float(string='خط عرض الموقع المؤقت', digits=(10, 7))
    temp_longitude = fields.Float(string='خط طول الموقع المؤقت', digits=(10, 7))
    temp_radius_meters = fields.Integer(string='نطاق السماح (بالمتر)', default=500)

    state = fields.Selection([
        ('confirm', 'قيد مراجعة المشرف'),
        ('approve', 'معتمد'),
        ('refuse', 'مرفوض'),
    ], string='الحالة', default='confirm', required=True)

    supervisor_id = fields.Many2one('hr.employee', string='المشرف المعتمد')
    action_date = fields.Datetime(string='تاريخ اتخاذ القرار')
    refusal_reason = fields.Text(string='سبب الرفض')
