# -*- coding: utf-8 -*-
from odoo import models, fields, api

class JandalahAnnouncement(models.Model):
    _name = 'jandalah.announcement'
    _description = 'تعاميم وقرارات إدارية'
    _order = 'date desc, id desc'

    name = fields.Char(string='عنوان التعميم / الخطاب', required=True)
    announcement_type = fields.Selection([
        ('circular', 'تعميم إداري عام'),
        ('letter', 'خطاب رسمي'),
        ('decision', 'قرار إداري'),
        ('policy', 'لائحة وسياسة داخلية'),
    ], string='نوع الإشعار', default='circular', required=True)
    
    date = fields.Date(string='تاريخ الإصدار', default=fields.Date.today, required=True)
    content = fields.Html(string='نص التعميم / القرار', required=True)
    
    target_type = fields.Selection([
        ('all', 'كافة الموظفين'),
        ('department', 'قسم محدد'),
        ('employees', 'موظفون محددون'),
    ], string='الفئة المستهدفة', default='all', required=True)

    department_id = fields.Many2one('hr.department', string='القسم المستهدف')
    employee_ids = fields.Many2many('hr.employee', 'jandalah_announcement_emp_rel', 'announcement_id', 'employee_id', string='الموظفون المخصصون')
    
    attachment_id = fields.Many2one('ir.attachment', string='المرفق الرسمي / مستند PDF')
    active = fields.Boolean(string='نشط', default=True)

    ack_ids = fields.One2many('jandalah.announcement.ack', 'announcement_id', string='إقرارات الاطلاع')
    ack_count = fields.Integer(string='عدد المطلعين', compute='_compute_ack_count')

    def _compute_ack_count(self):
        for rec in self:
            rec.ack_count = len(rec.ack_ids)

    def is_acknowledged_by(self, employee):
        """Checks whether the given employee has acknowledged reading this announcement."""
        self.ensure_one()
        if not employee:
            return False
        return bool(self.env['jandalah.announcement.ack'].sudo().search_count([
            ('announcement_id', '=', self.id),
            ('employee_id', '=', employee.id)
        ]))

    def is_visible_for(self, employee):
        """Checks if this announcement is targeted to the given employee."""
        self.ensure_one()
        if not employee or not self.active:
            return False
        if self.target_type == 'all':
            return True
        elif self.target_type == 'department' and self.department_id:
            return employee.department_id.id == self.department_id.id
        elif self.target_type == 'employees':
            return employee.id in self.employee_ids.ids
        return False


class JandalahAnnouncementAck(models.Model):
    _name = 'jandalah.announcement.ack'
    _description = 'إقرار قراءة التعميم'
    _order = 'ack_date desc'

    announcement_id = fields.Many2one('jandalah.announcement', string='التعميم', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='الموظف', required=True, ondelete='cascade')
    ack_date = fields.Datetime(string='تاريخ وساعة الإقرار', default=fields.Datetime.now, required=True)

    _sql_constraints = [
        ('emp_announcement_uniq', 'unique(announcement_id, employee_id)', 'لقد قمت بتأكيد الاطلاع على هذا التعميم مسبقاً.')
    ]
