# -*- coding: utf-8 -*-
from odoo import models, fields, api

class JandalahHrPenalty(models.Model):
    _name = 'jandalah.hr.penalty'
    _description = 'جزاءات وتحقيقات إدارية'
    _order = 'date desc, id desc'

    name = fields.Char(string='رقم / مرجع القرار', required=True, default='PEN')
    employee_id = fields.Many2one('hr.employee', string='الموظف المعني', required=True, ondelete='cascade')
    penalty_type = fields.Selection([
        ('warning', 'لفت نظر / إنذار كتابي'),
        ('deduction', 'خصم من الراتب'),
        ('investigation', 'إحالة للتحقيق الإداري'),
        ('suspension', 'إيقاف مؤقت عن العمل'),
    ], string='نوع الإجراء', default='warning', required=True)

    date = fields.Date(string='تاريخ القرار / الإحالة', default=fields.Date.today, required=True)
    reason = fields.Text(string='الموضوع / سبب الإجراء أو التحقيق', required=True)
    deduction_days = fields.Float(string='عدد أيام الخصم', default=0.0)

    state = fields.Selection([
        ('issued', 'صادر وبانتظار رد الموظف'),
        ('accepted', 'تم الإقرار والقبول'),
        ('objected', 'تم تسجيل اعتراض وإفادة'),
        ('closed', 'مغلق ومحفوظ'),
    ], string='الحالة', default='issued', required=True)

    employee_response = fields.Selection([
        ('accept', 'قبول وإقرار بالجزاء'),
        ('object', 'اعتراض وعدم موافقة'),
    ], string='رد الموظف')

    employee_comment = fields.Text(string='إفادة وتبرير الموظف')
    response_date = fields.Datetime(string='تاريخ وساعة الرد')
    attachment_id = fields.Many2one('ir.attachment', string='المستند الرسمي المرفق')
