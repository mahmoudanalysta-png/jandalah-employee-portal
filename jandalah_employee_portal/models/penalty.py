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
    issued_by = fields.Char(string='الجهة أو المسؤول المصدر', default='إدارة الموارد البشرية', help='مثال: الشؤون القانونية / إدارة الموارد البشرية')
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
    attachment_id = fields.Many2one('ir.attachment', string='المستند الرسمي (سجل قديم)')
    attachment_file = fields.Binary(string='رفع المستند الرسمي (PDF / صورة / مستند)', attachment=True)
    attachment_name = fields.Char(string='اسم الملف المرفق')

    def get_official_attachment_info(self):
        """Safely retrieves attachment metadata and data for penalty."""
        self.ensure_one()
        if self.attachment_file or self.attachment_name:
            return {
                'has_attachment': True,
                'data': self.attachment_file,
                'name': self.attachment_name or f"penalty_{self.id}.pdf",
                'source': 'binary',
            }

        Att = self.env['ir.attachment'].sudo()
        att = Att.search([
            ('res_model', '=', 'jandalah.hr.penalty'),
            ('res_id', '=', self.id),
            ('name', '!=', 'loader.js'),
            ('name', 'not like', '%.js'),
        ], order='id desc', limit=1)
        if att and (att.datas or att.db_datas or att.store_fname):
            return {
                'has_attachment': True,
                'data': att.datas,
                'name': att.name or f"penalty_{self.id}.pdf",
                'source': 'ir_attachment',
                'id': att.id,
            }

        if self.attachment_id:
            att = self.attachment_id
            if att.name and att.name != 'loader.js' and not att.name.endswith('.js') and (att.datas or att.db_datas or att.store_fname):
                return {
                    'has_attachment': True,
                    'data': att.datas,
                    'name': att.name or f"penalty_{self.id}.pdf",
                    'source': 'attachment_id',
                    'id': att.id,
                }

        return {'has_attachment': False, 'data': None, 'name': None, 'source': None}
