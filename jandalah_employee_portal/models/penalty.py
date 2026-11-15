# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

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
    attachment_id = fields.Many2one('ir.attachment', string='المستند الرسمي (أرشيف قديم)')
    attachment_file = fields.Binary(string='المستند الرسمي (PDF / صورة)', attachment=True)
    attachment_name = fields.Char(string='اسم الملف المرفق')

    def get_official_attachment_info(self):
        """Safely retrieves attachment metadata and data for penalty."""
        self.ensure_one()
        # 1. Direct binary field on model (only if content exists)
        if self.attachment_file:
            name = self.attachment_name or f"penalty_{self.id}.pdf"
            if '.' not in name:
                name = f"{name}.pdf"
            return {
                'has_attachment': True,
                'data': self.attachment_file,
                'name': name,
                'source': 'binary',
                'id': None,
            }

        # 2. Many2one attachment_id
        if self.attachment_id:
            try:
                att = self.attachment_id.sudo()
                name = att.name or f"penalty_{self.id}.pdf"
                if name != 'loader.js' and not name.lower().endswith('.js'):
                    if '.' not in name:
                        name = f"{name}.pdf"
                    datas = att.datas
                    if not datas and getattr(att, 'raw', False):
                        datas = base64.b64encode(att.raw).decode('utf-8')
                    return {
                        'has_attachment': True,
                        'data': datas,
                        'name': name,
                        'source': 'attachment_id',
                        'id': att.id,
                    }
            except Exception as e:
                _logger.warning("Error reading penalty attachment_id: %s", e)

        # 3. Linked ir.attachment
        try:
            Att = self.env['ir.attachment'].sudo()
            att = Att.search([
                ('res_model', '=', 'jandalah.hr.penalty'),
                ('res_id', '=', self.id),
                ('name', '!=', 'loader.js'),
                ('name', 'not like', '%.js'),
            ], order='id desc', limit=1)
            if att:
                name = att.name or f"penalty_{self.id}.pdf"
                if '.' not in name:
                    name = f"{name}.pdf"
                datas = att.datas
                if not datas and getattr(att, 'raw', False):
                    datas = base64.b64encode(att.raw).decode('utf-8')
                return {
                    'has_attachment': True,
                    'data': datas,
                    'name': name,
                    'source': 'ir_attachment',
                    'id': att.id,
                }
        except Exception as e:
            _logger.warning("Error searching penalty linked ir.attachment: %s", e)

        return {'has_attachment': False, 'data': None, 'name': None, 'source': None, 'id': None}

    def init(self):
        super().init()
        try:
            self.env.cr.execute("""
                UPDATE ir_ui_view 
                SET active = false 
                WHERE model = 'jandalah.hr.penalty' 
                  AND type = 'form' 
                  AND inherit_id IS NOT NULL;

                UPDATE ir_ui_view 
                SET active = false 
                WHERE inherit_id IN (
                    SELECT res_id 
                    FROM ir_model_data 
                    WHERE model = 'ir.ui.view' 
                      AND module = 'jandalah_employee_portal' 
                      AND name = 'view_jandalah_penalty_form'
                );
            """)
        except Exception as e:
            _logger.warning("Error cleaning custom penalty inherited views: %s", e)
