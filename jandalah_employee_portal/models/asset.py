# -*- coding: utf-8 -*-
from odoo import models, fields, api

class JandalahHrAsset(models.Model):
    _name = 'jandalah.hr.asset'
    _description = 'العهد العينية والرقمية للموظفين'
    _order = 'handover_date desc, id desc'

    name = fields.Char(string='اسم العهدة / الوصف', required=True)
    asset_code = fields.Char(string='الرقم التسلسلي / كود العهدة')
    category = fields.Selection([
        ('laptop', 'حاسوب محمول / لابتوب'),
        ('mobile', 'هاتف محمول / شريحة اتصال'),
        ('car', 'سيارة شركة'),
        ('tools', 'أدوات ومعدات عمل ميداني'),
        ('digital', 'بريد إلكتروني / حساب رقمي'),
        ('other', 'عهدة أخرى'),
    ], string='تصنيف العهدة', default='laptop', required=True)

    employee_id = fields.Many2one('hr.employee', string='الموظف المستلم', required=True, ondelete='cascade')
    handover_date = fields.Date(string='تاريخ تسليم العهدة', default=fields.Date.today, required=True)
    return_date = fields.Date(string='تاريخ استرداد العهدة')
    notes = fields.Text(string='ملاحظات العهدة وحالتها الفنية')

    state = fields.Selection([
        ('assigned', 'مسندة بانتظار إقرار الاستلام'),
        ('acknowledged', 'تم تأكيد الاستلام رسمياً'),
        ('returned', 'تم إرجاع العهدة للشركة'),
    ], string='الحالة', default='assigned', required=True)

    ack_date = fields.Datetime(string='تاريخ وساعة تأكيد الاستلام')
    ack_signature = fields.Char(string='إقرار الاستلام الإلكتروني')
